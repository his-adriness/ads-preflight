"""Tests for the lint command, run through the command line.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ads_preflight import cli
from ads_preflight.cli import app
from ads_preflight.client import ServerError
from ads_preflight.models import ToolDef

runner = CliRunner()

GOOD = {
    "name": "list_notes",
    "description": "List the user's notes.",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True},
}
WARNING_ONLY = {**GOOD, "description": None}  # missing description is a warning
HAS_ERROR = [GOOD, GOOD]  # a duplicate name is an error


def write_tools(tmp_path, tools):
    path = tmp_path / "tools.json"
    path.write_text(json.dumps({"tools": tools}), encoding="utf-8")
    return str(path)


def lint(tmp_path, tools, *options):
    return runner.invoke(app, ["lint", "--tools-file", write_tools(tmp_path, tools), *options])


# --- Exit codes ---------------------------------------------------------------------------


def test_clean_tools_exit_0(tmp_path):
    """Lint a file with a well-formed tool.

    What:     Exit code 0 and a "no problems" summary.
    Why:      A clean server must pass a CI step.
    Expected: exit code 0 and "no problems" in the output.
    """
    result = lint(tmp_path, [GOOD])
    assert result.exit_code == 0
    assert "no problems" in result.output


def test_an_error_exits_1(tmp_path):
    """Lint a file with a duplicate tool name.

    What:     Exit code 1, and the report names the rule.
    Why:      Errors must fail a CI step.
    Expected: exit code 1 and "core.duplicate-name" in the output.
    """
    result = lint(tmp_path, HAS_ERROR)
    assert result.exit_code == 1
    assert "core.duplicate-name" in result.output


def test_warnings_alone_exit_0(tmp_path):
    """Lint a file that has only a warning.

    What:     The warning is shown, and the exit code is 0.
    Why:      Warnings are advice and should not block a build by default.
    Expected: exit code 0 and "core.missing-description" in the output.
    """
    result = lint(tmp_path, [WARNING_ONLY])
    assert result.exit_code == 0
    assert "core.missing-description" in result.output


def test_strict_turns_warnings_into_failures(tmp_path):
    """Lint the same warning-only file with --strict.

    What:     Exit code 1.
    Why:      Some teams want every finding to block.
    Expected: exit code 1.
    """
    assert lint(tmp_path, [WARNING_ONLY], "--strict").exit_code == 1


# --- Output formats --------------------------------------------------------------------------


def test_json_format_prints_only_json(tmp_path):
    """Lint with --format json.

    What:     The whole output parses as one JSON document.
    Why:      A program reading the output must not meet extra text.
    Expected: The parsed summary shows one error, and the exit code is still 1.
    """
    result = lint(tmp_path, HAS_ERROR, "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["summary"]["error"] == 1


def test_unknown_format_is_a_usage_error(tmp_path):
    """Lint with a format that does not exist.

    What:     Exit code 2.
    Why:      A typo in the format must not silently fall back to another one.
    Expected: exit code 2.
    """
    assert lint(tmp_path, [GOOD], "--format", "xml").exit_code == 2


def test_terminal_output_has_no_color_codes_when_not_a_terminal(tmp_path):
    """Lint with output captured, as in CI.

    What:     No escape codes in the output.
    Why:      Color codes would clutter CI logs and break text matching.
    Expected: No escape character in the output.
    """
    assert "\x1b" not in lint(tmp_path, HAS_ERROR).output


def test_hostile_tool_name_is_not_echoed_raw(tmp_path):
    """Lint a file where a tool name carries an escape code.

    What:     The name appears as text.
    Why:      A file saved from a hostile server must not control the terminal.
    Expected: No escape character in the output.
    """
    evil = {**GOOD, "name": "x\u001b[2J"}
    assert "\x1b" not in lint(tmp_path, [evil, evil]).output


# --- Input problems -----------------------------------------------------------------------------


def test_missing_file_exits_2(tmp_path):
    """Lint a file that does not exist.

    What:     Exit code 2 and a message that names the problem.
    Why:      A bad input is not a failed check, and CI should tell them apart.
    Expected: exit code 2 and "cannot read file" in the output.
    """
    result = runner.invoke(app, ["lint", "--tools-file", str(tmp_path / "nope.json")])
    assert result.exit_code == 2
    assert "cannot read file" in result.output


def test_invalid_json_exits_2(tmp_path):
    """Lint a file that is not JSON.

    What:     Exit code 2 and the loader's message.
    Why:      The person needs the same helpful message the loader gives.
    Expected: exit code 2 and "not valid JSON" in the output.
    """
    path = tmp_path / "tools.json"
    path.write_text("{oops", encoding="utf-8")
    result = runner.invoke(app, ["lint", "--tools-file", str(path)])
    assert result.exit_code == 2
    assert "not valid JSON" in result.output


def test_a_bad_tool_in_the_file_exits_2_with_its_position(tmp_path):
    """Lint a file whose second tool has the wrong type for a field.

    What:     Exit code 2 and a message that says which tool.
    Why:      The loader rejects it before any rule runs.
    Expected: exit code 2 and "tool #2" in the output.
    """
    result = lint(tmp_path, [GOOD, {"name": "b", "description": 5}])
    assert result.exit_code == 2
    assert "tool #2" in result.output


def test_lint_needs_a_file_or_a_server():
    """Run lint with nothing to check.

    What:     Exit code 2 that mentions --tools-file.
    Why:      The person needs to know what to give.
    Expected: exit code 2 and "--tools-file" in the output.
    """
    result = runner.invoke(app, ["lint"])
    assert result.exit_code == 2
    assert "--tools-file" in result.output


def test_lint_rejects_a_file_and_a_server_together(tmp_path):
    """Run lint with both a tools file and a server command.

    What:     Exit code 2.
    Why:      It is unclear which one to check, so guessing would hide a mistake.
    Expected: exit code 2 and "not both" in the output.
    """
    path = write_tools(tmp_path, [GOOD])
    result = runner.invoke(app, ["lint", "--tools-file", path, "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "not both" in result.output


# --- A live server ------------------------------------------------------------------------------

SERVER_FILE = Path(__file__).parent.parent / "examples" / "servers" / "notes_server.py"
OLD_SERVER = SERVER_FILE.parent / "broken_server_old_protocol.py"


def fake_server(monkeypatch, tools=None, error=None):
    """Replace the live fetch with canned tools or an error. Returns the calls made."""
    calls = []

    def fetch(server, timeout):
        calls.append((list(server), timeout))
        if error:
            raise error
        return [ToolDef.model_validate(tool) for tool in tools or []]

    monkeypatch.setattr(cli, "_fetch_tools", fetch)
    return calls


def test_lint_checks_the_tools_a_server_lists(monkeypatch):
    """Lint a server command, with the server faked.

    What:     The tools it lists are checked, and the report names the server command.
    Why:      The live path must give the same findings as the file path.
    Expected: exit code 1, "core.duplicate-name" and "python server.py" in the output.
    """
    calls = fake_server(monkeypatch, HAS_ERROR)
    result = runner.invoke(app, ["lint", "--", "python", "server.py"])
    assert result.exit_code == 1
    assert "core.duplicate-name" in result.output
    assert "python server.py" in result.output
    assert calls == [(["python", "server.py"], 30.0)]


def test_timeout_reaches_the_client(monkeypatch):
    """Lint with --timeout 5.

    What:     The value is passed on as a number of seconds.
    Why:      An option that is parsed but not used would silently do nothing.
    Expected: The fetch was called with 5.0.
    """
    calls = fake_server(monkeypatch, [GOOD])
    result = runner.invoke(app, ["lint", "--timeout", "5", "--", "python", "server.py"])
    assert result.exit_code == 0
    assert calls[0][1] == 5.0


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_timeout_must_be_a_positive_number(value):
    """Lint with a timeout that makes no sense.

    What:     Exit code 2.
    Why:      A zero or negative limit would fail every server for the wrong reason.
    Expected: exit code 2.
    """
    result = runner.invoke(app, ["lint", "--timeout", value, "--", "python", "server.py"])
    assert result.exit_code == 2


def test_a_server_that_cannot_be_used_exits_2_with_its_message(monkeypatch):
    """Lint a server that fails to start, with the client faked.

    What:     Exit code 2, the message shown as written, its stderr lines kept on separate lines.
    Why:      "The server crashed" must not look like "the tools are fine", and the
              server's own output helps the developer fix it.
    Expected: exit code 2, and the two lines of the message on two output lines.
    """
    fake_server(monkeypatch, error=ServerError("cannot start the server\n  boom"))
    result = runner.invoke(app, ["lint", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "cannot start the server\n  boom" in result.output


def test_server_text_cannot_inject_escape_codes(monkeypatch):
    """Lint a server whose error message carries an escape code.

    What:     The escape character is shown as text.
    Why:      Server output is untrusted and must not control the terminal.
    Expected: No escape character in the output.
    """
    fake_server(monkeypatch, error=ServerError("bad\x1b[2J"))
    result = runner.invoke(app, ["lint", "--", "python", "server.py"])
    assert "\x1b" not in result.output


def test_lint_of_the_example_server_end_to_end():
    """Lint the real example server in a child process.

    What:     The notes server is started, its 4 tools are checked, and the report prints.
    Why:      This is the whole path a user takes: command line, client, rules, report.
    Expected: exit code 0 and "4 tools" in the output.
    """
    result = runner.invoke(app, ["lint", "--", sys.executable, str(SERVER_FILE)])
    assert result.exit_code == 0, result.output
    assert "4 tools" in result.output


def test_lint_of_a_server_with_a_broken_tool_list_exits_2():
    """Lint the hand-written server whose tool has no inputSchema.

    What:     Exit code 2 and a message about the protocol.
    Why:      The SDK rejects this reply, so this message is the signal until the raw
              reply is read directly.
    Expected: exit code 2 and "does not follow the MCP protocol" in the output.
    """
    result = runner.invoke(app, ["lint", "--", sys.executable, str(OLD_SERVER), "no-schema"])
    assert result.exit_code == 2
    assert "does not follow the MCP protocol" in result.output
