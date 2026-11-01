"""Tests for running a test file against a server.

Most tests use a fake session. The last ones start the example notes server.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest

from ads_preflight.client import ServerError
from ads_preflight.models import CallRecord, ServerInfo, ToolAnnotations, ToolDef
from ads_preflight.runner import CaseResult, RunResult, RunSetupError, run_suite
from ads_preflight.testfile import load_suite, parse_suite

ROOT = Path(__file__).parent.parent
SERVER = ROOT / "examples" / "servers" / "notes_server.py"
NOTES_FILE = ROOT / "examples" / "tests" / "notes.yaml"

SUITE = """\
version: 1
allow_tools: [read_a, read_b]
tests:
  - name: first
    call: read_a
    expect: {text_contains: hello}
  - name: second
    call: read_b
    arguments: {x: 1}
  - name: third
    call: read_a
"""


def run(coroutine_function):
    return anyio.run(coroutine_function)


def tool(name, read_only=True):
    return ToolDef(name=name, annotations=ToolAnnotations(readOnlyHint=read_only))


class FakeSession:
    """A session that answers calls from a table. A value that is an exception is raised."""

    def __init__(self, tools, answers):
        self.tools = tools
        self.answers = answers
        self.calls = []
        self.info = ServerInfo(name="Fake", version="1")

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        answer = self.answers[len(self.calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(text="hello", **overrides):
    data = {
        "tool": "t",
        "arguments": {},
        "result": [{"type": "text", "text": text}],
        "latency_ms": 5.0,
    }
    data.update(overrides)
    return CallRecord(**data)


def fake_opener(session, seen=None):
    @asynccontextmanager
    async def open_session(target, allowed, timeout, env):
        if seen is not None:
            seen.append((target, tuple(allowed), timeout, env))
        yield session

    return open_session


def run_with(session, text=SUITE, seen=None, timeout=7.0):
    suite = parse_suite(text)
    opener = fake_opener(session, seen)
    return run(
        lambda: run_suite(
            suite, ["server"], timeout=timeout, env={"A": "bcde"}, open_session=opener
        )
    )


def tools_ab():
    return [tool("read_a"), tool("read_b")]


# --- Results ----------------------------------------------------------------------------------


def test_every_test_passes():
    """Run a file whose tests all pass.

    What:     All tests are "passed" and the run is ok.
    Why:      The normal case.
    Expected: Three passed, ok True, and the server's name in the result.
    """
    session = FakeSession(tools_ab(), [ok(), ok(), ok()])
    result = run_with(session)
    assert [case.status for case in result.cases] == ["passed"] * 3
    assert result.ok and result.stopped is None
    assert result.server.name == "Fake"


def test_calls_use_the_tool_and_arguments_from_the_file():
    """Run a file with arguments.

    What:     Each call has the test's tool and arguments, in file order.
    Why:      The runner must send what the file says, and nothing else.
    Expected: read_a with {}, read_b with {"x": 1}, read_a with {}.
    """
    session = FakeSession(tools_ab(), [ok(), ok(), ok()])
    run_with(session)
    assert session.calls == [("read_a", {}), ("read_b", {"x": 1}), ("read_a", {})]


def test_a_failed_expectation_fails_that_test_only():
    """Make the first test's text wrong.

    What:     The first test fails with the reason; the others still run and pass.
    Why:      One report should show every problem.
    Expected: failed, passed, passed; the failure is on text_contains.
    """
    session = FakeSession(tools_ab(), [ok(text="bye"), ok(), ok()])
    result = run_with(session)
    assert [case.status for case in result.cases] == ["failed", "passed", "passed"]
    assert result.cases[0].failures[0].check == "text_contains"
    assert not result.ok and result.count("failed") == 1


def test_a_tool_error_is_a_normal_result():
    """Make the server report a tool error.

    What:     The test fails on is_error, and the run goes on.
    Why:      A tool that fails is an answer, not a broken server.
    Expected: failed for the first test, passed for the others, no stop.
    """
    session = FakeSession(tools_ab(), [ok(is_error=True), ok(), ok()])
    result = run_with(session)
    assert result.cases[0].status == "failed"
    assert result.cases[0].failures[0].check == "is_error"
    assert result.stopped is None


def test_the_record_of_each_call_is_kept():
    """Run a passing file.

    What:     Each result holds the CallRecord of its call.
    Why:      Reports and later features need the latency and the output.
    Expected: The first record has the fake's text and 5 ms.
    """
    result = run_with(FakeSession(tools_ab(), [ok(), ok(), ok()]))
    assert result.cases[0].record.text == "hello"
    assert result.cases[0].record.latency_ms == 5.0


# --- Setup ---------------------------------------------------------------------------------------


def test_only_the_allowed_tools_can_be_called():
    """Open a connection for a file.

    What:     The opener is given exactly the file's allowed tools, the target, the time limit
              and the environment.
    Why:      The allowlist is what makes any other call impossible.
    Expected: ("read_a", "read_b"), the target, 7.0 and the environment.
    """
    seen = []
    run_with(FakeSession(tools_ab(), [ok(), ok(), ok()]), seen=seen)
    assert seen == [(["server"], ("read_a", "read_b"), 7.0, {"A": "bcde"})]


def test_a_tool_the_server_does_not_have_stops_the_run_before_any_call():
    """Allow a tool the server does not list.

    What:     RunSetupError naming the tool, and no call is made.
    Why:      A changed server must not be tested halfway.
    Expected: "does not list the tool 'read_b'" and no calls.
    """
    session = FakeSession([tool("read_a")], [])
    with pytest.raises(RunSetupError) as error:
        run_with(session)
    assert "does not list the tool 'read_b'" in str(error.value)
    assert session.calls == []


@pytest.mark.parametrize("read_only", [False, None])
def test_a_tool_not_declared_read_only_stops_the_run_before_any_call(read_only):
    """Allow a tool that says it writes, or says nothing.

    What:     RunSetupError, and no call is made.
    Why:      Silence and mistakes are never treated as safe.
    Expected: "read_b" in the message and no calls.
    """
    unsure = ToolDef(name="read_b") if read_only is None else tool("read_b", read_only)
    session = FakeSession([tool("read_a"), unsure], [])
    with pytest.raises(RunSetupError) as error:
        run_with(session)
    assert "read_b" in str(error.value)
    assert session.calls == []


# --- A server that stops partway ---------------------------------------------------------------


def test_a_server_that_stops_partway_ends_the_run():
    """Make the second call fail with a server error.

    What:     The first test keeps its result; the second and third are "not run"; the result
              says why the run stopped.
    Why:      A dead server would fail every later test for the same reason.
    Expected: passed, not_run, not_run; stopped mentions 'second' and the error; ok False.
    """
    session = FakeSession(tools_ab(), [ok(), ServerError("the server closed the connection")])
    result = run_with(session)
    assert [case.status for case in result.cases] == ["passed", "not_run", "not_run"]
    assert "'second'" in result.stopped and "closed the connection" in result.stopped
    assert not result.ok
    assert len(session.calls) == 2


def test_a_failure_while_stopping_the_server_keeps_the_results():
    """Finish all tests, then fail while the server is being stopped.

    What:     The results are returned, with a note on why the run did not end cleanly.
    Why:      Losing finished test results because of a shutdown problem would be unfair.
    Expected: Three passed and a stopped text containing the error.
    """
    session = FakeSession(tools_ab(), [ok(), ok(), ok()])

    @asynccontextmanager
    async def open_session(target, allowed, timeout, env):
        yield session
        raise ServerError("shutdown failed")

    suite = parse_suite(SUITE)
    result = run(lambda: run_suite(suite, ["server"], open_session=open_session))
    assert result.count("passed") == 3
    assert "shutdown failed" in result.stopped


def test_a_server_that_cannot_be_used_at_all_raises():
    """Fail to connect.

    What:     ServerError is raised, not turned into a result.
    Why:      There is nothing to report on.
    Expected: ServerError with the original message.
    """

    @asynccontextmanager
    async def open_session(target, allowed, timeout, env):
        raise ServerError("cannot start the server")
        yield

    with pytest.raises(ServerError) as error:
        run(lambda: run_suite(parse_suite(SUITE), ["server"], open_session=open_session))
    assert "cannot start" in str(error.value)


def test_the_result_counts_its_parts():
    """Count results of each kind.

    What:     count() matches the statuses.
    Why:      The summary line and the exit code rely on it.
    Expected: 1 passed, 1 failed, 1 not run for a hand-built result.
    """
    cases = (
        CaseResult("a", "t", "passed"),
        CaseResult("b", "t", "failed"),
        CaseResult("c", "t", "not_run"),
    )
    result = RunResult(ServerInfo(), cases)
    assert (result.count("passed"), result.count("failed"), result.count("not_run")) == (1, 1, 1)


# --- The example server -----------------------------------------------------------------------


def test_the_example_file_passes_against_the_example_server():
    """Run examples/tests/notes.yaml against examples/servers/notes_server.py.

    What:     All three tests pass.
    Why:      The README will show this run, so it must keep working.
    Expected: ok True, with three passed.
    """
    suite = load_suite(NOTES_FILE)
    result = run(lambda: run_suite(suite, [sys.executable, str(SERVER)], timeout=30))
    assert result.ok, [case.failures for case in result.cases]
    assert result.count("passed") == 3


def test_a_wrong_expectation_fails_against_the_example_server():
    """Expect a note text the server does not return.

    What:     The test fails on text_equals and the result shows what came back.
    Why:      Proves the check can fail against a real server.
    Expected: failed, and "first note" in the message.
    """
    text = """\
version: 1
allow_tools: [get_note]
tests:
  - name: wrong text
    call: get_note
    arguments: {note_id: "1"}
    expect: {text_equals: something else}
"""
    result = run(lambda: run_suite(parse_suite(text), [sys.executable, str(SERVER)], timeout=30))
    assert result.cases[0].status == "failed"
    assert "first note" in result.cases[0].failures[0].message


def test_a_tool_that_changes_data_is_refused_against_the_example_server():
    """Allow add_note, which the example server marks as writing.

    What:     RunSetupError before any call.
    Why:      The read-only rule must hold against a real server too.
    Expected: "add_note" in the message and the notes unchanged.
    """
    text = """\
version: 1
allow_tools: [add_note]
tests:
  - name: add
    call: add_note
    arguments: {note_id: "9", text: "x"}
"""
    with pytest.raises(RunSetupError) as error:
        run(lambda: run_suite(parse_suite(text), [sys.executable, str(SERVER)], timeout=30))
    assert "add_note" in str(error.value)
