"""Tests for the run command, through the command line.

Most tests replace `cli._execute`, so no server is started. The last ones run the example
server.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from ads_preflight import cli
from ads_preflight.assertions import Failure
from ads_preflight.cli import app
from ads_preflight.client import HttpTarget, ServerError
from ads_preflight.models import CallRecord, ServerInfo
from ads_preflight.runner import CaseResult, RunResult, RunSetupError

runner = CliRunner()

ROOT = Path(__file__).parent.parent
SERVER = ROOT / "examples" / "servers" / "notes_server.py"
NOTES_FILE = ROOT / "examples" / "tests" / "notes.yaml"

BODY = """\
allow_tools: [read_a]
tests:
  - name: first
    call: read_a
"""


def write(tmp_path, server="", name="t.yaml"):
    path = tmp_path / name
    path.write_text("version: 1\n" + server + BODY, encoding="utf-8")
    return str(path)


def record():
    return CallRecord(tool="read_a", arguments={}, result="ok", latency_ms=3.0)


def passing():
    case = CaseResult("first", "read_a", "passed", record=record())
    return RunResult(ServerInfo(name="Fake"), (case,))


def fake_execute(monkeypatch, result=None, error=None):
    """Replace the function that runs the tests. Returns the list of calls it received."""
    calls = []

    def execute(suite, target, timeout, env):
        calls.append({"suite": suite, "target": target, "timeout": timeout, "env": env})
        if error is not None:
            raise error
        return result or passing()

    monkeypatch.setattr(cli, "_execute", execute)
    return calls


# --- Which server is tested -------------------------------------------------------------------


def test_the_server_command_is_passed_through_intact(tmp_path, monkeypatch):
    """Give a server command with its own flags after the double dash.

    What:     Everything after -- reaches the runner, in order.
    Why:      The server's flags (--port 0, -v) belong to the server, not to us.
    Expected: The target is ["python", "server.py", "--port", "0", "-v"].
    """
    calls = fake_execute(monkeypatch)
    args = ["run", write(tmp_path), "--", "python", "server.py", "--port", "0", "-v"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert calls[0]["target"] == ["python", "server.py", "--port", "0", "-v"]


def test_the_servers_own_options_are_not_mistaken_for_ours(tmp_path, monkeypatch):
    """Give a server argument that looks like our --version.

    What:     It reaches the server command and does not print our version.
    Why:      This is the reason for choosing the -- format over --command.
    Expected: exit code 0 from the run, and "--version" in the target.
    """
    calls = fake_execute(monkeypatch)
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "server.py", "--version"])
    assert result.exit_code == 0, result.output
    assert calls[0]["target"][-1] == "--version"


def test_arguments_with_spaces_stay_one_argument(tmp_path, monkeypatch):
    """Give a server argument that contains a space.

    What:     "my server.py" arrives as one argument.
    Why:      The shell has already split the line, and we must not split it again.
    Expected: The target is ["python", "my server.py"].
    """
    calls = fake_execute(monkeypatch)
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "my server.py"])
    assert result.exit_code == 0, result.output
    assert calls[0]["target"] == ["python", "my server.py"]


def test_the_file_names_its_own_server(tmp_path, monkeypatch):
    """Run a file whose server block has a command and a timeout.

    What:     The command and the timeout come from the file.
    Why:      A file with a server block should run with no other options.
    Expected: The target ["python", "s.py"] and the timeout 12.
    """
    calls = fake_execute(monkeypatch)
    path = write(tmp_path, "server: {command: [python, s.py], timeout: 12}\n")
    assert runner.invoke(app, ["run", path]).exit_code == 0
    assert calls[0]["target"] == ["python", "s.py"]
    assert calls[0]["timeout"] == 12


def test_a_server_on_the_command_line_replaces_the_files(tmp_path, monkeypatch):
    """Give a command after -- to a file that has its own server.

    What:     The command line wins.
    Why:      The same file should be usable against another server.
    Expected: The target is the command-line command.
    """
    calls = fake_execute(monkeypatch)
    path = write(tmp_path, "server: {command: [python, from_file.py]}\n")
    assert runner.invoke(app, ["run", path, "--", "python", "other.py"]).exit_code == 0
    assert calls[0]["target"] == ["python", "other.py"]


def test_a_url_on_the_command_line_replaces_a_command_in_the_file(tmp_path, monkeypatch):
    """Give --url to a file whose server is a command.

    What:     The target is the URL, with the token from --bearer-env.
    Why:      A file written for a local server must be usable against a hosted one.
    Expected: An HttpTarget with the URL and the Authorization header.
    """
    calls = fake_execute(monkeypatch)
    path = write(tmp_path, "server: {command: [python, from_file.py]}\n")
    result = runner.invoke(
        app,
        ["run", path, "--url", "https://example.com/mcp", "--bearer-env", "MY_TOKEN"],
        env={"MY_TOKEN": "tok-12345678"},
    )
    assert result.exit_code == 0, result.output
    target = calls[0]["target"]
    assert isinstance(target, HttpTarget) and target.url == "https://example.com/mcp"
    assert target.headers == {"Authorization": "Bearer tok-12345678"}
    assert "tok-12345678" not in result.output


def test_the_files_credentials_are_read_from_the_environment(tmp_path, monkeypatch):
    """Run a file whose server block names a URL, a bearer variable and a header variable.

    What:     The values are read from the environment when the run starts.
    Why:      Files hold names only, so they can be committed.
    Expected: Both headers on the target, and no value in the output.
    """
    calls = fake_execute(monkeypatch)
    path = write(
        tmp_path,
        "server: {url: 'https://example.com/mcp', bearer_env: MY_TOKEN, "
        "header_env: ['X-Api-Key=MY_KEY']}\n",
    )
    environment = {"MY_TOKEN": "tok-12345678", "MY_KEY": "key-98765"}
    result = runner.invoke(app, ["run", path], env=environment)
    assert result.exit_code == 0, result.output
    assert calls[0]["target"].headers == {
        "Authorization": "Bearer tok-12345678",
        "X-Api-Key": "key-98765",
    }
    assert "tok-12345678" not in result.output and "key-98765" not in result.output


def test_the_files_env_names_are_passed_to_a_started_server(tmp_path, monkeypatch):
    """Run a file whose server block lists an environment variable.

    What:     The variable and its value reach the runner as the server's environment.
    Why:      A server that needs a credential gets it by name.
    Expected: env == {"API_TOKEN": "s3cret-value"}.
    """
    calls = fake_execute(monkeypatch)
    path = write(tmp_path, "server: {command: [python, s.py], env: [API_TOKEN]}\n")
    result = runner.invoke(app, ["run", path], env={"API_TOKEN": "s3cret-value"})
    assert result.exit_code == 0, result.output
    assert calls[0]["env"] == {"API_TOKEN": "s3cret-value"}


def test_the_timeout_option_overrides_the_file(tmp_path, monkeypatch):
    """Give --timeout to a file with its own timeout.

    What:     The option wins.
    Why:      CI may need a different limit from a laptop.
    Expected: The timeout is 5.
    """
    calls = fake_execute(monkeypatch)
    path = write(tmp_path, "server: {command: [python, s.py], timeout: 12}\n")
    assert runner.invoke(app, ["run", path, "--timeout", "5"]).exit_code == 0
    assert calls[0]["timeout"] == 5


def test_the_default_timeout_is_30(tmp_path, monkeypatch):
    """Run with no timeout anywhere.

    What:     30 seconds.
    Why:      The same default as inspect and lint.
    Expected: The timeout is 30.
    """
    calls = fake_execute(monkeypatch)
    assert runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"]).exit_code == 0
    assert calls[0]["timeout"] == 30


def test_no_server_anywhere_is_a_usage_error(tmp_path, monkeypatch):
    """Run a file without a server block and give no server.

    What:     Exit code 2 and a message that lists the three ways to give one.
    Why:      There is nothing to test.
    Expected: exit code 2 and "no server to test".
    """
    calls = fake_execute(monkeypatch)
    result = runner.invoke(app, ["run", write(tmp_path)])
    assert result.exit_code == 2
    assert "no server to test" in result.output and "--url" in result.output
    assert calls == []


def test_credential_options_need_a_url(tmp_path, monkeypatch):
    """Give --bearer-env together with a server command.

    What:     Exit code 2.
    Why:      The same rule as for inspect and lint.
    Expected: exit code 2 and "--url" in the output.
    """
    fake_execute(monkeypatch)
    result = runner.invoke(
        app, ["run", write(tmp_path), "--bearer-env", "T", "--", "python", "s.py"]
    )
    assert result.exit_code == 2
    assert "--url" in result.output


# --- Files that cannot be used ------------------------------------------------------------------


def test_a_missing_test_file_exits_2(tmp_path, monkeypatch):
    """Run a file that does not exist.

    What:     Exit code 2 and "cannot read".
    Why:      A check that could not run is not a failed check.
    Expected: exit code 2.
    """
    calls = fake_execute(monkeypatch)
    result = runner.invoke(app, ["run", str(tmp_path / "nope.yaml"), "--", "python", "s.py"])
    assert result.exit_code == 2
    assert "cannot read" in result.output
    assert calls == []


def test_a_bad_test_file_exits_2_with_line_numbers(tmp_path, monkeypatch):
    """Run a file with a mistake.

    What:     Exit code 2 and the line of the mistake, and nothing is started.
    Why:      The user should fix the file, not wonder about the server.
    Expected: exit code 2 and "line 3" in the output.
    """
    calls = fake_execute(monkeypatch)
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\nallow_tools: [a]\noops: 1\ntests: [{name: t, call: a}]\n")
    result = runner.invoke(app, ["run", str(path), "--", "python", "s.py"])
    assert result.exit_code == 2
    assert "line 3" in result.output and "unknown field" in result.output
    assert calls == []


# --- Results and exit codes ---------------------------------------------------------------------


def test_a_run_where_everything_passes_exits_0(tmp_path, monkeypatch):
    """Run tests that all pass.

    What:     Exit code 0 and the report.
    Why:      CI treats 0 as success.
    Expected: exit code 0 and "1 passed" in the output.
    """
    fake_execute(monkeypatch)
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert result.exit_code == 0
    assert "1 passed" in result.output and "first (read_a" in result.output


def test_a_failed_test_exits_1_and_prints_why(tmp_path, monkeypatch):
    """Run tests where one fails.

    What:     Exit code 1 and the failure in the report.
    Why:      1 means the check ran and found a problem.
    Expected: exit code 1 and the failure message in the output.
    """
    failed = CaseResult(
        "first", "read_a", "failed", (Failure("text_equals", "expected x but got y"),), record()
    )
    fake_execute(monkeypatch, RunResult(ServerInfo(), (failed,)))
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert result.exit_code == 1
    assert "expected x but got y" in result.output


def test_a_run_that_stopped_exits_2_and_still_prints_the_report(tmp_path, monkeypatch):
    """Run tests where the server stops partway.

    What:     Exit code 2, with the report of what happened.
    Why:      A run that did not finish is not a pass or a fail, but the finished
              results are still useful.
    Expected: exit code 2, "not run" and the reason in the output.
    """
    cases = (
        CaseResult("first", "read_a", "passed", record=record()),
        CaseResult("second", "read_a", "not_run"),
    )
    fake_execute(monkeypatch, RunResult(ServerInfo(), cases, "stopped at the test 'second': boom"))
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert result.exit_code == 2
    assert "1 not run" in result.output and "boom" in result.output


def test_tools_that_do_not_match_the_server_exit_2(tmp_path, monkeypatch):
    """Run when the allowlist does not match the server.

    What:     Exit code 2 and the problems, one per line.
    Why:      Nothing was called, so it is not a test failure.
    Expected: exit code 2 and the message.
    """
    problem = RunSetupError("the tools do not match:\n  'read_a' says it changes data")
    fake_execute(monkeypatch, error=problem)
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert result.exit_code == 2
    assert "says it changes data" in result.output


def test_a_server_that_cannot_be_used_exits_2(tmp_path, monkeypatch):
    """Run against a server that cannot start.

    What:     Exit code 2 and its message.
    Why:      Same as inspect and lint.
    Expected: exit code 2 and the message.
    """
    fake_execute(monkeypatch, error=ServerError("cannot start the server: command not found"))
    result = runner.invoke(app, ["run", write(tmp_path), "--", "nope"])
    assert result.exit_code == 2
    assert "command not found" in result.output


def test_ctrl_c_exits_130(tmp_path, monkeypatch):
    """Interrupt a run.

    What:     Exit code 130 and "interrupted".
    Why:      The usual code for Ctrl-C, and not 1, which means a test failed.
    Expected: exit code 130.
    """
    fake_execute(monkeypatch, error=KeyboardInterrupt())
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert result.exit_code == 130
    assert "interrupted" in result.output


def test_text_from_the_server_cannot_change_the_terminal(tmp_path, monkeypatch):
    """Report a failure whose message contains an escape code.

    What:     No ESC byte reaches the output.
    Why:      The text comes from the server being tested.
    Expected: "\\x1b" absent.
    """
    failed = CaseResult(
        "first", "read_a", "failed", (Failure("text_equals", "got \x1b[31mred"),), record()
    )
    fake_execute(monkeypatch, RunResult(ServerInfo(), (failed,)))
    result = runner.invoke(app, ["run", write(tmp_path), "--", "python", "s.py"])
    assert "\x1b[31m" not in result.output


# --- Output formats ---------------------------------------------------------------------------


def test_json_format_prints_json(tmp_path, monkeypatch):
    """Run with --format json.

    What:     The output is a JSON report, with the same exit code as the terminal report.
    Why:      Programs read the result.
    Expected: exit code 0, and a document whose summary says one test passed.
    """
    fake_execute(monkeypatch)
    result = runner.invoke(
        app, ["run", write(tmp_path), "--format", "json", "--", "python", "s.py"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["passed"] == 1


def test_junit_format_prints_xml(tmp_path, monkeypatch):
    """Run with --format junit.

    What:     The output is JUnit XML with one test case.
    Why:      CI systems read it.
    Expected: exit code 0 and a parsed testcase named "first".
    """
    fake_execute(monkeypatch)
    result = runner.invoke(
        app, ["run", write(tmp_path), "--format", "junit", "--", "python", "s.py"]
    )
    assert result.exit_code == 0, result.output
    assert ET.fromstring(result.output).find("testsuite/testcase").attrib["name"] == "first"


def test_a_failed_test_still_exits_1_in_every_format(tmp_path, monkeypatch):
    """Run a failing test with each format.

    What:     Exit code 1 for terminal, json and junit.
    Why:      A CI job must fail on a failed test, whatever it does with the report.
    Expected: Exit code 1 three times.
    """
    failed = CaseResult(
        "first", "read_a", "failed", (Failure("text_equals", "expected x but got y"),), record()
    )
    fake_execute(monkeypatch, RunResult(ServerInfo(), (failed,)))
    for name in ("terminal", "json", "junit"):
        args = ["run", write(tmp_path), "--format", name, "--", "python", "s.py"]
        assert runner.invoke(app, args).exit_code == 1, name


def test_an_unknown_format_is_a_usage_error(tmp_path):
    """Give a format that does not exist.

    What:     Exit code 2.
    Why:      Failing loudly beats printing something unexpected.
    Expected: exit code 2.
    """
    args = ["run", write(tmp_path), "--format", "html", "--", "python", "s.py"]
    assert runner.invoke(app, args).exit_code == 2


def test_junit_end_to_end():
    """Run the example file with --format junit against the example server.

    What:     Valid XML with three test cases and no failures.
    Why:      The whole path, ending in the format CI reads.
    Expected: exit code 0, tests "3", failures "0".
    """
    result = runner.invoke(
        app,
        ["run", str(NOTES_FILE), "--format", "junit", "--timeout", "30", "--",
         sys.executable, str(SERVER)],
    )
    assert result.exit_code == 0, result.output
    suite = ET.fromstring(result.output).find("testsuite")
    assert suite.attrib["tests"] == "3" and suite.attrib["failures"] == "0"


# --- The example server -------------------------------------------------------------------------


def test_the_example_file_passes_end_to_end():
    """Run examples/tests/notes.yaml against the example server, through the command line.

    What:     Exit code 0 and three passed tests.
    Why:      The whole path: file, server start, allowlist, calls, report, exit code.
    Expected: exit code 0 and "3 passed".
    """
    result = runner.invoke(
        app, ["run", str(NOTES_FILE), "--timeout", "30", "--", sys.executable, str(SERVER)]
    )
    assert result.exit_code == 0, result.output
    assert "3 tests, 3 passed, 0 failed" in result.output


def test_a_wrong_expectation_exits_1_end_to_end(tmp_path):
    """Expect a text the example server does not return.

    What:     Exit code 1, and the report shows the text that came back.
    Why:      Proves a failing test fails the command.
    Expected: exit code 1 and "first note" in the output.
    """
    path = tmp_path / "wrong.yaml"
    path.write_text(
        "version: 1\nallow_tools: [get_note]\ntests:\n"
        "  - name: wrong\n    call: get_note\n    arguments: {note_id: '1'}\n"
        "    expect: {text_equals: something else}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["run", str(path), "--timeout", "30", "--", sys.executable, str(SERVER)]
    )
    assert result.exit_code == 1, result.output
    assert "first note" in result.output


def test_a_tool_that_changes_data_exits_2_end_to_end(tmp_path):
    """Allow add_note, which the example server marks as writing.

    What:     Exit code 2 and nothing is called.
    Why:      The read-only rule must hold through the command line too.
    Expected: exit code 2 and "add_note" in the output.
    """
    path = tmp_path / "write.yaml"
    path.write_text(
        "version: 1\nallow_tools: [add_note]\ntests:\n"
        "  - name: add\n    call: add_note\n    arguments: {note_id: '9', text: x}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["run", str(path), "--timeout", "30", "--", sys.executable, str(SERVER)]
    )
    assert result.exit_code == 2, result.output
    assert "add_note" in result.output
