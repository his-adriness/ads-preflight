"""Tests for the terminal report of a run.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json

from ads_preflight.assertions import Failure
from ads_preflight.models import CallRecord, ServerInfo
from ads_preflight.reporters.run_report import render_run_json, render_run_terminal
from ads_preflight.runner import CaseResult, RunResult


def record(latency=12.4):
    return CallRecord(tool="t", arguments={}, result="ok", latency_ms=latency)


def passed(name="first", tool="read_a"):
    return CaseResult(name, tool, "passed", record=record())


def failed(name="second", tool="read_b", message="the text does not contain 'x'"):
    return CaseResult(
        name, tool, "failed", (Failure("text_contains", message),), record(40.0)
    )


def not_run(name="third", tool="read_a"):
    return CaseResult(name, tool, "not_run")


def render(cases, stopped=None, info=None, color=False):
    result = RunResult(info or ServerInfo(name="Notes", version="1.2"), tuple(cases), stopped)
    return render_run_terminal(result, "tests.yaml", color=color)


def test_the_summary_comes_first_with_the_counts():
    """Render a run with one pass and one failure.

    What:     The first line names the file, the number of tests and the counts.
    Why:      It is what people read first in a CI log.
    Expected: "tests.yaml: 2 tests, 1 passed, 1 failed".
    """
    assert render([passed(), failed()]).splitlines()[0] == "tests.yaml: 2 tests, 1 passed, 1 failed"


def test_one_test_is_not_plural_and_not_run_is_counted_only_when_present():
    """Render a single passing test, then a run with a test that did not run.

    What:     "1 test", and "not run" appears in the summary only when there is one.
    Why:      Small things that make a report look careless.
    Expected: "1 test, 1 passed, 0 failed", and "1 not run" in the second.
    """
    assert render([passed()]).splitlines()[0] == "tests.yaml: 1 test, 1 passed, 0 failed"
    assert "1 not run" in render([passed(), not_run()]).splitlines()[0]
    assert "not run" not in render([passed()]).splitlines()[0]


def test_each_test_has_a_line_with_status_tool_and_time():
    """Render passed, failed and not run tests.

    What:     One line each, with the status, the test name, the tool and the call time.
    Why:      Enough to find the slow or broken test at a glance.
    Expected: The three lines in file order.
    """
    lines = render([passed(), failed(), not_run()]).splitlines()
    assert "passed   first (read_a, 12 ms)" in lines
    assert "failed   second (read_b, 40 ms)" in lines
    assert "not run  third (read_a)" in lines


def test_failures_are_listed_under_their_test():
    """Render a failed test.

    What:     The check and its message are indented on the next line.
    Why:      The reason must sit with the test it belongs to.
    Expected: A line starting with spaces and "text_contains: the text does not contain 'x'".
    """
    lines = render([failed()]).splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("failed"))
    assert lines[index + 1].strip() == "text_contains: the text does not contain 'x'"
    assert lines[index + 1].startswith(" ")


def test_a_stopped_run_says_why():
    """Render a run that stopped.

    What:     A line says the run stopped, with the reason, before the tests.
    Why:      "Not run" alone does not tell people what went wrong.
    Expected: "The run stopped at the test 'second': boom".
    """
    text = render([passed(), not_run("second")], stopped="stopped at the test 'second': boom")
    assert "The run stopped at the test 'second': boom" in text


def test_the_server_is_named():
    """Render a run with a named server and one without a name.

    What:     "Server: Notes 1.2", or a note that it gave no name.
    Why:      Tells which server was tested.
    Expected: Both lines.
    """
    assert "Server: Notes 1.2" in render([passed()])
    assert "Server: (did not give a name)" in render([passed()], info=ServerInfo())


def test_text_from_the_server_cannot_change_the_terminal():
    """Render a failure whose message and a server name contain escape codes.

    What:     The escape character appears as text, not as a control code.
    Why:      The text comes from the server being checked.
    Expected: No ESC byte anywhere in the output.
    """
    text = render(
        [failed(message="got \x1b[31mred")],
        info=ServerInfo(name="evil\x1b]0;title\x07"),
    )
    assert "\x1b" not in text


def test_colors_are_only_added_when_asked():
    """Render with and without color.

    What:     Color codes appear only with color=True.
    Why:      Plain text is right for files and CI logs.
    Expected: No ESC in the plain output; ESC in the colored output.
    """
    assert "\x1b" not in render([passed()])
    assert "\x1b" in render([passed()], color=True)


def test_an_empty_run_renders():
    """Render a result with no tests.

    What:     A summary of zero tests and no crash.
    Why:      A file always has tests, but the report must not depend on it.
    Expected: "0 tests".
    """
    assert "0 tests" in render([])


# --- JSON ----------------------------------------------------------------------------------------


def render_json(cases, stopped=None, info=None):
    result = RunResult(info or ServerInfo(name="Notes", version="1.2"), tuple(cases), stopped)
    return json.loads(render_run_json(result, "tests.yaml"))


def test_json_has_the_source_the_server_and_a_summary():
    """Render a run as JSON.

    What:     The source, the server fields and the counts, with every count present.
    Why:      Programs read these keys, so they must always be there.
    Expected: The values for a run with one of each status.
    """
    report = render_json([passed(), failed(), not_run()], stopped="boom")
    assert report["source"] == "tests.yaml"
    assert report["server"] == {"name": "Notes", "version": "1.2", "protocolVersion": None}
    assert report["summary"] == {"tests": 3, "passed": 1, "failed": 1, "notRun": 1}
    assert report["stopped"] == "boom"


def test_json_lists_each_test_with_its_status_and_failures():
    """Render passed, failed and not run tests as JSON.

    What:     One entry per test, in file order, with status, tool, latency and failures.
    Why:      CI tools build dashboards from this.
    Expected: The three entries as described, latency null for the test that did not run.
    """
    tests = render_json([passed(), failed(), not_run()])["tests"]
    assert [t["status"] for t in tests] == ["passed", "failed", "not_run"]
    assert tests[0] == {
        "name": "first",
        "tool": "read_a",
        "status": "passed",
        "latencyMs": 12.4,
        "failures": [],
    }
    assert tests[1]["failures"] == [
        {"check": "text_contains", "message": "the text does not contain 'x'"}
    ]
    assert tests[2]["latencyMs"] is None


def test_json_does_not_carry_the_servers_results():
    """Render a run whose call returned data.

    What:     The report has no field for the call's result or arguments.
    Why:      Reports go to CI logs and must not carry account data by default.
    Expected: The word "result" is absent from the keys of a test entry.
    """
    call = CallRecord(tool="t", arguments={"id": "SECRET"}, result="TOPSECRET", latency_ms=1.0)
    case = CaseResult("first", "read_a", "passed", record=call)
    text = render_run_json(RunResult(ServerInfo(), (case,)), "tests.yaml")
    assert "TOPSECRET" not in text and "SECRET" not in text


def test_json_escapes_text_from_the_server():
    """Render a failure whose message contains an escape code.

    What:     The output is valid JSON and has no raw control character.
    Why:      The text comes from the server being checked.
    Expected: json.loads works and the raw text has no ESC byte.
    """
    case = failed(message="got \x1b[31mred")
    result = RunResult(ServerInfo(name="evil\x1b"), (case,))
    text = render_run_json(result, "tests.yaml")
    assert "\x1b" not in text
    assert json.loads(text)["tests"][0]["failures"][0]["message"] == "got \x1b[31mred"
