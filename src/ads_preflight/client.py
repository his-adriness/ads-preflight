"""Talk to a live MCP server over stdio, and hand back ads-preflight's own types.

This is the only module that imports the MCP SDK's client, so a change in the SDK
only has to be fixed here. Everything else works on `ToolDef` and `CallRecord`.

The SDK checks and cleans every reply, which would hide the mistakes `lint` looks for.
So the tool list is read from the raw reply, copied from the transport before the SDK
sees it, and the SDK's own view is only a fallback.

Safety: a session cannot call a tool unless its name was passed in `allowed_tools`.
`inspect` and `lint` pass nothing, so they cannot call tools at all.
"""

import math
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

import anyio
from mcp import Client, StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

from ads_preflight.env import redact
from ads_preflight.models import CallRecord, ServerInfo, ToolDef
from ads_preflight.tools_file import ToolsFileError, parse_tools

STDERR_LINES_SHOWN = 15

# Raw `result` objects of the server's tools/list replies, in the order they arrived.
RawPages = list[dict[str, Any]]

# Opens the SDK client for a server command and copies raw tools/list replies into the
# list it is given. Tests replace it with a fake.
OpenClient = Callable[
    [Sequence[str], dict[str, str] | None, TextIO, RawPages], AbstractAsyncContextManager
]


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
    ):
        self._client = client
        self._hide = hide
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

    async def _request(
        self, method: Callable[..., Awaitable[Any]], action: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Run one SDK request with a time limit, turning failures into ServerError."""
        try:
            with anyio.fail_after(self._call_timeout):
                return await method(*args, **kwargs)
        except TimeoutError:
            message = f"no answer within {self._call_timeout:g} seconds while trying to {action}"
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
            message = f"the server returned an error while trying to {action}: {error}"
            raise ServerError(self._hide(_with_stderr(message, self._stderr_tail()))) from error
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream) as error:
            message = f"the server closed the connection while trying to {action}"
            raise ServerError(self._hide(_with_stderr(message, self._stderr_tail()))) from error


@asynccontextmanager
async def connect(
    command: Sequence[str],
    *,
    allowed_tools: Collection[str] = (),
    connect_timeout: float = 30.0,
    call_timeout: float = 60.0,
    env: dict[str, str] | None = None,
    open_client: OpenClient = _open_sdk_client,
) -> AsyncIterator[ServerSession]:
    """Start a server and connect to it. The server is stopped when the block ends.

    `command` is the server command as a list, such as ["python", "server.py"].
    `allowed_tools` are the only tools that may be called; the default is none.
    `env` adds environment variables for the server; their values are replaced by *** in
    every error message and stderr excerpt. Raises ServerError if the server
    cannot be started or does not finish the handshake within `connect_timeout` seconds.
    `open_client` exists so tests can swap in a fake.
    """
    if not command or not command[0]:
        raise ServerError("no server command was given")
    connected = False
    raw_pages: RawPages = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        log_path = Path(folder) / "server-stderr.txt"
        with open(log_path, "w", encoding="utf-8", errors="replace") as errlog:

            def hide(text: str) -> str:
                return redact(text, (env or {}).values())

            def stderr_tail() -> str:
                return hide(_read_tail(log_path))

            try:
                deadline = anyio.current_time() + connect_timeout
                with anyio.CancelScope(deadline=deadline) as guard:
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
            if guard.cancelled_caught and not connected:
                message = f"the server did not finish starting within {connect_timeout:g} seconds"
                raise ServerError(hide(_with_stderr(message, stderr_tail())))


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
    """The last lines the server wrote to stderr, or "" if there are none."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-STDERR_LINES_SHOWN:])


def _with_stderr(message: str, tail: str) -> str:
    if not tail:
        return message
    indented = "\n".join(f"  {line}" for line in tail.splitlines())
    return f"{message}\nthe server wrote this to stderr:\n{indented}"


async def inspect_server(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    open_client: OpenClient = _open_sdk_client,
) -> tuple[ServerInfo, list[ToolDef]]:
    """Start a server, read how it identifies itself and its tools, and stop it.

    No tool is ever called. `timeout` limits the handshake and each request, in seconds.
    `env` adds environment variables for the server. Raises ServerError.
    """
    async with connect(
        command, connect_timeout=timeout, call_timeout=timeout, env=env, open_client=open_client
    ) as session:
        return session.info, await session.list_tools()


async def list_server_tools(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    open_client: OpenClient = _open_sdk_client,
) -> list[ToolDef]:
    """Start a server, list its tools and stop it. No tool is ever called."""
    _, tools = await inspect_server(command, timeout=timeout, env=env, open_client=open_client)
    return tools
