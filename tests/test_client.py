"""Tests for the client wrapper, using a fake in place of the MCP SDK client.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCResponse
from pydantic import BaseModel, ValidationError

from ads_preflight.client import (
    ServerError,
    ServerSession,
    ToolNotAllowedError,
    _format_size,
    _read_tail,
    _RecordingStream,
    connect,
    inspect_server,
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
    async def open_client(command, env, errlog, raw_pages):
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
    async def wrapping(command, env, errlog, raw_pages):
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
    async def open_client(command, env, errlog, raw_pages):
        opened.append(list(command))
        yield FakeClient(pages=[([fake_tool("a")], None)])

    tools = run(lambda: list_server_tools(["srv"], timeout=5, open_client=open_client))
    assert [tool.name for tool in tools] == ["a"]
    assert opened == [["srv"]]


# --- Raw replies ------------------------------------------------------------------------------


def a_validation_error():
    """A pydantic error like the one the SDK raises for a reply it refuses."""

    class Reply(BaseModel):
        tools: list[dict[str, str]]

    try:
        Reply.model_validate({"tools": [1]})
    except ValidationError as invalid:
        return invalid


class RawFakeClient:
    """An SDK client whose transport saw raw replies. It can also refuse them."""

    def __init__(self, raw_pages, sink, refuse=False):
        self.raw_pages = raw_pages
        self.sink = sink
        self.refuse = refuse
        self.cursors = []

    async def list_tools(self, cursor=None):
        self.cursors.append(cursor)
        raw = self.raw_pages[0 if cursor is None else int(cursor)]
        self.sink.append(raw)  # the recording stream copies the reply before the SDK looks at it
        if self.refuse:
            raise a_validation_error()
        tools = [fake_tool(tool["name"]) for tool in raw["tools"]]
        return SimpleNamespace(tools=tools, next_cursor=raw.get("nextCursor"))


def raw_session(raw_pages, **options):
    sink = []
    client = RawFakeClient(raw_pages, sink, **options)
    return client, ServerSession(client, raw_pages=sink)


def test_a_refused_reply_still_gives_tools_to_lint():
    """List tools when the SDK refuses the reply but the raw copy was captured.

    What:     The tools are read from the raw copy, with no error.
    Why:      The mistakes lint looks for (a missing schema) are the ones the SDK refuses.
    Expected: One tool named "t" that has no input schema.
    """

    async def main():
        _, active = raw_session([{"tools": [{"name": "t", "description": "d"}]}], refuse=True)
        return await active.list_tools()

    tools = run(main)
    assert [tool.name for tool in tools] == ["t"]
    assert tools[0].inputSchema is None


def test_the_raw_reply_wins_over_the_sdks_cleaned_view():
    """List tools when the SDK would have changed a value.

    What:     The tool keeps the exact hint the server sent.
    Why:      The SDK turns "true" into true, which would hide a wrong type from lint.
    Expected: The annotation holds the string "true", not a boolean.
    """
    raw = {"tools": [{"name": "t", "annotations": {"readOnlyHint": "true"}}]}

    async def main():
        _, active = raw_session([raw])
        return await active.list_tools()

    tool = run(main)[0]
    assert tool.annotations.readOnlyHint == "true"


def test_pages_are_followed_with_the_raw_cursor_even_when_refused():
    """List tools over two pages when the SDK refuses every page.

    What:     Both pages are read, and the second request carries the first page's cursor.
    Why:      A server with many tools must not be linted from its first page only.
    Expected: Tools "a" and "b", and cursors None and "1".
    """
    pages = [
        {"tools": [{"name": "a"}], "nextCursor": "1"},
        {"tools": [{"name": "b"}]},
    ]

    async def main():
        client, active = raw_session(pages, refuse=True)
        return client, await active.list_tools()

    client, tools = run(main)
    assert [tool.name for tool in tools] == ["a", "b"]
    assert client.cursors == [None, "1"]


def test_a_refused_reply_with_no_raw_copy_is_still_an_error():
    """List tools when the SDK refuses a reply and nothing was captured.

    What:     ServerError about the protocol.
    Why:      Without the raw data there is nothing to lint, and guessing would hide it.
    Expected: ServerError mentioning the protocol.
    """

    async def main():
        await session(FakeClient(error=a_validation_error())).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "does not follow the MCP protocol" in str(error.value)


def test_a_raw_reply_that_is_not_a_tool_list_is_a_server_error():
    """List tools when a tool in the raw reply has no name.

    What:     ServerError that names the tool by position.
    Why:      Lint rules need a name to report on, so this cannot become a finding.
    Expected: ServerError mentioning the protocol and "tool #1".
    """

    async def main():
        _, active = raw_session([{"tools": [{"description": "no name"}]}], refuse=True)
        await active.list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "does not follow the MCP protocol" in str(error.value)
    assert "tool #1" in str(error.value)


def recording_stream(messages):
    """A recording stream over an in-memory stream that already holds `messages`."""
    sink = []
    sender, receiver = anyio.create_memory_object_stream(len(messages) + 1)
    for message in messages:
        sender.send_nowait(message)
    sender.close()
    return _RecordingStream(receiver, sink), sink


def response(result, request_id=1):
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result))


def test_the_recording_stream_copies_tool_lists_and_passes_everything_on():
    """Read three messages through the recording stream: a tool list, another result, an exception.

    What:     Only the tool list is copied, and all three messages come out unchanged.
    Why:      The recording stream must not change what the SDK receives.
    Expected: One copied page, and the same three items out.
    """
    tool_list = response({"tools": [{"name": "a"}]})
    other = response({"protocolVersion": "x"}, request_id=2)
    problem = ValueError("transport trouble")

    async def main():
        stream, sink = recording_stream([tool_list, other, problem])
        items = [item async for item in stream]
        return items, sink

    items, sink = run(main)
    assert items == [tool_list, other, problem]
    assert sink == [{"tools": [{"name": "a"}]}]


def test_the_recording_stream_ignores_a_result_whose_tools_is_not_a_list():
    """Read a result where "tools" is a string.

    What:     Nothing is copied.
    Why:      Only a real tools/list reply should be treated as one.
    Expected: An empty copy list.
    """

    async def main():
        stream, sink = recording_stream([response({"tools": "nope"})])
        await stream.receive()
        return sink

    assert run(main) == []


# --- Server details -------------------------------------------------------------------------


def test_info_reads_what_the_server_called_itself():
    """Read the identity of a connected server.

    What:     Name, version and protocol version come from the SDK client.
    Why:      `inspect` shows them so you can tell which server and revision answered.
    Expected: The three values as set on the fake.
    """
    client = FakeClient()
    client.server_info = SimpleNamespace(name="Notes", version="1.0")
    client.protocol_version = "2026-07-28"
    info = session(client).info
    assert (info.name, info.version, info.protocol_version) == ("Notes", "1.0", "2026-07-28")


def test_info_has_gaps_when_the_server_gave_no_details():
    """Read the identity of a server that did not identify itself.

    What:     Every part is None, with no error.
    Why:      Newer protocol revisions make the server name optional.
    Expected: Three None values, also for an empty version string.
    """
    client = FakeClient()
    client.server_info = None
    info = session(client).info
    assert (info.name, info.version, info.protocol_version) == (None, None, None)
    client.server_info = SimpleNamespace(name="x", version="")
    assert session(client).info.version is None


def test_inspect_server_returns_identity_and_tools():
    """Inspect through the one-call helper with a fake client.

    What:     The server details and the tools come back together.
    Why:      `inspect` is built on this helper.
    Expected: The fake's name and one tool named "a".
    """

    @asynccontextmanager
    async def open_client(command, env, errlog, raw_pages):
        client = FakeClient(pages=[([fake_tool("a")], None)])
        client.server_info = SimpleNamespace(name="Fake", version="2")
        client.protocol_version = "p"
        yield client

    info, tools = run(lambda: inspect_server(["srv"], timeout=5, open_client=open_client))
    assert info.name == "Fake"
    assert [tool.name for tool in tools] == ["a"]


# --- Variables and hidden values ------------------------------------------------------------


def hide(text):
    return text.replace("abcd1234", "***")


def test_env_reaches_the_client_opener():
    """Connect with an env dictionary.

    What:     The opener receives exactly that dictionary.
    Why:      It is how the server gets its variables.
    Expected: The dictionary passed to connect().
    """
    seen = []

    @asynccontextmanager
    async def open_client(command, env, errlog, raw_pages):
        seen.append(env)
        yield FakeClient(pages=[([fake_tool("a")], None)])

    run(lambda: list_server_tools(["srv"], env={"API_TOKEN": "abcd1234"}, open_client=open_client))
    assert seen == [{"API_TOKEN": "abcd1234"}]


def test_secret_values_are_hidden_in_server_stderr():
    """Fail a request while stderr holds a passed secret.

    What:     The error shows *** where the secret was.
    Why:      A crashing server may print its own configuration into a CI log.
    Expected: ServerError with "***" and without the secret.
    """

    async def main():
        client = FakeClient(error=anyio.ClosedResourceError())
        await session(client, stderr_tail=lambda: "config token=abcd1234", hide=hide).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "***" in str(error.value)
    assert "abcd1234" not in str(error.value)


def test_secret_values_are_hidden_in_a_start_up_failure():
    """Fail the start-up while the server wrote a passed secret to stderr.

    What:     The start-up error shows *** and not the secret.
    Why:      Start-up failures are the most common case where servers print their config.
    Expected: ServerError without the secret.
    """
    options = {"stderr_text": "boot token=abcd1234\n", "enter_error": RuntimeError("no handshake")}

    async def main():
        async with connect(
            ["s"], env={"API_TOKEN": "abcd1234"}, open_client=opener(**options)
        ):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "abcd1234" not in str(error.value)
    assert "boot token=***" in str(error.value)


def test_a_server_error_message_cannot_leak_a_secret():
    """Get an error reply from the server that repeats a passed secret.

    What:     The ServerError hides it.
    Why:      Servers echo bad configuration in their error messages.
    Expected: ServerError without the secret.
    """

    async def main():
        failing = FakeClient(error=MCPError(code=-32000, message="bad token abcd1234"))
        await session(failing, hide=hide).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "abcd1234" not in str(error.value)


# --- Reading and limiting the server's stderr --------------------------------------------------


def test_read_tail_of_a_missing_file_is_empty(tmp_path):
    """Read the stderr log when the file does not exist.

    What:     An empty string, no error.
    Why:      Error reporting must never fail because of the log.
    Expected: "".
    """
    assert _read_tail(tmp_path / "nothing.txt") == ""


def test_read_tail_keeps_only_the_last_lines(tmp_path):
    """Read a log of 30 lines.

    What:     The last 15 lines, in order.
    Why:      The end of the log is where the reason for a failure is.
    Expected: line 15 to line 29.
    """
    path = tmp_path / "log.txt"
    path.write_text("".join(f"line {n}\n" for n in range(30)), encoding="utf-8")
    lines = _read_tail(path).splitlines()
    assert lines[0] == "line 15"
    assert lines[-1] == "line 29"
    assert len(lines) == 15


def test_read_tail_of_a_huge_log_reads_only_the_end(tmp_path):
    """Read a log of about 3 MB.

    What:     The last 15 lines, found without reading the whole file.
    Why:      A noisy server once made the process run out of memory by reading all of it.
    Expected: line 299985 to line 299999, and nothing from the start.
    """
    path = tmp_path / "log.txt"
    path.write_text("".join(f"line {n}\n" for n in range(300_000)), encoding="utf-8")
    text = _read_tail(path)
    assert text.splitlines()[0] == "line 299985"
    assert text.splitlines()[-1] == "line 299999"
    assert "line 1\n" not in text


def test_read_tail_cuts_very_long_lines(tmp_path):
    """Read a log whose only line is 1 MB long.

    What:     One line of at most 300 characters.
    Why:      One huge line must not flood the error message.
    Expected: A single line ending in "...", no longer than 300 characters.
    """
    path = tmp_path / "log.txt"
    path.write_text("z" * 1_000_000, encoding="utf-8")
    lines = _read_tail(path).splitlines()
    assert len(lines) == 1
    assert len(lines[0]) == 300
    assert lines[0].endswith("...")


def test_read_tail_drops_a_first_line_that_was_cut(tmp_path):
    """Read a log whose lines are longer than a tenth of the part that is read.

    What:     Every returned line starts at its real beginning.
    Why:      Reading from the middle of the file can start in the middle of a line.
    Expected: Every line starts with "L".
    """
    path = tmp_path / "log.txt"
    path.write_text("".join(f"L{n} " + "y" * 10_000 + "\n" for n in range(20)), encoding="utf-8")
    lines = _read_tail(path).splitlines()
    assert 3 <= len(lines) <= 8
    assert all(line.startswith("L") for line in lines)


def test_read_tail_survives_bytes_that_are_not_text(tmp_path):
    """Read a log that contains bytes that are not valid UTF-8.

    What:     The readable parts come back and nothing is raised.
    Why:      A server can write anything to stderr.
    Expected: "ok" is in the result.
    """
    path = tmp_path / "log.txt"
    path.write_bytes(b"\xff\xfe ok \xff\n")
    assert "ok" in _read_tail(path)


def test_sizes_are_shown_in_the_unit_that_reads_best():
    """Format the limit for a message.

    What:     Megabytes for large limits, bytes for small ones.
    Why:      "0.0009 MB" would be hard to read.
    Expected: "8 MB" and "500 bytes".
    """
    assert _format_size(8 * 1024 * 1024) == "8 MB"
    assert _format_size(500) == "500 bytes"


def noisy_opener(lines=500, wait_before_connecting=False):
    """A fake opener whose server writes a lot to stderr, then does nothing."""

    @asynccontextmanager
    async def open_client(command, env, errlog, raw_pages):
        errlog.write("noise line token=abcd1234\n" * lines)
        errlog.flush()
        if wait_before_connecting:
            await anyio.sleep(10)
        yield FakeClient()

    return open_client


def test_a_server_that_writes_too_much_to_stderr_is_stopped():
    """Use a server that writes 13 KB to stderr while the limit is 1 KB.

    What:     The block ends quickly with a ServerError that gives the limit and the end of
              the log. A secret in the log is hidden.
    Why:      A server in a loop could otherwise fill the disk before the timeout.
    Expected: ServerError with "more than 1000 bytes" and "noise line", and without the
              secret, in well under the 10 seconds the body would otherwise wait.
    """

    async def main():
        async with connect(
            ["s"], env={"API_TOKEN": "abcd1234"}, stderr_limit=1000, open_client=noisy_opener()
        ):
            await anyio.sleep(10)

    start = time.perf_counter()
    with pytest.raises(ServerError) as error:
        run(main)
    assert time.perf_counter() - start < 5
    assert "more than 1000 bytes" in str(error.value)
    assert "noise line" in str(error.value)
    assert "abcd1234" not in str(error.value)


def test_a_server_that_writes_too_much_while_starting_is_stopped():
    """Use a server that floods stderr before it finishes the handshake.

    What:     A ServerError about stderr, not a start-up time-out.
    Why:      The cause is the flood, and the message should say so.
    Expected: ServerError with "more than 1000 bytes".
    """

    async def main():
        async with connect(
            ["s"], stderr_limit=1000, open_client=noisy_opener(wait_before_connecting=True)
        ):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "more than 1000 bytes" in str(error.value)


def test_stderr_below_the_limit_is_fine():
    """Use a server that writes 500 bytes to stderr while the limit is 1 MB.

    What:     The block runs to the end with no error.
    Why:      Ordinary logging must not be treated as a flood.
    Expected: The session works.
    """

    async def main():
        async with connect(["s"], open_client=noisy_opener(lines=20)) as active:
            return await active.list_tools()

    assert run(main) == []
