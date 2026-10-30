"""Tests for the client wrapper against real servers started as child processes.

These start real processes, so they also check process handling on Windows and Linux.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import sys
from pathlib import Path

import anyio
import pytest

from ads_preflight.client import ServerError, ToolNotAllowedError, connect, list_server_tools

SERVER_FILE = Path(__file__).parent.parent / "examples" / "servers" / "notes_server.py"
NOTES_SERVER = [sys.executable, str(SERVER_FILE)]
RAW_SERVER = SERVER_FILE.parent / "broken_server_old_protocol.py"


def run(coroutine_function):
    return anyio.run(coroutine_function)


def test_lists_the_example_servers_tools_with_their_access():
    """Connect to the example server and list its tools.

    What:     Four tools, with the access each one declares.
    Why:      This is the whole path: start the process, handshake, list, convert to ToolDef.
    Expected: Names in order, and the declared access of each tool.
    """

    async def main():
        async with connect(NOTES_SERVER) as session:
            return await session.list_tools()

    tools = run(main)
    assert [(tool.name, tool.declared_access) for tool in tools] == [
        ("list_notes", "read"),
        ("get_note", "read"),
        ("add_note", "write"),
        ("delete_note", "write"),
    ]
    assert all(tool.inputSchema for tool in tools)


def test_a_tool_cannot_be_called_unless_allowed():
    """Try to call a tool on a real server without allowing it.

    What:     ToolNotAllowedError, and nothing changes on the server.
    Why:      This is the guarantee that inspect and lint never change anything.
    Expected: ToolNotAllowedError.
    """

    async def main():
        async with connect(NOTES_SERVER) as session:
            await session.call_tool("delete_note", {"note_id": "1"})

    with pytest.raises(ToolNotAllowedError):
        run(main)


def test_calls_an_allowed_tool():
    """Call an allowed read-only tool.

    What:     The record holds the text, no error and a latency.
    Why:      This is the path `run` will use.
    Expected: The ids of the two starting notes, is_error False, latency above zero.
    """

    async def main():
        async with connect(NOTES_SERVER, allowed_tools=["list_notes"]) as session:
            return await session.call_tool("list_notes")

    record = run(main)
    assert record.is_error is False
    assert "1" in record.text and "2" in record.text
    assert record.latency_ms > 0


def test_a_failing_tool_is_reported_as_an_error_record():
    """Call a tool that raises on the server.

    What:     is_error is True and no exception reaches the caller.
    Why:      A failing tool is a result to check.
    Expected: is_error True.
    """

    async def main():
        async with connect(NOTES_SERVER, allowed_tools=["get_note"]) as session:
            return await session.call_tool("get_note", {"note_id": "missing"})

    assert run(main).is_error is True


def test_an_unknown_tool_name_is_an_error_record():
    """Allow and call a name the server does not have.

    What:     is_error is True and no exception reaches the caller.
    Why:      The SDK reports this as a tool error, not a protocol error.
    Expected: is_error True.
    """

    async def main():
        async with connect(NOTES_SERVER, allowed_tools=["no_such_tool"]) as session:
            return await session.call_tool("no_such_tool")

    assert run(main).is_error is True


def test_missing_program_is_reported_by_name():
    """Connect with a command that does not exist.

    What:     ServerError that names the command.
    Why:      A typo in the command is the most common mistake.
    Expected: ServerError that mentions the program name.
    """

    async def main():
        async with connect(["ads-preflight-no-such-program"]):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "ads-preflight-no-such-program" in str(error.value)


def test_a_server_that_crashes_on_start_shows_its_stderr():
    """Connect to a program that prints an error and exits.

    What:     ServerError that includes the program's stderr.
    Why:      The reason a server fails to start is on its stderr.
    Expected: ServerError that contains the printed text.
    """
    crash = "import sys; print('server failed: bad config', file=sys.stderr); sys.exit(3)"

    async def main():
        async with connect([sys.executable, "-c", crash]):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "server failed: bad config" in str(error.value)


def test_a_server_that_hangs_on_start_times_out():
    """Connect to a program that never answers the handshake.

    What:     ServerError after the connect timeout, and the child process is stopped.
    Why:      A hung server must not hang this tool.
    Expected: ServerError that mentions "did not finish starting".
    """
    hang = "import time; time.sleep(60)"

    async def main():
        async with connect([sys.executable, "-c", hang], connect_timeout=1.0):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert "did not finish starting" in str(error.value)


def test_an_older_protocol_server_works():
    """Connect to a hand-written server that only knows the older handshake.

    What:     The client falls back to the old handshake and lists the tool.
    Why:      Most servers in the wild are older than the newest protocol revision.
    Expected: One tool named "ok" that declares itself read-only.
    """

    async def main():
        async with connect([sys.executable, str(RAW_SERVER), "good"]) as session:
            return await session.list_tools()

    tools = run(main)
    assert [(tool.name, tool.declared_access) for tool in tools] == [("ok", "read")]


@pytest.mark.parametrize("variant", ["wrong-key", "no-schema"])
def test_a_malformed_tool_list_is_a_server_error(variant):
    """List the tools of a server whose tool has no inputSchema.

    What:     ServerError that says the reply breaks the protocol and names the field.
    Why:      The SDK rejects the reply before ads-preflight sees it, so this is the only
              signal available for now.
    Expected: ServerError that mentions the protocol and "inputSchema".
    """

    async def main():
        async with connect([sys.executable, str(RAW_SERVER), variant]) as session:
            await session.list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "does not follow the MCP protocol" in str(error.value)
    assert "inputSchema" in str(error.value)


def test_list_server_tools_starts_lists_and_stops():
    """List the example server's tools with the one-call helper.

    What:     Four tool names come back.
    Why:      `lint` and `inspect` use this helper, so it must work on a real process.
    Expected: The four names of the example server.
    """
    tools = run(lambda: list_server_tools(NOTES_SERVER, timeout=20))
    assert [tool.name for tool in tools] == ["list_notes", "get_note", "add_note", "delete_note"]
