"""Tests for ToolDef and ToolAnnotations.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import copy
import pickle

import pytest
from pydantic import ValidationError

from mcpdoctor.models import ToolAnnotations, ToolDef

# A tool as a server typically lists it, with every standard field.
FULL_TOOL = {
    "name": "list_notes",
    "title": "List notes",
    "description": "List the user's notes, newest first.",
    "inputSchema": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1}},
        "required": [],
    },
    "outputSchema": {"type": "object"},
    "annotations": {
        "title": "List notes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "_meta": {"vendor": "example"},
}

# Values a server might send for a hint instead of a real True or False.
NON_BOOLEAN_HINTS = ["true", "false", "yes", 1, 0, "maybe", [], {}]


def make_tool(**overrides):
    """Build a valid ToolDef from wire-format keys, overriding only what a test needs."""
    data = {"name": "search_notes"}
    data.update(overrides)
    return ToolDef.model_validate(data)


# --- Basics: required fields and defaults ----------------------------------------


def test_minimal_tool_needs_only_a_name():
    """Minimal tool needs only a name.

    What:     A tool with just a name loads, and every other field is None.
    Why:      The linter must be able to load a badly written tool in order to report
              on it. Rejecting a missing description would hide the problem.
    Expected: name is set, and the other five fields are all None.
    """
    tool = make_tool()
    assert tool.name == "search_notes"
    assert tool.title is None
    assert tool.description is None
    assert tool.inputSchema is None
    assert tool.outputSchema is None
    assert tool.annotations is None


def test_name_is_required():
    """Name is required.

    What:     A tool with no name is rejected.
    Why:      A tool that cannot be called by name cannot be tested or reported on.
    Expected: ToolDef.model_validate({}) raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        ToolDef.model_validate({})


@pytest.mark.parametrize("bad", [123, None, ["x"], {"a": 1}])
def test_name_must_be_a_string(bad):
    """Name must be a string.

    What:     A name that is a number, None, a list or a dict is rejected.
    Why:      A wrong type here is a broken file, not a style problem, and it should
              fail where it is loaded.
    Expected: A ValidationError for each value.
    """
    with pytest.raises(ValidationError):
        make_tool(name=bad)


def test_empty_name_is_accepted():
    """Empty name is accepted.

    What:     An empty string is a valid name at this level.
    Why:      An empty name is a quality problem for a lint rule to report. The model
              only rejects data it cannot represent.
    Expected: make_tool(name="").name == "".
    """
    assert make_tool(name="").name == ""


def test_full_tool_loads_every_field():
    """Full tool loads every field.

    What:     A realistic tool with every standard field is read correctly.
    Why:      The main path: this is what a real tools/list entry looks like.
    Expected: Each field holds the value from the input, and the annotations are a
              ToolAnnotations with the four hints.
    """
    tool = ToolDef.model_validate(FULL_TOOL)
    assert tool.name == "list_notes"
    assert tool.title == "List notes"
    assert tool.description == "List the user's notes, newest first."
    assert tool.inputSchema == FULL_TOOL["inputSchema"]
    assert tool.outputSchema == {"type": "object"}
    assert isinstance(tool.annotations, ToolAnnotations)
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is False


def test_construct_in_python_with_wire_names():
    """Construct in Python with wire names.

    What:     ToolDef(name=..., inputSchema=...) works as a normal constructor.
    Why:      Field names match the wire format, so code and data use one spelling.
    Expected: The inputSchema keyword is stored in the inputSchema field.
    """
    tool = ToolDef(name="x", inputSchema={"type": "object"})
    assert tool.inputSchema == {"type": "object"}


# --- Wrong types ----------------------------------------------------------------


@pytest.mark.parametrize("field", ["title", "description"])
def test_text_fields_must_be_strings(field):
    """Text fields must be strings.

    What:     A title or description that is a list is rejected.
    Why:      Later rules treat the description as text, and a list would crash them.
    Expected: A ValidationError for each field.
    """
    with pytest.raises(ValidationError):
        make_tool(**{field: ["not", "text"]})


@pytest.mark.parametrize("field", ["inputSchema", "outputSchema"])
def test_schemas_must_be_objects(field):
    """Schemas must be objects.

    What:     A schema given as a string or a list is rejected.
    Why:      A JSON Schema is an object. Later rules look inside it.
    Expected: A ValidationError for a string and for a list.
    """
    for bad in ("not a schema", ["x"]):
        with pytest.raises(ValidationError):
            make_tool(**{field: bad})


def test_annotations_must_be_an_object():
    """Annotations must be an object.

    What:     Annotations given as a list or a string are rejected.
    Why:      Hints are read by name, so anything other than an object is unusable.
    Expected: A ValidationError for both.
    """
    for bad in (["readOnlyHint"], "readOnly"):
        with pytest.raises(ValidationError):
            make_tool(annotations=bad)


def test_null_optional_fields_load_as_none():
    """Null optional fields load as None.

    What:     Explicit nulls for the optional fields are accepted.
    Why:      Some servers send null instead of leaving a key out.
    Expected: All optional fields are None.
    """
    tool = make_tool(
        title=None, description=None, inputSchema=None, outputSchema=None, annotations=None
    )
    assert tool.title is None
    assert tool.description is None
    assert tool.inputSchema is None
    assert tool.outputSchema is None
    assert tool.annotations is None


# --- Annotations ----------------------------------------------------------------


def test_hints_that_were_not_sent_are_none_not_false():
    """Unsent hints are None, not False.

    What:     An annotations object with no hints leaves every hint as None.
    Why:      The MCP spec has different defaults per hint. "Not said" and "said no"
              must stay apart, or a missing readOnlyHint would look like a claim.
    Expected: All four hints are None.
    """
    annotations = make_tool(annotations={}).annotations
    assert annotations.readOnlyHint is None
    assert annotations.destructiveHint is None
    assert annotations.idempotentHint is None
    assert annotations.openWorldHint is None


@pytest.mark.parametrize("bad", NON_BOOLEAN_HINTS)
def test_non_boolean_hints_load_and_are_kept_as_sent(bad):
    """Non-boolean hints load and are kept as sent.

    What:     A hint that is a string, a number, a list or a dict loads, and its value
              and type are stored unchanged.
    Why:      A lint rule has to be able to report "readOnlyHint is the string 'true'".
              Rejecting the value would stop the whole file from loading, and the
              mistake would never be reported. Converting it would hide the mistake.
    Expected: annotations.readOnlyHint equals the input and has the same type.
    """
    value = make_tool(annotations={"readOnlyHint": bad}).annotations.readOnlyHint
    assert value == bad
    assert type(value) is type(bad)


@pytest.mark.parametrize("bad", NON_BOOLEAN_HINTS)
def test_non_boolean_read_only_hint_counts_as_unknown(bad):
    """A non-boolean readOnlyHint counts as unknown.

    What:     A readOnlyHint that is not a real True or False gives "unknown".
    Why:      A safety check must not guess. Reading the string "false" as False, or
              "yes" as True, could mark a tool safe when it is not.
    Expected: declared_access == "unknown" for each value.
    """
    assert make_tool(annotations={"readOnlyHint": bad}).declared_access == "unknown"


def test_null_hint_is_none():
    """A null hint is None.

    What:     An explicit null hint is accepted and means "no claim".
    Why:      Same as an unsent hint.
    Expected: readOnlyHint is None.
    """
    assert make_tool(annotations={"readOnlyHint": None}).annotations.readOnlyHint is None


# --- Unknown fields are kept ---------------------------------------------------


def test_unknown_top_level_fields_are_kept():
    """Unknown top-level fields are kept.

    What:     Keys we do not model, including _meta, are stored on the tool.
    Why:      Vendors add their own keys, and rules for them need to see those.
    Expected: model_extra holds _meta and the vendor key with their values.
    """
    tool = make_tool(_meta={"a": 1}, **{"x-vendor": "ads"})
    assert tool.model_extra == {"_meta": {"a": 1}, "x-vendor": "ads"}


def test_unknown_annotation_fields_are_kept():
    """Unknown annotation fields are kept.

    What:     A vendor hint inside annotations is stored on the annotations.
    Why:      Same reason as for the tool: vendor hints matter to vendor rules.
    Expected: model_extra on the annotations holds the vendor hint.
    """
    annotations = make_tool(annotations={"readOnlyHint": True, "spendsMoney": True}).annotations
    assert annotations.model_extra == {"spendsMoney": True}


def test_snake_case_keys_are_not_read_as_wire_fields():
    """Snake_case keys are not read as wire fields.

    What:     A tool that spells the key input_schema does not get an inputSchema.
    Why:      A server using the wrong spelling is buggy, and the linter should be able
              to say so. Accepting the wrong spelling would hide it.
    Expected: inputSchema is None, and input_schema is kept as an unknown field.
    """
    tool = make_tool(input_schema={"type": "object"})
    assert tool.inputSchema is None
    assert tool.model_extra == {"input_schema": {"type": "object"}}


# --- Round trip ------------------------------------------------------------------


def test_dump_gives_back_what_the_server_sent():
    """Dump gives back what the server sent.

    What:     Loading a tool and dumping it without None values returns the input.
    Why:      Snapshots and reports compare tools. A loader that changed the data
              would create differences that were never in the server.
    Expected: ToolDef.model_validate(FULL_TOOL).model_dump(exclude_none=True) equals
              FULL_TOOL.
    """
    tool = ToolDef.model_validate(FULL_TOOL)
    assert tool.model_dump(exclude_none=True) == FULL_TOOL


def test_json_round_trip():
    """JSON round trip.

    What:     A tool written to JSON and read back is equal to the original.
    Why:      Tools files and saved reports are JSON.
    Expected: The restored tool equals the original.
    """
    tool = ToolDef.model_validate(FULL_TOOL)
    assert ToolDef.model_validate_json(tool.model_dump_json()) == tool


def test_nested_schema_is_preserved_exactly():
    """Nested schema is preserved exactly.

    What:     A deeply nested schema with arrays, enums and $ref comes back unchanged.
    Why:      Schema drift checks later depend on the schema being stored as sent.
    Expected: tool.inputSchema == the input schema.
    """
    schema = {
        "type": "object",
        "$defs": {"id": {"type": "string", "pattern": "^[0-9]+$"}},
        "properties": {
            "profileId": {"$ref": "#/$defs/id"},
            "status": {"enum": ["ENABLED", "PAUSED"], "default": "PAUSED"},
            "ids": {"type": "array", "items": {"$ref": "#/$defs/id"}, "maxItems": 100},
        },
        "required": ["profileId"],
        "additionalProperties": False,
    }
    assert make_tool(inputSchema=schema).inputSchema == schema


def test_unicode_and_long_text_survive():
    """Unicode and long text survive.

    What:     Non-English names and a description of about 1 MB load and round-trip.
    Why:      Real servers use non-English text, and some descriptions are very long.
    Expected: The restored tool equals the original.
    """
    tool = make_tool(name="búsqueda_検索", description="é" * 1_000_000)
    assert ToolDef.model_validate_json(tool.model_dump_json()) == tool


# --- Immutability and copying ------------------------------------------------------


def test_tool_is_frozen():
    """Tool is frozen.

    What:     Assigning to a field of a ToolDef raises an error.
    Why:      Several rules read the same tool, and none of them may change it.
    Expected: Setting tool.name raises a ValidationError.
    """
    tool = make_tool()
    with pytest.raises(ValidationError):
        tool.name = "other"


def test_annotations_are_frozen():
    """Annotations are frozen.

    What:     Assigning to a hint raises an error.
    Why:      A rule must not be able to turn a "write" claim into a "read" one.
    Expected: Setting annotations.readOnlyHint raises a ValidationError.
    """
    annotations = make_tool(annotations={"readOnlyHint": False}).annotations
    with pytest.raises(ValidationError):
        annotations.readOnlyHint = True


def test_equal_tools_compare_equal():
    """Equal tools compare equal.

    What:     Tools with the same data are equal, and different ones are not.
    Why:      Comparing two listings relies on value equality.
    Expected: Two identical tools are ==, and tools with different descriptions are !=.
    """
    assert make_tool(description="a") == make_tool(description="a")
    assert make_tool(description="a") != make_tool(description="b")


def test_deepcopy_and_pickle_preserve_equality():
    """Deepcopy and pickle preserve equality.

    What:     A tool survives deepcopy and pickle, including its unknown fields.
    Why:      Tools may be copied or sent between processes.
    Expected: The copy and the unpickled tool both equal the original.
    """
    tool = ToolDef.model_validate(FULL_TOOL)
    assert copy.deepcopy(tool) == tool
    assert pickle.loads(pickle.dumps(tool)) == tool


# --- declared_access ------------------------------------------------------------


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        ({"readOnlyHint": True}, "read"),
        ({"readOnlyHint": False}, "write"),
        ({"readOnlyHint": None}, "unknown"),
        ({}, "unknown"),
        (None, "unknown"),
    ],
    ids=["read-only", "not-read-only", "null-hint", "no-hints", "no-annotations"],
)
def test_declared_access(annotations, expected):
    """Declared access.

    What:     declared_access follows readOnlyHint: True is "read", False is "write",
              and anything unsaid is "unknown".
    Why:      Spend-safety rules depend on this. Silence must never count as safe.
    Expected: The value in the table for each case.
    """
    assert make_tool(annotations=annotations).declared_access == expected


@pytest.mark.parametrize(
    "annotations",
    [{"destructiveHint": True}, {"destructiveHint": False}, {"idempotentHint": True}],
    ids=["destructive", "not-destructive", "idempotent"],
)
def test_other_hints_do_not_decide_access(annotations):
    """Other hints do not decide access.

    What:     destructiveHint or idempotentHint alone leaves access as "unknown".
    Why:      Per the spec these only matter when the tool is not read-only, and
              that is the one thing they leave unsaid.
    Expected: declared_access == "unknown".
    """
    assert make_tool(annotations=annotations).declared_access == "unknown"


def test_read_only_wins_over_other_hints():
    """Read-only wins over other hints.

    What:     readOnlyHint=True with destructiveHint=True is still "read".
    Why:      The two claims contradict each other. The model reports the main claim
              and leaves flagging the contradiction to a lint rule.
    Expected: declared_access == "read".
    """
    tool = make_tool(annotations={"readOnlyHint": True, "destructiveHint": True})
    assert tool.declared_access == "read"


# --- A tool shaped like an ad-platform tool ---------------------------------------


def test_ad_style_tool_with_no_annotations_is_unknown():
    """Ad-style tool with no annotations is unknown.

    What:     A tool with a group-style name, three account ID parameters, and no
              annotations loads, and its access is "unknown".
    Why:      This is the shape the project is aimed at. A write tool that says
              nothing about itself must never be treated as safe.
    Expected: The name and parameters load unchanged, and declared_access is "unknown".
    """
    tool = make_tool(
        name="account_management-create_advertiser_account",
        description="Creates an advertiser account.",
        inputSchema={
            "type": "object",
            "properties": {
                "profileId": {"type": "string"},
                "managerAccountId": {"type": "string"},
                "advertiserAccountId": {"type": "string"},
            },
            "required": ["profileId"],
        },
    )
    assert tool.name == "account_management-create_advertiser_account"
    assert set(tool.inputSchema["properties"]) == {
        "profileId",
        "managerAccountId",
        "advertiserAccountId",
    }
    assert tool.declared_access == "unknown"