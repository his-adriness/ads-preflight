"""Talk to a live MCP server over stdio or HTTP, and hand back ads-preflight's own types.

This is the only module that imports the MCP SDK's client, so a change in the SDK
only has to be fixed here. Everything else works on `ToolDef` and `CallRecord`.

The SDK checks and cleans every reply, which would hide the mistakes `lint` looks for.
So the tool list is read from the raw reply, copied from the transport before the SDK
sees it, and the SDK's own view is only a fallback.

Safety: a session cannot call a tool unless its name was passed in `allowed_tools`.
`inspect` and `lint` pass nothing, so they cannot call tools at all.
"""

import logging
import math
import os
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

import anyio
import httpx2
from mcp import Client, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from mcp.types import CONNECTION_CLOSED
from pydantic import ValidationError

from ads_preflight.credentials import secret_values
from ads_preflight.env import redact
from ads_preflight.models import CallRecord, ServerInfo, ToolDef
from ads_preflight.tools_file import ToolsFileError, parse_tools
from ads_preflight.urls import display_url, host_and_port

# The SDK logs a full traceback for every line a server prints that is not an MCP message.
# With no handler, Python prints those to the terminal. We report such lines ourselves.
logging.getLogger("mcp").addHandler(logging.NullHandler())

STRAY_LINES_KEPT = 3  # How many non-MCP stdout lines are remembered.
STRAY_LINE_CHARS = 200

STDERR_LINES_SHOWN = 15
STDERR_TAIL_BYTES = 64 * 1024  # Only this much of the end of the log is ever read.
STDERR_LINE_CHARS = 300  # Longer lines are cut, so one huge line cannot flood the report.
STDERR_LIMIT_BYTES = 8 * 1024 * 1024  # A server that writes more than this is stopped.
_STDERR_POLL_SECONDS = 0.1

class RawPages(list[dict[str, Any]]):
    """Raw `result` objects of the server's tools/list replies, in the order they arrived.

    `stray` holds the first few stdout lines that were not MCP messages, such as a log line
    the server printed to stdout by mistake.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stray: list[str] = []

# Opens the SDK client for a server command and copies raw tools/list replies into the
# list it is given. Tests replace it with a fake.
OpenClient = Callable[
    [Sequence[str], dict[str, str] | None, TextIO, RawPages], AbstractAsyncContextManager
]


# Opens the SDK client for a server URL. It is given the connect time limit, the list the
# recording stream fills, a list that receives the status code of every HTTP response of 300
# or more, and the headers to send with every request. Tests replace it with a fake.
HttpOpenClient = Callable[
    [str, float, RawPages, list[int], Mapping[str, str]], AbstractAsyncContextManager
]


@dataclass(frozen=True)
class HttpTarget:
    """A server that is already running and is reached over HTTP.

    `headers` carries credentials. It is left out of repr and comparison, so a secret
    cannot end up in a log line or an assertion message by accident.
    """

    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)


class ServerError(Exception):
    """The server could not be reached or did not answer. The message is ready to show."""


class ToolNotAllowedError(Exception):
    """A tool call was refused because its name is not on the allowed list.

    This is a safety stop, not a server problem, so it is not a ServerError.
    """


class _ReplyRejected(ServerError):
    """The SDK refused a reply because it does not follow the protocol."""


class _RecordingStream:
    """A stream wrapper that records each tools/list result as it passes.

    Every message is handed on unchanged, so the SDK behaves as if the wrapper were not there.
    """

    def __init__(self, inner: Any, raw_pages: RawPages):
        self._inner = inner
        self._raw_pages = raw_pages

    def _look(self, item: Any) -> Any:
        if isinstance(item, SessionMessage):
            result = getattr(item.message, "result", None)
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                self._raw_pages.append(result)
        elif isinstance(item, Exception):
            stray = getattr(self._raw_pages, "stray", None)
            if stray is not None and len(stray) < STRAY_LINES_KEPT:
                stray.append(_stray_text(item))
        return item

    async def receive(self) -> Any:
        return self._look(await self._inner.receive())

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> "_RecordingStream":
        return self

    async def __anext__(self) -> Any:
        return self._look(await self._inner.__anext__())

    async def __aenter__(self) -> "_RecordingStream":
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._inner.__aexit__(exc_type, exc_value, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)  # such as `last_context`, which the SDK may read


def _stray_text(problem: Exception) -> str:
    """The line the SDK could not read as an MCP message, cut to a readable length."""
    text = str(problem)
    if isinstance(problem, ValidationError):
        errors = problem.errors()
        if errors and isinstance(errors[0].get("input"), str):
            text = errors[0]["input"]
    return text if len(text) <= STRAY_LINE_CHARS else text[: STRAY_LINE_CHARS - 3] + "..."


def _open_sdk_client(
    command: Sequence[str], env: dict[str, str] | None, errlog: TextIO, raw_pages: RawPages
) -> AbstractAsyncContextManager:
    params = StdioServerParameters(command=command[0], args=list(command[1:]), env=env)

    @asynccontextmanager
    async def transport() -> AsyncIterator[Any]:
        async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
            yield _RecordingStream(read_stream, raw_pages), write_stream

    return Client(transport())


class ServerSession:
    """A connected server. Create one with `connect`, not directly."""

    def __init__(
        self,
        client: Any,
        *,
        allowed_tools: Collection[str] = (),
        call_timeout: float = 60.0,
        stderr_tail: Callable[[], str] = lambda: "",
        raw_pages: RawPages | None = None,
        hide: Callable[[str], str] = lambda text: text,
        statuses: list[int] | None = None,
    ):
        self._client = client
        self._hide = hide
        self._statuses = statuses if statuses is not None else []
        self._raw_pages = raw_pages if raw_pages is not None else []
        self._allowed = frozenset(allowed_tools)
        self._call_timeout = call_timeout
        self._stderr_tail = stderr_tail

    @property
    def info(self) -> ServerInfo:
        """How the server identified itself. Parts it did not give are None."""
        implementation = getattr(self._client, "server_info", None)
        name = getattr(implementation, "name", None)
        version = getattr(implementation, "version", None)
        protocol = getattr(self._client, "protocol_version", None)
        return ServerInfo(
            name=name if isinstance(name, str) and name else None,
            version=version if isinstance(version, str) and version else None,
            protocol_version=protocol if isinstance(protocol, str) and protocol else None,
        )

    async def list_tools(self) -> list[ToolDef]:
        """Return every tool the server lists, following its pages.

        The tools come from the raw reply when there is one, so mistakes the SDK would
        clean up or reject still reach the lint rules. Only a reply that is not a tools
        list at all (for example a tool with no name) raises ServerError.
        """
        tools: list[ToolDef] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            known = len(self._raw_pages)
            page = None
            try:
                page = await self._request(self._client.list_tools, "list tools", cursor=cursor)
            except _ReplyRejected:
                if len(self._raw_pages) == known:
                    raise  # nothing was captured, so there is nothing else to read
            if len(self._raw_pages) > known:
                raw = self._raw_pages[-1]
                tools.extend(_parse_raw_tools(raw))
                next_cursor = raw.get("nextCursor")
                cursor = next_cursor if isinstance(next_cursor, str) else None
            else:
                tools.extend(
                    ToolDef.model_validate(tool.model_dump(by_alias=True, exclude_none=True))
                    for tool in page.tools
                )
                cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise ServerError("the server repeats a page cursor, so its tool list never ends")
            seen_cursors.add(cursor)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallRecord:
        """Call one tool and return the record. Only allowed names are called.

        A tool that fails is not an exception: the record has `is_error` set.
        """
        if name not in self._allowed:
            raise ToolNotAllowedError(f"'{name}' is not on the allowed list, so it was not called")
        arguments = dict(arguments or {})
        start = time.perf_counter()
        result = await self._request(self._client.call_tool, f"call '{name}'", name, arguments)
        latency_ms = (time.perf_counter() - start) * 1000
        return CallRecord(
            tool=name,
            arguments=arguments,
            result=[block.model_dump(by_alias=True, exclude_none=True) for block in result.content],
            is_error=bool(result.is_error),
            latency_ms=latency_ms,
        )

    def _note(self) -> str:
        """Extra lines for an error: text printed on stdout by mistake, or an HTTP status."""
        note = ""
        stray = getattr(self._raw_pages, "stray", None)
        if stray:
            note += (
                f"\nthe server also printed text on stdout that is not an MCP message: "
                f"{stray[0]!r}\n(a server must write logs to stderr; stdout is only for "
                "MCP messages)"
            )
        if self._statuses:
            note += f"\nthe server answered HTTP {self._statuses[-1]}"
        return note

    async def _request(
        self, method: Callable[..., Awaitable[Any]], action: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Run one SDK request with a time limit, turning failures into ServerError."""
        try:
            with anyio.fail_after(self._call_timeout):
                return await method(*args, **kwargs)
        except TimeoutError:
            message = f"no answer within {self._call_timeout:g} seconds while trying to {action}"
            message += self._note()
            raise ServerError(self._hide(_with_stderr(message, self._stderr_tail()))) from None
        except ValidationError as error:
            # The SDK checks every reply against the protocol and rejects one that does not fit.
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            message = (
                f"the server's reply does not follow the MCP protocol while trying to {action} "
                f"({where}: {first['msg']})"
            )
            raise _ReplyRejected(self._hide(message)) from error
        except MCPError as error:
            if error.error.code == CONNECTION_CLOSED:
                message = f"the server closed the connection while trying to {action}"
                message += self._note()
            else:
                message = f"the server returned an error while trying to {action}: {error}"
            raise ServerError(self._hide(_with_stderr(message, self._stderr_tail()))) from error
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream) as error:
            message = f"the server closed the connection while trying to {action}"
            message += self._note()
            raise ServerError(self._hide(_with_stderr(message, self._stderr_tail()))) from error


@asynccontextmanager
async def connect(
    command: Sequence[str],
    *,
    allowed_tools: Collection[str] = (),
    connect_timeout: float = 30.0,
    call_timeout: float = 60.0,
    env: dict[str, str] | None = None,
    stderr_limit: int = STDERR_LIMIT_BYTES,
    open_client: OpenClient = _open_sdk_client,
) -> AsyncIterator[ServerSession]:
    """Start a server and connect to it. The server is stopped when the block ends.

    `command` is the server command as a list, such as ["python", "server.py"].
    `allowed_tools` are the only tools that may be called; the default is none.
    `env` adds environment variables for the server; their values are replaced by *** in
    every error message and stderr excerpt. Raises ServerError if the server
    cannot be started or does not finish the handshake within `connect_timeout` seconds.
    A server that writes more than `stderr_limit` bytes to stderr is stopped, and the
    block ends with a ServerError that shows the last lines it wrote.
    `open_client` exists so tests can swap in a fake.
    """
    if not command or not command[0]:
        raise ServerError("no server command was given")
    connected = False
    flood: list[str] = []  # Filled with the last stderr lines if the server writes too much.
    raw_pages = RawPages()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        log_path = Path(folder) / "server-stderr.txt"
        # Append mode, so the server keeps writing at the end if the file is emptied.
        with open(log_path, "a", encoding="utf-8", errors="replace") as errlog:

            def hide(text: str) -> str:
                return redact(text, (env or {}).values())

            def stderr_tail() -> str:
                return hide(_read_tail(log_path))

            try:
                async with anyio.create_task_group() as watchers:
                    deadline = anyio.current_time() + connect_timeout
                    with anyio.CancelScope(deadline=deadline) as guard:
                        watchers.start_soon(_watch_stderr, log_path, stderr_limit, guard, flood)
                        async with open_client(command, env, errlog, raw_pages) as client:
                            guard.deadline = math.inf  # The time limit is for connecting only.
                            connected = True
                            yield ServerSession(
                                client,
                                allowed_tools=allowed_tools,
                                call_timeout=call_timeout,
                                stderr_tail=stderr_tail,
                                raw_pages=raw_pages,
                                hide=hide,
                            )
                    watchers.cancel_scope.cancel()  # The server is stopped: stop watching.
            except Exception as error:
                leaves = _leaf_errors(error)
                if connected:
                    # Not a connection problem: pass on what the caller's own code raised,
                    # without the exception group the SDK wraps around it.
                    if len(leaves) == 1 and leaves[0] is not error:
                        raise leaves[0] from None
                    raise
                problem = _startup_error(command, leaves, stderr_tail())
                raise ServerError(hide(str(problem))) from error
            if flood:
                message = f"the server wrote more than {_format_size(stderr_limit)} to stderr"
                raise ServerError(hide(_with_stderr(message, flood[0])))
            if guard.cancelled_caught and not connected:
                message = f"the server did not finish starting within {connect_timeout:g} seconds"
                raise ServerError(hide(_with_stderr(message, stderr_tail())))


async def _watch_stderr(
    log_path: Path, limit: int, guard: anyio.CancelScope, flood: list[str]
) -> None:
    """Stop the run if the server's stderr log grows past `limit` bytes.

    The server writes to the file directly, so its size cannot be capped. This notes the
    last lines, cancels the run, and then keeps emptying the file while the server is
    being stopped, so a fast writer cannot fill the disk in the meantime.
    """
    while True:
        await anyio.sleep(_STDERR_POLL_SECONDS)
        try:
            size = log_path.stat().st_size
        except OSError:
            continue
        if flood:
            _empty(log_path)
        elif size > limit:
            flood.append(_read_tail(log_path))
            guard.cancel()


def _empty(log_path: Path) -> None:
    try:
        os.truncate(log_path, 0)
    except OSError:
        pass  # Some systems do not allow emptying a file another process has open.


def _format_size(size: int) -> str:
    megabytes = size / (1024 * 1024)
    return f"{megabytes:g} MB" if megabytes >= 1 else f"{size} bytes"


def _open_http_client(
    url: str,
    connect_timeout: float,
    raw_pages: RawPages,
    statuses: list[int],
    headers: Mapping[str, str],
) -> AbstractAsyncContextManager:
    async def record(response: httpx2.Response) -> None:
        if response.status_code >= 300:
            statuses.append(response.status_code)

    @asynccontextmanager
    async def transport() -> AsyncIterator[Any]:
        timeout = httpx2.Timeout(connect_timeout, read=300.0)  # A reply may arrive as a stream.
        # Redirects are not followed: the credentials must go only to the URL that was given.
        async with httpx2.AsyncClient(
            timeout=timeout,
            headers=dict(headers),
            follow_redirects=False,
            event_hooks={"response": [record]},
        ) as http:
            async with streamable_http_client(url, http_client=http) as (read_stream, write_stream):
                yield _RecordingStream(read_stream, raw_pages), write_stream

    return Client(transport())


@asynccontextmanager
async def connect_http(
    url: str,
    *,
    connect_timeout: float = 30.0,
    call_timeout: float = 60.0,
    headers: Mapping[str, str] | None = None,
    open_client: HttpOpenClient = _open_http_client,
) -> AsyncIterator[ServerSession]:
    """Connect to a server that is already running at `url`. No tool can be called.

    The caller checks the URL first (see `urls.check_url`). `headers` are sent with every
    request, and their values are hidden in every error message. Raises ServerError if the
    server cannot be reached, answers with an HTTP error, or does not finish the
    handshake within `connect_timeout` seconds.
    """
    sent = dict(headers or {})
    secrets = secret_values(sent)

    def hide(text: str) -> str:
        return redact(text, secrets)

    connected = False
    raw_pages = RawPages()
    statuses: list[int] = []
    try:
        with anyio.CancelScope(deadline=anyio.current_time() + connect_timeout) as guard:
            async with open_client(url, connect_timeout, raw_pages, statuses, sent) as client:
                guard.deadline = math.inf  # The time limit is for connecting only.
                connected = True
                yield ServerSession(
                    client,
                    call_timeout=call_timeout,
                    raw_pages=raw_pages,
                    hide=hide,
                    statuses=statuses,
                )
    except Exception as error:
        leaves = _leaf_errors(error)
        if connected:
            if len(leaves) == 1 and leaves[0] is not error:
                raise leaves[0] from None
            raise
        problem = _http_startup_error(url, leaves, statuses, sent_credentials=bool(sent))
        raise ServerError(hide(str(problem))) from error
    if guard.cancelled_caught and not connected:
        raise ServerError(
            f"{display_url(url)} did not finish the handshake within {connect_timeout:g} seconds"
        )


def _http_startup_error(
    url: str, leaves: list[BaseException], statuses: list[int], *, sent_credentials: bool = False
) -> ServerError:
    """Describe why an HTTP server could not be reached or did not answer the handshake."""
    shown = display_url(url)
    if statuses:
        code = statuses[0]
        if code in (401, 403) and sent_credentials:
            return ServerError(
                f"the server at {shown} refused the credentials that were sent (HTTP {code}): "
                "check that the token is valid and allowed to use this server"
            )
        if code in (401, 403):
            return ServerError(
                f"the server at {shown} refused the request (HTTP {code}): it probably needs "
                "credentials. Pass a token with --bearer-env NAME, or another header with "
                "--header-env HEADER=NAME"
            )
        if 300 <= code < 400:
            return ServerError(
                f"the server at {shown} redirected the request (HTTP {code}): use the URL it "
                "redirects to. Redirects are not followed, so that credentials are only sent "
                "to the URL you gave"
            )
        if code == 404:
            return ServerError(
                f"no MCP server found at {shown} (HTTP 404): check the URL and its path"
            )
        if code >= 500:
            return ServerError(f"the server at {shown} failed (HTTP {code}) while connecting")
        return ServerError(f"the server at {shown} answered HTTP {code} while connecting")
    for leaf in leaves:
        if isinstance(leaf, httpx2.TimeoutException):
            return ServerError(f"no answer from {shown} in time while connecting")
        if isinstance(leaf, httpx2.TransportError):
            reason = str(leaf) or type(leaf).__name__
            return ServerError(f"cannot connect to {host_and_port(url)}: {reason}")
    reason = "; ".join(str(leaf) or type(leaf).__name__ for leaf in leaves)
    return ServerError(f"the server at {shown} did not complete the MCP handshake: {reason}")


def _parse_raw_tools(raw: dict[str, Any]) -> list[ToolDef]:
    """Read tools from a raw reply as leniently as `--tools-file` does."""
    try:
        return parse_tools(raw)
    except ToolsFileError as error:
        raise ServerError(
            f"the server's reply does not follow the MCP protocol while trying to list tools "
            f"({error})"
        ) from error


def _leaf_errors(error: BaseException) -> list[BaseException]:
    """Unwrap nested exception groups into the errors that actually happened."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaf_errors(inner)]
    return [error]


def _startup_error(command: Sequence[str], leaves: list[BaseException], tail: str) -> ServerError:
    """Describe why the server could not be started or did not answer the handshake."""
    for leaf in leaves:
        if isinstance(leaf, FileNotFoundError):
            return ServerError(f"cannot start the server: command not found: {command[0]!r}")
    for leaf in leaves:
        if isinstance(leaf, PermissionError):
            return ServerError(f"cannot start the server: not allowed to run {command[0]!r}")
    for leaf in leaves:
        if isinstance(leaf, OSError):
            return ServerError(f"cannot start the server: {leaf}")
    reason = "; ".join(str(leaf) or type(leaf).__name__ for leaf in leaves)
    message = f"the server did not complete the MCP handshake: {reason}"
    return ServerError(_with_stderr(message, tail))


def _read_tail(log_path: Path) -> str:
    """The last lines the server wrote to stderr, or "" if there are none.

    Reads only the end of the file, so a huge log costs no more than a small one.
    """
    try:
        with open(log_path, "rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            log.seek(max(0, size - STDERR_TAIL_BYTES))
            data = log.read(STDERR_TAIL_BYTES)
    except OSError:
        return ""
    lines = data.decode("utf-8", errors="replace").strip().splitlines()
    if size > STDERR_TAIL_BYTES and len(lines) > 1:
        lines = lines[1:]  # The first line was probably cut in the middle.
    lines = [_shorten(line) for line in lines[-STDERR_LINES_SHOWN:]]
    return "\n".join(lines)


def _shorten(line: str) -> str:
    if len(line) <= STDERR_LINE_CHARS:
        return line
    return line[: STDERR_LINE_CHARS - 3] + "..."


def _with_stderr(message: str, tail: str) -> str:
    if not tail:
        return message
    indented = "\n".join(f"  {line}" for line in tail.splitlines())
    return f"{message}\nthe server wrote this to stderr:\n{indented}"


async def inspect_server(
    target: Sequence[str] | HttpTarget,
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    open_client: OpenClient = _open_sdk_client,
    open_http_client: HttpOpenClient = _open_http_client,
) -> tuple[ServerInfo, list[ToolDef]]:
    """Connect to a server, read how it identifies itself and its tools, and stop using it.

    `target` is a command to start (a list), or an `HttpTarget` for a server that is already
    running. No tool is ever called. `timeout` limits the handshake and each request, in
    seconds. `env` adds environment variables for a command and cannot be used with a URL.
    Raises ServerError.
    """
    if isinstance(target, HttpTarget):
        if env:
            raise ServerError("environment variables can only be passed to a server we start")
        connection = connect_http(
            target.url,
            connect_timeout=timeout,
            call_timeout=timeout,
            headers=target.headers,
            open_client=open_http_client,
        )
    else:
        connection = connect(
            target,
            connect_timeout=timeout,
            call_timeout=timeout,
            env=env,
            open_client=open_client,
        )
    async with connection as session:
        return session.info, await session.list_tools()


async def list_server_tools(
    target: Sequence[str] | HttpTarget,
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    open_client: OpenClient = _open_sdk_client,
    open_http_client: HttpOpenClient = _open_http_client,
) -> list[ToolDef]:
    """Connect to a server, list its tools and stop using it. No tool is ever called."""
    _, tools = await inspect_server(
        target,
        timeout=timeout,
        env=env,
        open_client=open_client,
        open_http_client=open_http_client,
    )
    return tools
