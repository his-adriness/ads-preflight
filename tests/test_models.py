"""Tests for CallRecord: basics, a gauntlet of edge cases, and the .text helper."""

import copy
import json
import math
import pickle
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from mcpdoctor.models import CallRecord


def make_record(**overrides):
    """Build a valid CallRecord, overriding only the fields a test cares about."""
    data = {"tool": "search_notes", "arguments": {}, "result": "ok", "latency_ms": 1.5}
    data.update(overrides)
    return CallRecord(**data)


# --- Basics: defaults, validation, equality ------------------------------------

def test_defaults():
    """is_error defaults to False when not provided.

    Passes if: record.is_error is False.
    """
    record = make_record()
    assert record.is_error is False


def test_record_is_frozen():
    """Assigning to a field raises, so records can't be changed in place.

    Passes if: setting record.tool raises a ValidationError.
    """
    record = make_record()
    with pytest.raises(ValidationError):
        record.tool = "other"


def test_missing_required_field_raises():
    """Leaving out a required field (latency_ms) fails validation.

    Passes if: creating the record without latency_ms raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        CallRecord(tool="x", arguments={}, result="ok")  # latency_ms missing


def test_arguments_must_be_a_dict():
    """Arguments given as anything other than a dict are rejected.

    Passes if: arguments="not a dict" raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        make_record(arguments="not a dict")


def test_latency_int_is_converted_to_float():
    """An integer latency is stored as a float.

    Passes if: latency_ms=2 is stored as 2.0 and its type is float.
    """
    record = make_record(latency_ms=2)
    assert record.latency_ms == 2.0
    assert isinstance(record.latency_ms, float)


def test_equal_records_compare_equal():
    """Records with the same values are equal, and different ones are not.

    Passes if: two identical records are ==, and records with different tools are !=.
    """
    assert make_record() == make_record()
    assert make_record() != make_record(tool="other")


# --- Structure and contract ----------------------------------------------------

def test_fields_contract():
    """The model has exactly these five fields, in this order.

    Passes if: the field names are tool, arguments, result, is_error, latency_ms.
    Guards against accidental renames/additions that would break saved JSONL files.
    """
    assert list(CallRecord.model_fields) == [
        "tool",
        "arguments",
        "result",
        "is_error",
        "latency_ms",
    ]


def test_result_is_required():
    """result has no default, so leaving it out fails even though it accepts Any.

    Passes if: creating a record without result raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        CallRecord(tool="x", arguments={}, latency_ms=1.0)


def test_dump_has_only_fields_not_the_text_property():
    """model_dump() contains the five fields and not the computed .text helper.

    Passes if: the dumped keys equal the field names and "text" is absent.
    """
    dumped = make_record().model_dump()
    assert set(dumped) == set(CallRecord.model_fields)
    assert "text" not in dumped


# --- Immutability --------------------------------------------------------------

def test_cannot_delete_or_add_attributes():
    """A frozen record rejects deleting a field and adding a new attribute.

    Passes if: `del record.tool` and `record.extra = 1` both raise.
    """
    record = make_record()
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        del record.tool
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        record.extra = 1


def test_text_property_is_read_only():
    """The .text helper can't be assigned to.

    Passes if: setting record.text raises, and .text still returns the original value.
    """
    record = make_record(result="hello")
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        record.text = "hacked"
    assert record.text == "hello"


def test_frozen_is_shallow_nested_values_can_still_change():
    """KNOWN LIMITATION: frozen only protects the fields, not the dicts/lists inside them.

    Passes if: mutating record.arguments in place succeeds (documenting the gap).
    If you ever make nested data immutable, flip this test.
    """
    record = make_record(arguments={"q": "a"})
    record.arguments["q"] = "changed"
    assert record.arguments == {"q": "changed"}


def test_record_with_dict_fields_is_unhashable():
    """Records hold dicts, so they can't be put in a set or used as dict keys.

    Passes if: hash(record) raises TypeError.
    """
    with pytest.raises(TypeError):
        hash(make_record())


# --- Copying and pickling ------------------------------------------------------

def test_model_copy_returns_new_record_and_keeps_original():
    """model_copy(update=...) returns a changed copy and leaves the original alone.

    Passes if: the copy has tool == "other", the original still has
    tool == "search_notes", and they are different objects.
    """
    original = make_record()
    updated = original.model_copy(update={"tool": "other"})
    assert updated.tool == "other"
    assert original.tool == "search_notes"
    assert updated is not original


def test_model_copy_is_shallow_and_deep_copy_is_independent():
    """model_copy shares nested objects with the original; deep=True does not.

    Passes if: the shallow copy's result is the same object, the deep copy's is
    an equal but different object.
    """
    original = make_record(result=[{"type": "text", "text": "a"}])
    shallow = original.model_copy()
    deep = original.model_copy(deep=True)
    assert shallow.result is original.result
    assert deep.result == original.result
    assert deep.result is not original.result


def test_model_copy_update_skips_validation():
    """GOTCHA: model_copy(update=...) does not validate the new values.

    Passes if: an invalid latency passes through model_copy without an error
    (while the normal constructor would reject it). Normalizers should rebuild
    records via model_validate instead of trusting model_copy.
    """
    bad = make_record().model_copy(update={"latency_ms": "not a number"})
    assert bad.latency_ms == "not a number"
    with pytest.raises(ValidationError):
        make_record(latency_ms="not a number")


def test_deepcopy_and_pickle_preserve_equality():
    """Records survive deepcopy and pickle unchanged.

    Passes if: both the deep copy and the unpickled record equal the original.
    """
    record = make_record(result=[{"type": "text", "text": "hi"}], is_error=True)
    assert copy.deepcopy(record) == record
    assert pickle.loads(pickle.dumps(record)) == record


# --- Serialization (JSONL safety) ----------------------------------------------

def test_json_round_trip():
    """A record survives being written to JSON and read back unchanged.

    Passes if: the record read back from JSON equals the original.
    """
    record = make_record(
        arguments={"query": "meeting"},
        result=[{"type": "text", "text": "hi"}],
        is_error=True,
    )
    restored = CallRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_json_is_single_line_even_with_newlines_in_result():
    """One record must be one line, or the JSONL session format breaks.

    Passes if: the JSON string contains no raw newline, and parsing it gives
    the original text back.
    """
    record = make_record(result="line1\nline2\r\nline3")
    dumped = record.model_dump_json()
    assert "\n" not in dumped
    assert "\r" not in dumped
    assert json.loads(dumped)["result"] == "line1\nline2\r\nline3"


def test_json_field_order_and_determinism():
    """JSON output is stable: same record, same string, fixed key order.

    Passes if: dumping twice gives identical strings and keys appear in field order.
    This keeps snapshot and JSONL diffs readable in git.
    """
    record = make_record(arguments={"b": 1, "a": 2})
    first, second = record.model_dump_json(), record.model_dump_json()
    assert first == second
    assert list(json.loads(first)) == list(CallRecord.model_fields)


def test_json_round_trip_unicode_and_large_payload():
    """Unicode names and a ~1 MB result survive a JSON round trip.

    Passes if: the restored record equals the original.
    """
    record = make_record(tool="búsqueda_📝_検索", result="é" * 1_000_000)
    restored = CallRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_json_round_trip_deeply_nested_result():
    """A result nested 10 levels deep round-trips through JSON.

    Passes if: the restored record equals the original.
    Very deep nesting (50+ levels) hits pydantic's recursion limit, which varies
    by version, so this uses a depth real servers could plausibly return.
    """
    nested = "leaf"
    for _ in range(10):
        nested = {"child": [nested]}
    record = make_record(result=nested)
    assert CallRecord.model_validate_json(record.model_dump_json()) == record


def test_non_serializable_result_fails_at_dump_time_not_construction():
    """result accepts Any, so a non-JSON value is only caught when writing JSON.

    Passes if: building the record works, but model_dump_json() raises
    a PydanticSerializationError.
    """
    record = make_record(result=object())
    with pytest.raises(PydanticSerializationError):
        record.model_dump_json()


# --- Type coercion and strange inputs ------------------------------------------

def test_is_error_truthy_values_coerce_to_true():
    """Common truthy values are coerced to True.

    Passes if: is_error is True for True, 1, "true", "True", "yes" and "1".
    """
    for value in [True, 1, "true", "True", "yes", "1"]:
        assert make_record(is_error=value).is_error is True


def test_is_error_rejects_non_boolean_looking_values():
    """Values that don't look like booleans are rejected.

    Passes if: "maybe", [], {} and None raise a ValidationError.
    """
    for value in ["maybe", [], {}, None]:
        with pytest.raises(ValidationError):
            make_record(is_error=value)


def test_extra_fields_are_rejected():
    """Unknown fields are rejected, so typos in field names are caught.

    Passes if: passing surprise=1 raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        make_record(surprise=1)


# --- Latency edge cases --------------------------------------------------------

def test_latency_boundaries_are_accepted():
    """Zero, tiny and enormous latencies are valid.

    Passes if: 0, 0.0001 and 1e12 are stored as floats equal to the input.
    """
    for value in [0, 0.0001, 1e12]:
        assert make_record(latency_ms=value).latency_ms == float(value)


def test_negative_latency_is_rejected():
    """A duration can't be negative.

    Passes if: latency_ms=-1 and latency_ms=-0.001 raise a ValidationError.
    """
    for bad in (-1, -0.001):
        with pytest.raises(ValidationError):
            make_record(latency_ms=bad)


def test_nan_and_infinite_latency_are_rejected():
    """NaN and infinity make no sense as a latency (and break JSON).

    Passes if: latency_ms=nan, inf and -inf all raise a ValidationError.
    """
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            make_record(latency_ms=bad)


# --- The .text helper ----------------------------------------------------------

def test_text_from_plain_string():
    """A plain string result is returned as is by .text.

    Passes if: .text == "hello" for result="hello".
    """
    assert make_record(result="hello").text == "hello"


def test_text_joins_text_blocks_and_skips_others():
    """Text blocks are joined with newlines and non-text blocks are skipped.

    Passes if: .text == "a\\nb" for blocks text "a", image, text "b".
    """
    result = [
        {"type": "text", "text": "a"},
        {"type": "image", "data": "...", "mimeType": "image/png"},
        {"type": "text", "text": "b"},
    ]
    assert make_record(result=result).text == "a\nb"


def test_text_is_empty_for_none():
    """A None result gives an empty string instead of an error.

    Passes if: .text == "" for result=None.
    """
    assert make_record(result=None).text == ""


def test_text_is_empty_for_empty_list():
    """An empty list of blocks gives an empty string.

    Passes if: .text == "" for result=[].
    """
    assert make_record(result=[]).text == ""


def test_text_is_empty_for_unsupported_result_type():
    """A result that is neither a string nor a list gives an empty string.

    Passes if: .text == "" for result=123.
    """
    assert make_record(result=123).text == ""


def test_text_ignores_blocks_without_text_key():
    """A text block with no 'text' key is skipped, like any malformed block.

    Passes if: .text == "kept" for a text block with no text followed by "kept".
    """
    result = [{"type": "text"}, {"type": "text", "text": "kept"}]
    assert make_record(result=result).text == "kept"


def test_text_ignores_non_dict_items():
    """Items in the list that aren't dicts are ignored.

    Passes if: .text == "kept" for ["stray string", 42, text block "kept"].
    """
    result = ["stray string", 42, {"type": "text", "text": "kept"}]
    assert make_record(result=result).text == "kept"


def test_text_survives_non_string_text_values():
    """A malformed block must not crash .text.

    Passes if: blocks whose 'text' is None, a number, a bool, a list or a dict are
    skipped, and .text == "kept" (only the valid block's text is returned).
    """
    result = [
        {"type": "text", "text": None},
        {"type": "text", "text": 123},
        {"type": "text", "text": True},
        {"type": "text", "text": ["x"]},
        {"type": "text", "text": {"k": 1}},
        {"type": "text", "text": "kept"},
    ]
    assert make_record(result=result).text == "kept"


def test_text_is_repeatable_and_does_not_mutate_result():
    """Reading .text twice gives the same value and leaves result untouched.

    Passes if: both calls match and result still equals its original value.
    """
    result = [{"type": "text", "text": "a"}, {"type": "image"}]
    snapshot = copy.deepcopy(result)
    record = make_record(result=result)
    assert record.text == record.text == "a"
    assert record.result == snapshot


def test_text_handles_ten_thousand_blocks():
    """A huge list of blocks is joined correctly.

    Passes if: .text has 10,000 lines and the first and last are right.
    """
    result = [{"type": "text", "text": f"line{i}"} for i in range(10_000)]
    lines = make_record(result=result).text.split("\n")
    assert len(lines) == 10_000
    assert lines[0] == "line0"
    assert lines[-1] == "line9999"


# --- Concurrency ---------------------------------------------------------------

def test_records_are_safe_to_share_across_threads():
    """Many threads reading one record all see the same, uncorrupted data.

    Passes if: 200 concurrent reads of .text all return "a\\nb".
    """
    record = make_record(
        result=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    )
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: record.text, range(200)))
    assert results == ["a\nb"] * 200