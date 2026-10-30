"""Tests for the client wrapper, using a fake in place of the MCP SDK client.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ValidationError

from ads_preflight.client import (
    ServerError,
    ServerSession,
    ToolNotAllowedError,
    connect,
    list_server_tools,
)

# --- Fakes --------------------------------------------------------------------------------


class FakeModel:
    """Stands in for an SDK object: it dumps to a dict like a pydantic model."""

    def __init__(self, **fields):
        self.fields = fields
        for name, value in fields.items():
            setattr(self, name, value)

    def model_dump(self, by_alias=False, exclude_none=False):
        return dict(self.fields)


def fake_tool(name, **extra):
    return FakeModel(name=name, **extra)


class FakeClient:
    """An SDK client that serves canned pages and results and records what was asked."""

    def __init__(self, pages=None, results=None, delay=0.0, error=None):
        self.pages = pages or [([], None)]
        self.results = results or {}
        self.delay = delay
        self.error = error
        self.list_calls = []
        self.tool_calls = []

    async def list_tools(self, cursor=None):
        self.list_calls.append(cursor)
        if self.error:
            raise self.error
        index = 0 if cursor is None else int(cursor)
        tools, next_cursor = self.pages[index]
        return SimpleNamespace(tools=tools, next_cursor=next_cursor)

    async def call_tool(self, name, arguments):
        self.tool_calls.append((name, arguments))
        await anyio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.results[name]


def result(text="ok", is_error=False):
    block = FakeModel(type="text", text=text)
    return SimpleNamespace(content=[block], is_error=is_error)


def session(client, **options):
    return ServerSession(client, **options)


def run(coroutine_function):
    return anyio.run(coroutine_function)


# --- Listing tools --------------------------------------------------------------------------


def test_list_tools_returns_tooldefs():
    """List the tools of a one-page server.

    What:     SDK tool objects become ToolDef objects, in order.
    Why:      Everything above the client works on ToolDef, never on SDK types.
    Expected: Two ToolDef objects with the names in order.
    """

    async def main():
        client = FakeClient(pages=[([fake_tool("a"), fake_tool("b")], None)])
        return await session(client).list_tools()

    assert [tool.name for tool in run(main)] == ["a", "b"]


def test_list_tools_keeps_fields_the_sdk_dumped():
    """List a tool that has a description and a read-only hint.

    What:     The dumped fields reach the ToolDef.
    Why:      Lint rules read these fields, and declared_access depends on the hint.
    Expected: description and declared_access are set.
    """

    async def main():
        tool = fake_tool("a", description="Does a thing.", annotations={"readOnlyHint": True})
        return await session(FakeClient(pages=[([tool], None)])).list_tools()

    tools = run(main)
    assert tools[0].description == "Does a thing."
    assert tools[0].declared_access == "read"


def test_list_tools_follows_pages():
    """List the tools of a server that uses pagination.

    What:     All pages are read, and each next cursor is sent back.
    Why:      Stopping after the first page would silently hide tools from lint.
    Expected: Three tools, and the cursors sent were None, "1" and "2".
    """

    async def main():
        client = FakeClient(
            pages=[([fake_tool("a")], "1"), ([fake_tool("b")], "2"), ([fake_tool("c")], None)]
        )
        tools = await session(client).list_tools()
        return tools, client.list_calls

    tools, cursors = run(main)
    assert [tool.name for tool in tools] == ["a", "b", "c"]
    assert cursors == [None, "1", "2"]


def test_a_repeating_cursor_is_an_error():
    """List the tools of a server whose next cursor points to the same page.

    What:     The loop stops with a ServerError.
    Why:      Otherwise a broken server would make the tool run forever.
    Expected: ServerError that mentions the cursor.
    """

    async def main():
        client = FakeClient(pages=[([fake_tool("a")], "0")])
        await session(client).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "cursor" in str(error.value)


# --- Calling tools --------------------------------------------------------------------------


def test_a_tool_that_is_not_allowed_is_never_called():
    """Call a tool when no tools are allowed.

    What:     ToolNotAllowedError is raised and the server is not contacted.
    Why:      inspect and lint must be unable to change anything, whatever else goes wrong.
    Expected: ToolNotAllowedError, and the fake client saw no calls.
    """
    client = FakeClient(results={"delete_all": result()})

    async def main():
        await session(client).call_tool("delete_all", {})

    with pytest.raises(ToolNotAllowedError):
        run(main)
    assert client.tool_calls == []


def test_only_the_named_tools_are_allowed():
    """Allow one tool and try a similar name.

    What:     The allowed name works and a different one is refused.
    Why:      The check must be an exact match, not a prefix or a case-insensitive one.
    Expected: "list_notes" is called, "List_notes" and "list_notes2" raise.
    """
    client = FakeClient(results={"list_notes": result()})

    async def main():
        allowed = session(client, allowed_tools=["list_notes"])
        record = await allowed.call_tool("list_notes", {})
        refused = []
        for name in ("List_notes", "list_notes2"):
            try:
                await allowed.call_tool(name, {})
            except ToolNotAllowedError:
                refused.append(name)
        return record, refused

    record, refused = run(main)
    assert record.tool == "list_notes"
    assert refused == ["List_notes", "list_notes2"]


def test_call_returns_a_record():
    """Call an allowed tool.

    What:     The record has the tool, arguments, text content, error flag and a latency.
    Why:      Every later feature reads this record.
    Expected: tool, arguments and text match; is_error is False; latency is not negative.
    """

    async def main():
        client = FakeClient(results={"echo": result("hello")})
        return await session(client, allowed_tools=["echo"]).call_tool("echo", {"x": 1})

    record = run(main)
    assert record.tool == "echo"
    assert record.arguments == {"x": 1}
    assert record.text == "hello"
    assert record.is_error is False
    assert record.latency_ms >= 0


def test_a_failed_tool_is_a_record_not_an_exception():
    """Call a tool that the server reports as failed.

    What:     The record has is_error True and the error text.
    Why:      A failing tool is a result to check, not a crash of our own tool.
    Expected: No exception, is_error True.
    """

    async def main():
        client = FakeClient(results={"bad": result("it broke", is_error=True)})
        return await session(client, allowed_tools=["bad"]).call_tool("bad")

    record = run(main)
    assert record.is_error is True
    assert record.text == "it broke"


def test_latency_covers_the_wait_for_the_server():
    """Call a tool that takes a moment to answer.

    What:     latency_ms is at least the delay.
    Why:      The number is only useful if it measures the server's time.
    Expected: latency_ms >= 40 for a 0.05 second delay.
    """

    async def main():
        client = FakeClient(results={"slow": result()}, delay=0.05)
        return await session(client, allowed_tools=["slow"]).call_tool("slow")

    assert run(main).latency_ms >= 40


def test_a_call_that_takes_too_long_is_a_server_error():
    """Call a tool that answers after the time limit.

    What:     ServerError that says how long was waited and what was attempted.
    Why:      A hung server must not hang this tool forever.
    Expected: ServerError that mentions "no answer" and the tool name.
    """

    async def main():
        client = FakeClient(results={"slow": result()}, delay=5)
        await session(client, allowed_tools=["slow"], call_timeout=0.05).call_tool("slow")

    with pytest.raises(ServerError) as error:
        run(main)
    assert "no answer" in str(error.value)
    assert "'slow'" in str(error.value)


def test_a_protocol_error_is_a_server_error():
    """Call a tool when the server answers with a JSON-RPC error.

    What:     The SDK's MCPError becomes a ServerError with the server's message.
    Why:      Callers handle one error type and never import SDK exceptions.
    Expected: ServerError that contains the server's message.
    """

    async def main():
        client = FakeClient(error=MCPError(code=-32000, message="Connection closed"))
        await session(client, allowed_tools=["x"]).call_tool("x")

    with pytest.raises(ServerError) as error:
        run(main)
    assert "Connection closed" in str(error.value)


def test_a_closed_stream_is_a_server_error():
    """List tools after the server has gone away.

    What:     anyio's closed-stream errors become a ServerError.
    Why:      A server that crashes mid-session should give a readable message.
    Expected: ServerError that mentions the closed connection.
    """

    async def main():
        client = FakeClient(error=anyio.ClosedResourceError())
        await session(client).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "closed the connection" in str(error.value)


def test_a_reply_that_breaks_the_protocol_is_a_server_error():
    """List tools when the SDK rejects the server's reply as malformed.

    What:     The SDK's validation error becomes a ServerError that names the bad field.
    Why:      A raw pydantic error would leak SDK internals and crash the command.
    Expected: ServerError that mentions the protocol and the field path.
    """

    class Reply(BaseModel):
        tools: list[dict[str, str]]

    try:
        Reply.model_validate({"tools": [1]})
    except ValidationError as invalid:
        broken = invalid

    async def main():
        await session(FakeClient(error=broken)).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "does not follow the MCP protocol" in str(error.value)
    assert "tools.0" in str(error.value)


def test_the_servers_stderr_is_added_to_errors():
    """Fail a request while the server has written to stderr.

    What:     The error message ends with the server's stderr lines.
    Why:      The reason a server failed is usually in its own output.
    Expected: ServerError that contains the stderr text.
    """

    async def main():
        client = FakeClient(error=MCPError(code=1, message="boom"))
        await session(client, stderr_tail=lambda: "Traceback: bad config").list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "Traceback: bad config" in str(error.value)


# --- connect --------------------------------------------------------------------------------


def opener(client=None, *, enter_error=None, enter_delay=0.0, stderr_text=""):
    """Build a fake `open_client` function."""

    @asynccontextmanager
    async def open_client(command, env, errlog):
        if stderr_text:
            errlog.write(stderr_text)
            errlog.flush()
        if enter_delay:
            await anyio.sleep(enter_delay)
        if enter_error:
            raise enter_error
        yield client

    return open_client


def test_connect_gives_a_working_session():
    """Connect with a fake client and list tools.

    What:     The session from connect() lists tools through the client.
    Why:      This is the path every command takes.
    Expected: One tool named "a".
    """

    async def main():
        client = FakeClient(pages=[([fake_tool("a")], None)])
        async with connect(["server"], open_client=opener(client)) as active:
            return await active.list_tools()

    assert [tool.name for tool in run(main)] == ["a"]


def test_connect_passes_allowed_tools_to_the_session():
    """Connect with an allowed tool and call it.

    What:     allowed_tools reaches the session.
    Why:      A wiring mistake here would either block runs or allow everything.
    Expected: The allowed tool works and another one is refused.
    """

    async def main():
        client = FakeClient(results={"a": result()})
        async with connect(["s"], allowed_tools=["a"], open_client=opener(client)) as active:
            await active.call_tool("a")
            with pytest.raises(ToolNotAllowedError):
                await active.call_tool("b")

    run(main)


@pytest.mark.parametrize("command", [[], [""]])
def test_an_empty_command_is_a_server_error(command):
    """Connect with no command.

    What:     ServerError before anything is started.
    Why:      An empty command is a usage mistake and should say so.
    Expected: ServerError that mentions "no server command".
    """

    async def main():
        async with connect(command, open_client=opener()):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "no server command" in str(error.value)


def test_a_missing_command_is_reported_by_name():
    """Connect when the SDK cannot find the program.

    What:     A FileNotFoundError inside an exception group becomes a clear message.
    Why:      The SDK wraps errors in groups, and a typo in the command is the most common mistake.
    Expected: ServerError that mentions "command not found" and the program name.
    """
    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [FileNotFoundError("no such file")])

    async def main():
        async with connect(["no-such-program", "--x"], open_client=opener(enter_error=wrapped)):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "command not found" in str(error.value)
    assert "no-such-program" in str(error.value)


def test_a_failed_handshake_shows_the_servers_stderr():
    """Connect to a server that writes to stderr and then drops the connection.

    What:     The error includes the handshake failure and the server's stderr.
    Why:      A server that crashes at start-up explains itself on stderr.
    Expected: ServerError that contains "Connection closed" and the stderr text.
    """
    error_in_group = ExceptionGroup("x", [MCPError(code=-32000, message="Connection closed")])
    fake = opener(enter_error=error_in_group, stderr_text="ImportError: no module named foo\n")

    async def main():
        async with connect(["server"], open_client=fake):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "Connection closed" in str(error.value)
    assert "ImportError: no module named foo" in str(error.value)


def test_a_server_that_never_answers_the_handshake_times_out():
    """Connect to a server that hangs while starting.

    What:     ServerError that says how long was waited.
    Why:      A hung start-up must not hang the tool.
    Expected: ServerError that mentions "did not finish starting".
    """

    async def main():
        async with connect(["s"], connect_timeout=0.05, open_client=opener(enter_delay=5)):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "did not finish starting" in str(error.value)


def test_the_connect_time_limit_ends_once_connected():
    """Stay connected for longer than the connect timeout.

    What:     The session still works after the connect timeout has passed.
    Why:      The limit is for starting only. A long session is not a failure.
    Expected: No error, and a tool listing after the timeout still works.
    """

    async def main():
        client = FakeClient(pages=[([fake_tool("a")], None)])
        async with connect(["s"], connect_timeout=0.05, open_client=opener(client)) as active:
            await anyio.sleep(0.15)
            return await active.list_tools()

    assert [tool.name for tool in run(main)] == ["a"]


def test_the_callers_own_error_passes_through_unchanged():
    """Raise an error inside the connect block, wrapped in a group as the SDK would.

    What:     The caller gets its own exception back, not a ServerError and not a group.
    Why:      Callers handle their own errors, and wrapping would hide them.
    Expected: ValueError with the original message.
    """

    @asynccontextmanager
    async def wrapping(command, env, errlog):
        try:
            yield FakeClient()
        except ValueError as error:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [error]) from None

    async def main():
        async with connect(["s"], open_client=wrapping):
            raise ValueError("my own mistake")

    with pytest.raises(ValueError) as error:
        run(main)
    assert str(error.value) == "my own mistake"


def test_a_server_error_raised_in_the_block_is_not_rewrapped():
    """Raise a ServerError inside the connect block.

    What:     It passes through as the same kind of error.
    Why:      A failed call inside the block is already a clear message.
    Expected: ServerError with the original text.
    """

    async def main():
        async with connect(["s"], open_client=opener(FakeClient())):
            raise ServerError("already clear")

    with pytest.raises(ServerError) as error:
        run(main)
    assert str(error.value) == "already clear"


def test_list_server_tools_uses_the_timeout_and_returns_tool_defs():
    """List tools through the one-call helper with a fake client.

    What:     The tools come back as ToolDef, and the fake was opened once.
    Why:      The helper is the single path `lint` and `inspect` share.
    Expected: One tool named "a", and one open of the client.
    """
    opened = []

    @asynccontextmanager
    async def open_client(command, env, errlog):
        opened.append(list(command))
        yield FakeClient(pages=[([fake_tool("a")], None)])

    tools = run(lambda: list_server_tools(["srv"], timeout=5, open_client=open_client))
    assert [tool.name for tool in tools] == ["a"]
    assert opened == [["srv"]]
