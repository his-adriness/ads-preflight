"""Tests for the lint command, run through the command line.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json

import pytest
from typer.testing import CliRunner

from ads_preflight.cli import app

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


@pytest.mark.parametrize("args", [["lint", "--", "python", "server.py"]])
def test_lint_of_a_live_server_is_still_a_stub(args):
    """Run lint with a server command only.

    What:     Exit code 1 and "not implemented yet".
    Why:      Checking a live server needs the client, which is not built yet.
    Expected: exit code 1 and "not implemented yet" in the output.
    """
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented yet" in result.output
