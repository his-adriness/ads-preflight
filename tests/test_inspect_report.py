"""Tests for the inspect reports (terminal and JSON).

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json

from ads_preflight.models import ServerInfo, ToolDef
from ads_preflight.reporters.inspect_report import render_inspect_json, render_inspect_terminal

INFO = ServerInfo(name="Notes", version="1.0", protocol_version="2026-07-28")


def tool(name, description="Does a thing.", read_only=None, **extra):
    data = {"name": name, "description": description, **extra}
    if read_only is not None:
        data["annotations"] = {"readOnlyHint": read_only}
    return ToolDef.model_validate(data)


READ = tool("list_notes", "List the ids of all notes.", True)
WRITE = tool("add_note", "Create a note.", False)
SILENT = tool("mystery")


# --- Terminal ------------------------------------------------------------------------------


def test_header_names_the_server_and_counts_tools_by_access():
    """Render a server with two read tools and one write tool.

    What:     A server line, then a count line that groups tools by declared access.
    Why:      The access counts are the quick answer to "can this server change anything?".
    Expected: "Server: Notes 1.0 (protocol 2026-07-28)" and "3 tools: 2 read, 1 write".
    """
    text = render_inspect_terminal(INFO, [READ, READ, WRITE])
    lines = text.splitlines()
    assert lines[0] == "Server: Notes 1.0 (protocol 2026-07-28)"
    assert lines[1] == "3 tools: 2 read, 1 write"


def test_one_tool_is_singular():
    """Render a server with one tool.

    What:     "1 tool", not "1 tools".
    Why:      A small thing that makes the output look finished.
    Expected: The count line is "1 tool: 1 read".
    """
    assert render_inspect_terminal(INFO, [READ]).splitlines()[1] == "1 tool: 1 read"


def test_a_server_with_no_tools_says_so():
    """Render a server that lists no tools.

    What:     "no tools" and no table.
    Why:      An empty table header looks like a bug.
    Expected: Two lines, the second "no tools".
    """
    assert render_inspect_terminal(INFO, []).splitlines() == [
        "Server: Notes 1.0 (protocol 2026-07-28)",
        "no tools",
    ]


def test_missing_server_details_are_left_out():
    """Render a server that gave no name, version or protocol.

    What:     The line says the server gave no name and shows nothing for the rest.
    Why:      Printing "None" would look like data.
    Expected: "Server: (did not give a name)".
    """
    text = render_inspect_terminal(ServerInfo(), [READ])
    assert text.splitlines()[0] == "Server: (did not give a name)"


def test_a_tool_without_a_hint_is_shown_as_unknown():
    """Render a tool with no readOnlyHint.

    What:     Its access column says "unknown" and the count line includes it.
    Why:      Silence is never shown as safe.
    Expected: "unknown" in the row of that tool and in the count line.
    """
    text = render_inspect_terminal(INFO, [SILENT])
    assert "1 unknown" in text.splitlines()[1]
    row = next(line for line in text.splitlines() if line.startswith("mystery"))
    assert "unknown" in row


def test_a_hint_with_the_wrong_type_is_also_unknown():
    """Render a tool whose readOnlyHint is the string "true".

    What:     Access is "unknown".
    Why:      A mistaken hint must not be read as a claim of safety.
    Expected: "unknown" in the tool's row.
    """
    text = render_inspect_terminal(INFO, [tool("t", read_only="true")])
    assert "unknown" in text.splitlines()[-1]


def test_columns_line_up():
    """Render tools with names of different lengths.

    What:     The access column starts at the same position on every row.
    Why:      A ragged table is hard to scan.
    Expected: The same column index for "read" or "write" on every tool row.
    """
    long = tool("a_much_longer_tool_name", "Long.", True)
    rows = render_inspect_terminal(INFO, [READ, long, WRITE]).splitlines()[3:]
    starts = {row.index("read") if "read" in row else row.index("write") for row in rows[1:]}
    assert len(starts) == 1


def test_only_the_first_line_of_a_description_is_shown():
    """Render a tool with a long, multi-line description.

    What:     Only the first non-empty line appears, cut at 60 characters with "...".
    Why:      One line per tool keeps the table readable.
    Expected: The first line, shortened, and not the second line.
    """
    description = "\n  \n" + "x" * 80 + "\nSecond line."
    text = render_inspect_terminal(INFO, [tool("t", description, True)])
    row = text.splitlines()[-1]
    assert row.endswith("x" * 57 + "...")
    assert "Second line" not in text


def test_a_missing_or_blank_description_is_marked():
    """Render tools whose description is missing or only whitespace.

    What:     "(no description)" in both rows.
    Why:      An empty cell looks like a rendering fault.
    Expected: Both rows end with "(no description)".
    """
    rows = render_inspect_terminal(INFO, [tool("a", None), tool("b", "  \n ")]).splitlines()
    assert rows[-1].endswith("(no description)")
    assert rows[-2].endswith("(no description)")


def test_server_text_cannot_inject_escape_codes():
    """Render a server whose name, tool name and description carry escape codes.

    What:     The escape character appears as text everywhere.
    Why:      The server is untrusted and must not control the terminal.
    Expected: No escape character in the output.
    """
    evil = "x\u001b[2J"
    info = ServerInfo(name=evil, version=evil, protocol_version=evil)
    text = render_inspect_terminal(info, [tool(evil, evil, True)])
    assert "\x1b" not in text


# --- JSON ----------------------------------------------------------------------------------


def test_json_has_the_server_summary_and_tools():
    """Render JSON for a server with one read tool and one write tool.

    What:     Source, server details, summary counts and each tool with its declared access.
    Why:      Scripts need the same facts as the terminal view, in a stable shape.
    Expected: The parsed document matches on every key.
    """
    data = json.loads(render_inspect_json("python server.py", INFO, [READ, WRITE]))
    assert data["source"] == "python server.py"
    assert data["server"] == {
        "name": "Notes",
        "version": "1.0",
        "protocolVersion": "2026-07-28",
    }
    assert data["summary"] == {"tools": 2, "read": 1, "write": 1, "unknown": 0}
    assert [(t["name"], t["declaredAccess"]) for t in data["tools"]] == [
        ("list_notes", "read"),
        ("add_note", "write"),
    ]


def test_json_keeps_everything_the_server_sent():
    """Render JSON for a tool with a schema and an unknown field.

    What:     The input schema and the unknown field are in the output.
    Why:      JSON is where people look for the full schema, and unknown fields explain
              mistakes such as input_schema.
    Expected: Both values are present, and missing values are left out, not null.
    """
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    odd = tool("t", "d", True, inputSchema=schema, input_schema=schema)
    entry = json.loads(render_inspect_json("s", INFO, [odd]))["tools"][0]
    assert entry["inputSchema"] == schema
    assert entry["input_schema"] == schema
    assert "outputSchema" not in entry


def test_json_marks_missing_server_details_as_null():
    """Render JSON for a server that gave no details.

    What:     The three keys exist and are null.
    Why:      A program should not have to test whether a key exists.
    Expected: All three values are None.
    """
    data = json.loads(render_inspect_json("s", ServerInfo(), []))
    assert data["server"] == {"name": None, "version": None, "protocolVersion": None}
    assert data["summary"]["tools"] == 0
