"""Talk to a live MCP server over stdio, and hand back ads-preflight's own types.

This is the only module that imports the MCP SDK's client, so a change in the SDK
only has to be fixed here. Everything else works on `ToolDef` and `CallRecord`.

Safety: a session cannot call a tool unless its name was passed in `allowed_tools`.
`inspect` and `lint` pass nothing, so they cannot call tools at all.
"""

import math
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, TextIO

import anyio
from mcp import Client, StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from ads_preflight.models import CallRecord, ToolDef

STDERR_LINES_SHOWN = 15

# Opens the SDK client for a server command. Tests replace it with a fake.
OpenClient = Callable[[Sequence[str], dict[str, str] | None, TextIO], AbstractAsyncContextManager]


class ServerError(Exception):
    """The server could not be reached or did not answer. The message is ready to show."""


class ToolNotAllowedError(Exception):
    """A tool call was refused because its name is not on the allowed list.

    This is a safety stop, not a server problem, so it is not a ServerError.
    """


def _open_sdk_client(
    command: Sequence[str], env: dict[str, str] | None, errlog: TextIO
) -> AbstractAsyncContextManager:
    params = StdioServerParameters(command=command[0], args=list(command[1:]), env=env)
    return Client(stdio_client(params, errlog=errlog))


class ServerSession:
    """A connected server. Create one with `connect`, not directly."""

    def __init__(
        self,
        client: Any,
        *,
        allowed_tools: Collection[str] = (),
        call_timeout: float = 60.0,
        stderr_tail: Callable[[], str] = lambda: "",
    ):
        self._client = client
        self._allowed = frozenset(allowed_tools)
        self._call_timeout = call_timeout
        self._stderr_tail = stderr_tail

    async def list_tools(self) -> list[ToolDef]:
        """Return every tool the server lists, following its pages."""
        tools: list[ToolDef] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            page = await self._request(self._client.list_tools, "list tools", cursor=cursor)
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
            raise ServerError(_with_stderr(message, self._stderr_tail())) from None
        except ValidationError as error:
            # The SDK checks every reply against the protocol and rejects one that does not fit.
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            message = (
                f"the server's reply does not follow the MCP protocol while trying to {action} "
                f"({where}: {first['msg']})"
            )
            raise ServerError(message) from error
        except MCPError as error:
            message = f"the server returned an error while trying to {action}: {error}"
            raise ServerError(_with_stderr(message, self._stderr_tail())) from error
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream) as error:
            message = f"the server closed the connection while trying to {action}"
            raise ServerError(_with_stderr(message, self._stderr_tail())) from error


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
    `env` adds environment variables for the server. Raises ServerError if the server
    cannot be started or does not finish the handshake within `connect_timeout` seconds.
    `open_client` exists so tests can swap in a fake.
    """
    if not command or not command[0]:
        raise ServerError("no server command was given")
    connected = False
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        log_path = Path(folder) / "server-stderr.txt"
        with open(log_path, "w", encoding="utf-8", errors="replace") as errlog:

            def stderr_tail() -> str:
                return _read_tail(log_path)

            try:
                deadline = anyio.current_time() + connect_timeout
                with anyio.CancelScope(deadline=deadline) as guard:
                    async with open_client(command, env, errlog) as client:
                        guard.deadline = math.inf  # The time limit is for connecting only.
                        connected = True
                        yield ServerSession(
                            client,
                            allowed_tools=allowed_tools,
                            call_timeout=call_timeout,
                            stderr_tail=stderr_tail,
                        )
            except Exception as error:
                leaves = _leaf_errors(error)
                if connected:
                    # Not a connection problem: pass on what the caller's own code raised,
                    # without the exception group the SDK wraps around it.
                    if len(leaves) == 1 and leaves[0] is not error:
                        raise leaves[0] from None
                    raise
                raise _startup_error(command, leaves, stderr_tail()) from error
            if guard.cancelled_caught and not connected:
                message = f"the server did not finish starting within {connect_timeout:g} seconds"
                raise ServerError(_with_stderr(message, stderr_tail()))


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
