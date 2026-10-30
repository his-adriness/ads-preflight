"""Tests for the client wrapper against real servers started as child processes.

These start real processes, so they also check process handling on Windows and Linux.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import sys
import time
from pathlib import Path

import anyio
import pytest

from ads_preflight.client import (
    ServerError,
    ToolNotAllowedError,
    connect,
    inspect_server,
    list_server_tools,
)

SERVER_FILE = Path(__file__).parent.parent / "examples" / "servers" / "notes_server.py"
NOTES_SERVER = [sys.executable, str(SERVER_FILE)]
RAW_SERVER = SERVER_FILE.parent / "broken_server_old_protocol.py"
PROBE_SERVER = SERVER_FILE.parent / "env_probe_server.py"
MISBEHAVING_SERVER = SERVER_FILE.parent / "misbehaving_server.py"


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
def test_a_malformed_tool_list_is_still_read(variant):
    """List the tools of a server whose tool has no usable inputSchema.

    What:     The tool comes back with no input schema, and no error.
    Why:      The SDK refuses this reply, but lint needs to see it to report the mistake.
              Reading the raw reply is the only way.
    Expected: One tool named "t" with no input schema.
    """

    async def main():
        async with connect([sys.executable, str(RAW_SERVER), variant]) as session:
            return await session.list_tools()

    tools = run(main)
    assert [tool.name for tool in tools] == ["t"]
    assert tools[0].inputSchema is None


def test_list_server_tools_starts_lists_and_stops():
    """List the example server's tools with the one-call helper.

    What:     Four tool names come back.
    Why:      `lint` and `inspect` use this helper, so it must work on a real process.
    Expected: The four names of the example server.
    """
    tools = run(lambda: list_server_tools(NOTES_SERVER, timeout=20))
    assert [tool.name for tool in tools] == ["list_notes", "get_note", "add_note", "delete_note"]


def test_inspect_server_reads_the_identity_of_real_servers():
    """Inspect the example server and the old-protocol server.

    What:     Each reports the name it gives itself, and a protocol version is present.
    Why:      The two servers use different protocol revisions and identify themselves in
              different ways, and both must work.
    Expected: Names "Notes" and "raw", the old server's version "0" and protocol "2025-06-18".
    """
    new_info, _ = run(lambda: inspect_server(NOTES_SERVER, timeout=20))
    old_info, _ = run(lambda: inspect_server([sys.executable, str(RAW_SERVER), "good"], timeout=20))
    assert new_info.name == "Notes"
    assert new_info.protocol_version
    assert (old_info.name, old_info.version, old_info.protocol_version) == (
        "raw",
        "0",
        "2025-06-18",
    )


def test_a_named_variable_reaches_the_server():
    """List the tools of a server that reports whether a variable reached it.

    What:     With the variable passed, the tool name says it is set. Without, missing.
    Why:      Only the variables we pass should arrive, as the SDK starts the server with a
              minimal environment.
    Expected: Tool names PROBE_VAR_is_set and PROBE_VAR_is_missing.
    """
    command = [sys.executable, str(PROBE_SERVER), "PROBE_VAR"]
    with_var = run(lambda: list_server_tools(command, timeout=20, env={"PROBE_VAR": "value1234"}))
    without = run(lambda: list_server_tools(command, timeout=20))
    assert [tool.name for tool in with_var] == ["PROBE_VAR_is_set"]
    assert [tool.name for tool in without] == ["PROBE_VAR_is_missing"]


def test_a_secret_a_crashing_server_prints_is_hidden():
    """Start a server that writes the variable's value to stderr and exits.

    What:     The error message shows the stderr line with *** in place of the value.
    Why:      This is the leak the redaction exists for.
    Expected: ServerError with "PROBE_VAR=***" and without the value.
    """
    command = [sys.executable, str(PROBE_SERVER), "PROBE_VAR", "leak"]

    async def main():
        await list_server_tools(command, timeout=20, env={"PROBE_VAR": "value1234"})

    with pytest.raises(ServerError) as error:
        run(main)
    assert "PROBE_VAR=***" in str(error.value)
    assert "value1234" not in str(error.value)


@pytest.mark.parametrize("mode", ["flood-at-start", "flood-stderr"])
def test_a_server_that_floods_stderr_is_stopped_with_its_last_lines(mode):
    """Run a server that writes to stderr as fast as it can.

    What:     A ServerError that gives the limit and the end of the log, soon after the limit
              is passed. The server is stopped.
    Why:      Without a limit this server writes about a gigabyte every second, and reading
              its log once made the process run out of memory.
    Expected: ServerError with "more than 200000 bytes" and "flood line", well before the
              server's own 30-second stop.
    """
    command = [sys.executable, str(MISBEHAVING_SERVER), mode]

    async def main():
        async with connect(command, stderr_limit=200_000, connect_timeout=20, call_timeout=20) as s:
            await s.list_tools()

    start = time.perf_counter()
    with pytest.raises(ServerError) as error:
        run(main)
    assert time.perf_counter() - start < 20
    assert "more than 200000 bytes" in str(error.value)
    assert "flood line" in str(error.value)


def test_a_chatty_server_below_the_limit_works():
    """Run a server that writes 100 log lines to stderr and then works normally.

    What:     The tools are listed.
    Why:      Ordinary logging is common and must not be treated as a flood.
    Expected: One tool named "ok".
    """
    command = [sys.executable, str(MISBEHAVING_SERVER), "chatty"]
    tools = run(lambda: list_server_tools(command, timeout=20))
    assert [tool.name for tool in tools] == ["ok"]


# --- Servers that stop, print the wrong thing or ignore shutdown -------------------------------

SLOW_STOP_SECONDS = 20  # Stopping a server that ignores the stop signal takes several seconds.


def misbehaving(mode, heartbeat=None):
    command = [sys.executable, str(MISBEHAVING_SERVER), mode]
    return command + [str(heartbeat)] if heartbeat else command


def has_stopped(heartbeat: Path) -> bool:
    """True if the server's heartbeat file stops growing, which means the process is gone."""
    for _ in range(100):  # The server may not have written its first beat yet.
        size = heartbeat.stat().st_size if heartbeat.exists() else 0
        if size:
            break
        time.sleep(0.05)
    assert size, "the server never wrote a heartbeat"
    time.sleep(0.6)
    return heartbeat.stat().st_size == size


@pytest.mark.parametrize("mode", ["exit-after-handshake", "half-line"])
def test_a_server_that_stops_mid_request_is_reported_as_closing_the_connection(mode):
    """Ask for the tools of a server that exits instead of answering.

    What:     A ServerError that says the server closed the connection.
    Why:      "The server returned an error" would be wrong, and a stack trace unhelpful.
    Expected: ServerError with "closed the connection while trying to list tools".
    """

    async def main():
        async with connect(misbehaving(mode), call_timeout=10) as session:
            await session.list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "closed the connection while trying to list tools" in str(error.value)


def test_text_printed_on_stdout_is_named_when_the_server_then_hangs():
    """Ask for the tools of a server that prints a log line to stdout and never answers.

    What:     The time-out error quotes the log line and says logs belong on stderr.
    Why:      A time-out alone does not tell a developer that the stdout line is the clue.
    Expected: ServerError with "no answer within", the log line and "stderr".
    """

    async def main():
        async with connect(misbehaving("stdout-noise-hang"), call_timeout=2) as session:
            await session.list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "no answer within 2 seconds" in str(error.value)
    assert "server v1.0 listening on stdio" in str(error.value)
    assert "stderr" in str(error.value)


def test_a_server_that_never_answers_times_out_and_is_stopped(tmp_path):
    """Ask for the tools of a server that never answers and ignores the stop signal.

    What:     A time-out error, in bounded time, and the server process is gone afterwards.
    Why:      A hung server must never leave a process running after the command ends.
    Expected: ServerError with "no answer within 2 seconds", under 20 seconds in all, and a
              heartbeat that has stopped.
    """
    heartbeat = tmp_path / "heartbeat.txt"

    async def main():
        async with connect(misbehaving("hang-on-tools", heartbeat), call_timeout=2) as session:
            await session.list_tools()

    start = time.perf_counter()
    with pytest.raises(ServerError) as error:
        run(main)
    assert time.perf_counter() - start < SLOW_STOP_SECONDS
    assert "no answer within 2 seconds" in str(error.value)
    assert has_stopped(heartbeat)


def test_a_server_that_ignores_shutdown_is_still_stopped(tmp_path):
    """Use a server that works, then keeps running after its input is closed.

    What:     The tools are listed, leaving the block takes bounded time, and the process is
              gone afterwards.
    Why:      Servers that do not exit when asked are common, and must not pile up.
    Expected: One tool, under 20 seconds in all, and a heartbeat that has stopped.
    """
    heartbeat = tmp_path / "heartbeat.txt"

    async def main():
        async with connect(misbehaving("ignore-shutdown", heartbeat)) as session:
            return await session.list_tools()

    start = time.perf_counter()
    tools = run(main)
    assert time.perf_counter() - start < SLOW_STOP_SECONDS
    assert [tool.name for tool in tools] == ["ok"]
    assert has_stopped(heartbeat)
