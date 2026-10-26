"""Tests for the tools-file loader: parse_tools and load_tools.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json

import pytest

from mcpdoctor.models import ToolDef
from mcpdoctor.toolsfile import ToolsFileError, load_tools, parse_tools

TOOLS = [
    {"name": "list_notes", "description": "List notes.", "annotations": {"readOnlyHint": True}},
    {"name": "create_note", "description": "Create a note."},
]


def write_json(path, data):
    """Write data to a file as JSON and return the path."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- parse_tools: accepted shapes -----------------------------------------------


def test_bare_list_of_tools():
    """Bare list of tools.

    What:     A plain JSON list of tool objects is read as a list of ToolDef.
    Why:      The simplest file a user can write by hand.
    Expected: Two ToolDef objects with the names from the input.
    """
    tools = parse_tools(TOOLS)
    assert all(isinstance(tool, ToolDef) for tool in tools)
    assert [tool.name for tool in tools] == ["list_notes", "create_note"]


def test_object_with_a_tools_list():
    """Object with a tools list.

    What:     {"tools": [...]} is read the same way as a bare list.
    Why:      This is the shape of a real tools/list result.
    Expected: The same two tools.
    """
    assert [tool.name for tool in parse_tools({"tools": TOOLS})] == ["list_notes", "create_note"]


def test_full_json_rpc_response():
    """Full JSON-RPC response.

    What:     A whole response with the tools under "result" is unwrapped.
    Why:      People often save the raw response they captured from a server.
    Expected: The same two tools.
    """
    response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": TOOLS}}
    assert [tool.name for tool in parse_tools(response)] == ["list_notes", "create_note"]


def test_other_top_level_keys_are_ignored():
    """Other top-level keys are ignored.

    What:     Keys next to "tools", such as nextCursor, do not matter.
    Why:      Servers add pagination and metadata, and none of it is a tool.
    Expected: The tools load, with no error.
    """
    data = {"tools": TOOLS, "nextCursor": "abc", "_meta": {"x": 1}}
    assert len(parse_tools(data)) == 2


def test_file_order_is_kept():
    """File order is kept.

    What:     Tools come back in the order they were in the file.
    Why:      Reports list tools in server order, and numbers in error messages must
              match the position in the file.
    Expected: Names in the same order as the input.
    """
    names = ["zeta", "alpha", "mid"]
    assert [t.name for t in parse_tools([{"name": n} for n in names])] == names


@pytest.mark.parametrize("data", [[], {"tools": []}], ids=["list", "object"])
def test_empty_list_is_valid(data):
    """Empty list is valid.

    What:     A file with no tools loads as an empty list.
    Why:      A server with no tools is a finding for the linter, not a broken file.
    Expected: [].
    """
    assert parse_tools(data) == []


def test_duplicate_names_are_kept():
    """Duplicate names are kept.

    What:     Two tools with the same name both load.
    Why:      Duplicate names are a problem in the server, and a lint rule should
              report them. Dropping one here would hide it.
    Expected: Two tools, both named "dup".
    """
    assert [t.name for t in parse_tools([{"name": "dup"}, {"name": "dup"}])] == ["dup", "dup"]


def test_unknown_fields_and_odd_hints_survive_loading():
    """Unknown fields and odd hints survive loading.

    What:     A vendor key and a hint that is the string "true" load without error.
    Why:      The linter needs to see these in order to report them.
    Expected: The vendor key is in model_extra, and the hint is still the string.
    """
    tool = parse_tools([{"name": "x", "x-vendor": 1, "annotations": {"readOnlyHint": "true"}}])[0]
    assert tool.model_extra == {"x-vendor": 1}
    assert tool.annotations.readOnlyHint == "true"


# --- parse_tools: errors --------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "found"),
    [("text", "a string"), (5, "a number"), (None, "null"), (True, "a boolean")],
    ids=["string", "number", "null", "boolean"],
)
def test_wrong_top_level_type(data, found):
    """Wrong top-level type.

    What:     A top-level value that is not a list or an object is rejected.
    Why:      The user should be told what was found, not shown a crash.
    Expected: A ToolsFileError that mentions "list of tools" and what was found.
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools(data)
    assert "list of tools" in str(error.value)
    assert found in str(error.value)


def test_object_without_tools():
    """Object without tools.

    What:     An object with no "tools" key is rejected, and the keys it has are listed.
    Why:      The usual cause is the wrong file, and the keys help to spot that.
    Expected: A ToolsFileError that mentions "no 'tools' list" and the keys found.
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools({"servers": [], "version": 1})
    assert "no 'tools' list" in str(error.value)
    assert "servers, version" in str(error.value)


def test_tools_must_be_a_list():
    """Tools must be a list.

    What:     {"tools": {...}} is rejected.
    Why:      A single tool object where a list is expected is an easy mistake to make.
    Expected: A ToolsFileError that mentions "'tools' must be a list" and "an object".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools({"tools": {"name": "x"}})
    assert "'tools' must be a list" in str(error.value)
    assert "an object" in str(error.value)


def test_a_tool_must_be_an_object():
    """A tool must be an object.

    What:     A list item that is a string is rejected, with its position.
    Why:      The user needs to know which item to fix.
    Expected: A ToolsFileError that mentions "tool #2 must be an object" and "a string".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"name": "ok"}, "oops"])
    assert "tool #2 must be an object" in str(error.value)
    assert "a string" in str(error.value)


def test_missing_name_is_reported_with_position():
    """Missing name is reported with its position.

    What:     A tool without a name is rejected, and the error says which tool and why.
    Why:      With no name to show, the position is the only way to find the tool.
    Expected: A ToolsFileError that mentions "tool #2", "name" and "Field required".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"name": "ok"}, {"description": "no name"}])
    message = str(error.value)
    assert "tool #2" in message
    assert "name" in message
    assert "Field required" in message


def test_wrong_type_is_reported_with_name_and_field():
    """Wrong type is reported with the tool name and the field.

    What:     A description that is a list is rejected, naming the tool and the field.
    Why:      Both the position and the name help to find the tool in a long file.
    Expected: A ToolsFileError that mentions "tool #1 ('x')" and "description".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"name": "x", "description": ["not", "text"]}])
    assert "tool #1 ('x')" in str(error.value)
    assert "description" in str(error.value)


def test_nested_problem_shows_its_path():
    """Nested problem shows its path.

    What:     A bad value inside annotations is reported with the path to it.
    Why:      "title" alone would be ambiguous, since both the tool and its annotations
              have one.
    Expected: The error mentions "annotations.title".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"name": "x", "annotations": {"title": 5}}])
    assert "annotations.title" in str(error.value)


def test_several_problems_in_one_tool_are_all_reported():
    """Several problems in one tool are all reported.

    What:     A tool with two wrong fields lists both in one error.
    Why:      Fixing them one run at a time is slow.
    Expected: The error mentions both "title" and "description".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"name": "x", "title": 1, "description": 2}])
    assert "title" in str(error.value)
    assert "description" in str(error.value)


def test_first_bad_tool_is_the_one_reported():
    """First bad tool is the one reported.

    What:     With two invalid tools, the error is about the first.
    Why:      It documents that loading stops at the first invalid tool.
    Expected: The error mentions "tool #1" and not "tool #3".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"description": "a"}, {"name": "ok"}, {"description": "b"}])
    assert "tool #1" in str(error.value)
    assert "tool #3" not in str(error.value)


# --- load_tools: files ----------------------------------------------------------


def test_load_from_a_file(tmp_path):
    """Load from a file.

    What:     A JSON file with a tools list loads into ToolDef objects.
    Why:      The main path of the tools-file feature.
    Expected: Two tools with the names from the file.
    """
    path = write_json(tmp_path / "tools.json", {"tools": TOOLS})
    assert [tool.name for tool in load_tools(path)] == ["list_notes", "create_note"]


def test_path_can_be_a_string(tmp_path):
    """Path can be a string.

    What:     load_tools accepts a plain string path as well as a Path.
    Why:      The command line gives it a string.
    Expected: Two tools.
    """
    path = write_json(tmp_path / "tools.json", TOOLS)
    assert len(load_tools(str(path))) == 2


def test_file_with_a_byte_order_mark_loads(tmp_path):
    """File with a byte order mark loads.

    What:     A file that starts with a UTF-8 byte order mark is read normally.
    Why:      Windows editors often write one, and it would otherwise break the JSON.
    Expected: Two tools.
    """
    path = tmp_path / "tools.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(TOOLS).encode("utf-8"))
    assert len(load_tools(path)) == 2


def test_non_ascii_text_loads(tmp_path):
    """Non-ASCII text loads.

    What:     Tool names and descriptions with accents and non-Latin text are kept.
    Why:      Real servers use more than English.
    Expected: The name and description come back unchanged.
    """
    path = write_json(tmp_path / "tools.json", [{"name": "búsqueda_検索", "description": "é"}])
    tool = load_tools(path)[0]
    assert tool.name == "búsqueda_検索"
    assert tool.description == "é"


def test_utf16_file_gets_a_clear_error(tmp_path):
    """UTF-16 file gets a clear error.

    What:     A file saved as UTF-16 is rejected with advice to save it as UTF-8.
    Why:      PowerShell's > operator writes UTF-16, so this is a likely Windows trap.
    Expected: A ToolsFileError that mentions the file name and "UTF-8".
    """
    path = tmp_path / "tools.json"
    path.write_bytes(json.dumps(TOOLS).encode("utf-16"))
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert str(path) in str(error.value)
    assert "UTF-8" in str(error.value)


def test_invalid_json_gets_a_clear_error(tmp_path):
    """Invalid JSON gets a clear error.

    What:     A file that is not valid JSON is rejected, with the line and column.
    Why:      The position is what lets the user fix it.
    Expected: A ToolsFileError that mentions the file name, "not valid JSON" and "line 1".
    """
    path = tmp_path / "tools.json"
    path.write_text('{"tools": [', encoding="utf-8")
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert str(path) in str(error.value)
    assert "not valid JSON" in str(error.value)
    assert "line 1" in str(error.value)


def test_empty_file_gets_a_clear_error(tmp_path):
    """Empty file gets a clear error.

    What:     An empty file is rejected as invalid JSON.
    Why:      An empty file is a common result of a failed export.
    Expected: A ToolsFileError that mentions "not valid JSON".
    """
    path = tmp_path / "tools.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert "not valid JSON" in str(error.value)


def test_missing_file_gets_a_clear_error(tmp_path):
    """Missing file gets a clear error.

    What:     A path that does not exist is rejected with the path in the message.
    Why:      A typo in the path should not look like a crash.
    Expected: A ToolsFileError that mentions the path and "cannot read file".
    """
    path = tmp_path / "nope.json"
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert str(path) in str(error.value)
    assert "cannot read file" in str(error.value)


def test_folder_instead_of_file_gets_a_clear_error(tmp_path):
    """Folder instead of file gets a clear error.

    What:     A path that is a folder is rejected as unreadable.
    Why:      Passing a folder by mistake should not look like a crash.
    Expected: A ToolsFileError that mentions "cannot read file".
    """
    with pytest.raises(ToolsFileError) as error:
        load_tools(tmp_path)
    assert "cannot read file" in str(error.value)


def test_content_errors_start_with_the_file_name(tmp_path):
    """Content errors start with the file name.

    What:     A problem inside the tools is reported after the file name.
    Why:      With several files, the message must say which one is wrong.
    Expected: The message starts with "<path>: tool #1".
    """
    path = write_json(tmp_path / "tools.json", [{"description": "no name"}])
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert str(error.value).startswith(f"{path}: tool #1")


def test_a_thousand_tools_load(tmp_path):
    """A thousand tools load.

    What:     A file with 1,000 tools loads completely, in order.
    Why:      Large servers exist, and loading must stay correct at that size.
    Expected: 1,000 tools, with the first and last names right.
    """
    path = write_json(tmp_path / "tools.json", [{"name": f"tool_{i}"} for i in range(1000)])
    tools = load_tools(path)
    assert len(tools) == 1000
    assert tools[0].name == "tool_0"
    assert tools[-1].name == "tool_999"

# --- Hints for common mistakes --------------------------------------------------


def test_server_error_response_shows_the_servers_message():
    """Server error response shows the server's message.

    What:     A saved JSON-RPC error response is reported as an error response, with
              the message the server gave.
    Why:      "No 'tools' list found" would hide the real cause, such as a server that
              does not support listing tools.
    Expected: A ToolsFileError that mentions "error response" and "Method not found".
    """
    response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    with pytest.raises(ToolsFileError) as error:
        parse_tools(response)
    assert "error response" in str(error.value)
    assert "Method not found" in str(error.value)


def test_server_error_response_without_a_message():
    """Server error response without a message.

    What:     An error object with no message still gives a clear error.
    Why:      The hint must not crash on incomplete error objects.
    Expected: A ToolsFileError that mentions "error response" and "no message given".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools({"error": {"code": 1}})
    assert "error response" in str(error.value)
    assert "no message given" in str(error.value)


def test_tools_next_to_an_error_key_still_load():
    """Tools next to an error key still load.

    What:     An object that has a "tools" list and also an "error" key is read as tools.
    Why:      The error hint must only apply when there are no tools to read.
    Expected: Two tools.
    """
    assert len(parse_tools({"tools": TOOLS, "error": {"message": "partial"}})) == 2


def test_openai_function_definition_gets_a_hint():
    """OpenAI function definition gets a hint.

    What:     A tool written as {"type": "function", "function": {...}} is rejected
              with a message that says what it looks like.
    Why:      "name: Field required" would be confusing for a file that is the wrong
              format, not a file with a forgotten name.
    Expected: A ToolsFileError that mentions "tool #1", "OpenAI" and "top level".
    """
    tool = {"type": "function", "function": {"name": "get_weather", "parameters": {}}}
    with pytest.raises(ToolsFileError) as error:
        parse_tools([tool])
    assert "tool #1" in str(error.value)
    assert "OpenAI" in str(error.value)
    assert "top level" in str(error.value)


def test_gemini_declarations_get_a_hint():
    """Gemini declarations get a hint.

    What:     A tool written as {"functionDeclarations": [...]} is rejected with a
              message that says what it looks like.
    Why:      Same as for OpenAI: the file is the wrong format.
    Expected: A ToolsFileError that mentions "tool #1" and "Gemini".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"functionDeclarations": [{"name": "get_weather"}]}])
    assert "tool #1" in str(error.value)
    assert "Gemini" in str(error.value)


def test_a_tool_with_a_name_gets_no_format_hint():
    """A tool with a name gets no format hint.

    What:     A tool that has a name and also a "function" key loads normally.
    Why:      The hint is for tools that cannot be read as MCP. A named tool can.
    Expected: The tool loads, and "function" is kept as an unknown field.
    """
    tool = parse_tools([{"name": "x", "function": {"a": 1}}])[0]
    assert tool.model_extra == {"function": {"a": 1}}


def test_function_key_that_is_not_an_object_gets_no_hint():
    """A function key that is not an object gets no hint.

    What:     {"function": "text"} without a name gets the normal missing-name error.
    Why:      Only a real OpenAI-style object should trigger the format hint.
    Expected: A ToolsFileError that mentions "Field required" and not "OpenAI".
    """
    with pytest.raises(ToolsFileError) as error:
        parse_tools([{"function": "text"}])
    assert "Field required" in str(error.value)
    assert "OpenAI" not in str(error.value)


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        ('[{"name": "a"},]', "comma"),
        ('[{"name": "a"}, // second tool later\n]', "comments"),
        ('// my tools\n[{"name": "a"}]', "comments"),
        ('/* my tools */ [{"name": "a"}]', "comments"),
    ],
    ids=["trailing-comma", "end-of-line-comment", "line-comment", "block-comment"],
)
def test_hand_edited_json_gets_a_hint(tmp_path, text, hint):
    """Hand-edited JSON gets a hint.

    What:     A file with a trailing comma or a comment is rejected with a hint.
    Why:      Both are allowed by many editors and tools but not by JSON, and the
              plain parser message does not say so.
    Expected: A ToolsFileError that mentions "not valid JSON" and the hint.
    """
    path = tmp_path / "tools.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert "not valid JSON" in str(error.value)
    assert hint in str(error.value)


def test_other_invalid_json_gets_no_hint(tmp_path):
    """Other invalid JSON gets no hint.

    What:     A file that is cut off gets the plain error, with no advice about
              comments or commas.
    Why:      A wrong hint is worse than none.
    Expected: A ToolsFileError that mentions "not valid JSON" and neither "comma"
              nor "comments".
    """
    path = tmp_path / "tools.json"
    path.write_text('{"tools": [', encoding="utf-8")
    with pytest.raises(ToolsFileError) as error:
        load_tools(path)
    assert "not valid JSON" in str(error.value)
    assert "comma" not in str(error.value)
    assert "comments" not in str(error.value)