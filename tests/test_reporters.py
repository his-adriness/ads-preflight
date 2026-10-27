"""Tests for the terminal and JSON reports.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json

import click
import pytest

from ads_preflight.findings import Finding
from ads_preflight.reporters.json_reporter import render_json
from ads_preflight.reporters.terminal import clean_text, render_terminal


def finding(severity="error", rule="core.x", tool="t", message="m", fix=None):
    return Finding(rule=rule, severity=severity, tool=tool, message=message, fix=fix)


# --- clean_text ---------------------------------------------------------------------


def test_clean_text_leaves_normal_text_alone():
    """Clean ordinary text, including non-English letters.

    What:     Printable characters pass through unchanged.
    Why:      Cleaning must not damage tool names in other languages.
    Expected: The same string.
    """
    assert clean_text("list_notes (año) 日本語") == "list_notes (año) 日本語"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a\x1b[31mred", "a\\x1b[31mred"),
        ("line1\nline2", "line1\\nline2"),
        ("tab\there", "tab\\there"),
        ("bell\x07", "bell\\x07"),
        ("flip\u202etext", "flip\\u202etext"),
    ],
    ids=["escape-code", "newline", "tab", "bell", "bidi-override"],
)
def test_clean_text_shows_control_characters_as_escapes(text, expected):
    """Clean text that could rewrite the terminal.

    What:     Escape codes, line breaks and direction overrides are shown as escapes.
    Why:      Tool names come from the server being checked, which may be hostile and
              could hide or fake report lines.
    Expected: The escaped text.
    """
    assert clean_text(text) == expected


# --- The terminal report ---------------------------------------------------------------


def test_no_findings_say_no_problems():
    """Render an empty list of findings.

    What:     A single summary line.
    Why:      A clean run should be short and unambiguous.
    Expected: "tools.json: 3 tools, no problems".
    """
    assert render_terminal([], "tools.json", 3) == "tools.json: 3 tools, no problems"


@pytest.mark.parametrize(
    "tool_count, severities, expected",
    [
        (1, ["error"], "1 tool, 1 error"),
        (2, ["error", "error", "warning"], "2 tools, 2 errors, 1 warning"),
        (0, ["warning", "info"], "0 tools, 1 warning, 1 info"),
    ],
)
def test_summary_counts_and_plurals(tool_count, severities, expected):
    """Render summary lines for different counts.

    What:     Singular and plural words, errors first, and only the severities present.
    Why:      "1 errors" looks careless, and a zero count is noise.
    Expected: The summary shown for each case.
    """
    findings = [finding(severity) for severity in severities]
    first_line = render_terminal(findings, "f.json", tool_count).splitlines()[0]
    assert first_line == f"f.json: {expected}"


def test_each_finding_is_one_aligned_line_with_its_fix_below():
    """Render two findings of different widths.

    What:     Rule and tool columns line up, and the fix goes on its own indented line.
    Why:      A report is read by scanning columns.
    Expected: The exact text below.
    """
    findings = [
        finding("error", "core.wrong-schema-key", "create_note", "has a wrong key", "rename it"),
        finding("warning", "core.x", "a", "short"),
    ]
    assert render_terminal(findings, "f.json", 2) == (
        "f.json: 2 tools, 1 error, 1 warning\n"
        "\n"
        "error    core.wrong-schema-key  create_note  has a wrong key\n"
        "         fix: rename it\n"
        "warning  core.x                 a            short"
    )


def test_a_finding_about_the_whole_server_is_labelled():
    """Render a finding that has no tool.

    What:     The tool column says "(server)".
    Why:      An empty column would look like a bug.
    Expected: The line contains "(server)".
    """
    assert "(server)" in render_terminal([finding(tool=None)], "f.json", 0)


def test_color_adds_codes_only_when_asked():
    """Render the same finding with and without color.

    What:     Color wraps the severity label in codes, and the text is otherwise equal.
    Why:      The caller strips the codes when the output is not a terminal, and plain
              output must contain none.
    Expected: Codes only with color=True, and the codes removed leave the plain text.
    """
    findings = [finding()]
    plain = render_terminal(findings, "f.json", 1)
    colored = render_terminal(findings, "f.json", 1, color=True)
    assert "\x1b[" not in plain
    assert "\x1b[" in colored
    assert click.unstyle(colored) == plain


def test_hostile_text_cannot_inject_escape_codes():
    """Render a finding whose tool name and message carry escape codes.

    What:     The report shows them as text.
    Why:      Otherwise a server could clear the screen or fake a clean result.
    Expected: No raw escape character in the output.
    """
    evil = finding(tool="x\x1b[2J", message="a\nb", rule="core.x\x1b]0;t\x07", fix="\x1b[31m")
    assert "\x1b" not in render_terminal([evil], "src\x1b[0m", 1)


def test_brackets_in_messages_are_kept():
    """Render a message with square brackets and braces.

    What:     The text appears exactly as written.
    Why:      Fix hints contain braces such as {'type': 'object'}.
    Expected: The message and fix are in the output.
    """
    text = render_terminal([finding(message="has [x]", fix="use {'type': 'object'}")], "f", 1)
    assert "has [x]" in text
    assert "use {'type': 'object'}" in text


# --- The JSON report ---------------------------------------------------------------------


def test_json_report_has_the_documented_shape():
    """Render findings as JSON and read them back.

    What:     Keys are source, tools, summary and findings, and each finding has all fields.
    Why:      CI jobs parse this output, so its shape must stay stable.
    Expected: The parsed data below.
    """
    findings = [finding("error", "core.a", "t", "msg", "do this"), finding("info", tool=None)]
    data = json.loads(render_json(findings, "tools.json", 4))
    assert list(data) == ["source", "tools", "summary", "findings"]
    assert data["source"] == "tools.json"
    assert data["tools"] == 4
    assert data["summary"] == {"error": 1, "warning": 0, "info": 1}
    assert data["findings"][0] == {
        "rule": "core.a",
        "severity": "error",
        "tool": "t",
        "message": "msg",
        "fix": "do this",
    }
    assert data["findings"][1]["tool"] is None
    assert data["findings"][1]["fix"] is None


def test_json_report_with_no_findings_is_still_complete():
    """Render an empty list as JSON.

    What:     Zero counts for every severity and an empty findings list.
    Why:      A parser should not need to handle missing keys.
    Expected: summary of zeros and findings == [].
    """
    data = json.loads(render_json([], "f.json", 0))
    assert data["summary"] == {"error": 0, "warning": 0, "info": 0}
    assert data["findings"] == []


def test_json_report_keeps_a_windows_path_intact():
    """Render with a Windows path as the source.

    What:     Backslashes survive a round trip.
    Why:      Many users run on Windows, and a broken path would mislead CI logs.
    Expected: The parsed source equals the original string.
    """
    path = "C:\\Users\\me\\tools.json"
    assert json.loads(render_json([], path, 0))["source"] == path
