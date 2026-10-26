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
    """Defaults.

    What:     is_error is False when it is not given.
    Why:      Most calls succeed, and callers should not have to say so every time.
    Expected: record.is_error is False.
    """
    record = make_record()
    assert record.is_error is False


def test_record_is_frozen():
    """Record is frozen.

    What:     Assigning to a field raises an error.
    Why:      Every feature shares records, so an in-place edit would silently change what the
              others see.
    Expected: Setting record.tool raises a ValidationError.
    """
    record = make_record()
    with pytest.raises(ValidationError):
        record.tool = "other"


def test_missing_required_field_raises():
    """Missing required field raises.

    What:     A record without latency_ms is rejected.
    Why:      Every call has a measured time, and a silently missing one would hide bad data.
    Expected: Creating the record without latency_ms raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        CallRecord(tool="x", arguments={}, result="ok")  # latency_ms missing


def test_arguments_must_be_a_dict():
    """Arguments must be a dict.

    What:     Arguments that are not a dict are rejected.
    Why:      Tool arguments are always a JSON object, and anything else would break replaying the
              call.
    Expected: arguments="not a dict" raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        make_record(arguments="not a dict")


def test_latency_int_is_converted_to_float():
    """Latency int is converted to float.

    What:     An integer latency is stored as a float.
    Why:      Latency is always a float, so later math and JSON output behave the same for every
              record.
    Expected: latency_ms=2 is stored as 2.0, and its type is float.
    """
    record = make_record(latency_ms=2)
    assert record.latency_ms == 2.0
    assert isinstance(record.latency_ms, float)


def test_equal_records_compare_equal():
    """Equal records compare equal.

    What:     Records with the same values are equal, and different ones are not.
    Why:      Comparing results between runs depends on value equality.
    Expected: Two identical records are ==, and records with different tools are !=.
    """
    assert make_record() == make_record()
    assert make_record() != make_record(tool="other")


# --- Structure and contract ----------------------------------------------------

def test_fields_contract():
    """Fields contract.

    What:     The model has exactly these five fields, in this order.
    Why:      A rename or an extra field would break JSONL files that were already saved.
    Expected: The field names are tool, arguments, result, is_error, latency_ms.
    """
    assert list(CallRecord.model_fields) == [
        "tool",
        "arguments",
        "result",
        "is_error",
        "latency_ms",
    ]


def test_result_is_required():
    """Result is required.

    What:     result has no default, even though it accepts any value.
    Why:      A record with no result at all is different from one whose result is None, and the
              first is a mistake.
    Expected: Creating a record without result raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        CallRecord(tool="x", arguments={}, latency_ms=1.0)


def test_dump_has_only_fields_not_the_text_property():
    """Dump has only fields not the text property.

    What:     model_dump() holds the five fields and not the .text helper.
    Why:      Records forbid extra fields, so a dumped 'text' key would make a saved record fail to
              load again.
    Expected: The dumped keys equal the field names, and "text" is absent.
    """
    dumped = make_record().model_dump()
    assert set(dumped) == set(CallRecord.model_fields)
    assert "text" not in dumped


# --- Immutability --------------------------------------------------------------

def test_cannot_delete_or_add_attributes():
    """Cannot delete or add attributes.

    What:     A frozen record rejects deleting a field and adding an attribute.
    Why:      Immutability has to cover more than plain assignment.
    Expected: `del record.tool` and `record.extra = 1` both raise.
    """
    record = make_record()
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        del record.tool
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        record.extra = 1


def test_text_property_is_read_only():
    """Text property is read only.

    What:     The .text helper cannot be assigned to.
    Why:      .text is derived from result, so assigning to it would let the two disagree.
    Expected: Setting record.text raises, and .text still returns the original value.
    """
    record = make_record(result="hello")
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        record.text = "hacked"
    assert record.text == "hello"


def test_frozen_is_shallow_nested_values_can_still_change():
    """Frozen is shallow nested values can still change.

    What:     KNOWN LIMITATION: frozen protects the fields, not the dicts and lists inside them.
    Why:      This documents the gap so nobody assumes records are deeply immutable. If nested data
              is ever made immutable, flip this test.
    Expected: Changing record.arguments in place succeeds.
    """
    record = make_record(arguments={"q": "a"})
    record.arguments["q"] = "changed"
    assert record.arguments == {"q": "changed"}


def test_record_with_dict_fields_is_unhashable():
    """Record with dict fields is unhashable.

    What:     Records hold dicts, so they cannot go in a set or be used as dict keys.
    Why:      It stops code from relying on records being hashable.
    Expected: hash(record) raises TypeError.
    """
    with pytest.raises(TypeError):
        hash(make_record())


# --- Copying and pickling ------------------------------------------------------

def test_model_copy_returns_new_record_and_keeps_original():
    """Model copy returns new record and keeps original.

    What:     model_copy(update=...) returns a changed copy and leaves the original alone.
    Why:      Changing a record means building a new one, and the original must stay intact.
    Expected: The copy has tool == "other", the original still has tool == "search_notes", and they
              are different objects.
    """
    original = make_record()
    updated = original.model_copy(update={"tool": "other"})
    assert updated.tool == "other"
    assert original.tool == "search_notes"
    assert updated is not original


def test_model_copy_is_shallow_and_deep_copy_is_independent():
    """Model copy is shallow and deep copy is independent.

    What:     model_copy shares nested objects with the original, and deep=True does not.
    Why:      Knowing which copy shares data tells us when a copy is safe to modify.
    Expected: The shallow copy's result is the same object, and the deep copy's result is equal but
              a different object.
    """
    original = make_record(result=[{"type": "text", "text": "a"}])
    shallow = original.model_copy()
    deep = original.model_copy(deep=True)
    assert shallow.result is original.result
    assert deep.result == original.result
    assert deep.result is not original.result


def test_model_copy_update_skips_validation():
    """Model copy update skips validation.

    What:     GOTCHA: model_copy(update=...) does not validate the new values.
    Why:      Code that cleans records must rebuild them with model_validate, as normalize() does,
              and not trust model_copy.
    Expected: An invalid latency passes through model_copy without an error, while the constructor
              rejects it.
    """
    bad = make_record().model_copy(update={"latency_ms": "not a number"})
    assert bad.latency_ms == "not a number"
    with pytest.raises(ValidationError):
        make_record(latency_ms="not a number")


def test_deepcopy_and_pickle_preserve_equality():
    """Deepcopy and pickle preserve equality.

    What:     Records survive deepcopy and pickle unchanged.
    Why:      Records may be copied for safe editing or sent between processes.
    Expected: The deep copy and the unpickled record both equal the original.
    """
    record = make_record(result=[{"type": "text", "text": "hi"}], is_error=True)
    assert copy.deepcopy(record) == record
    assert pickle.loads(pickle.dumps(record)) == record


# --- Serialization (JSONL safety) ----------------------------------------------

def test_json_round_trip():
    """Json round trip.

    What:     A record survives being written to JSON and read back.
    Why:      Saved sessions are only useful if loading gives back exactly what was saved.
    Expected: The record read back from JSON equals the original.
    """
    record = make_record(
        arguments={"query": "meeting"},
        result=[{"type": "text", "text": "hi"}],
        is_error=True,
    )
    restored = CallRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_json_is_single_line_even_with_newlines_in_result():
    """Json is single line even with newlines in result.

    What:     One record is always one line of JSON.
    Why:      The JSONL session format stores one record per line, so a raw newline would split a
              record in two.
    Expected: The JSON string contains no raw newline or carriage return, and parsing it gives the
              original text.
    """
    record = make_record(result="line1\nline2\r\nline3")
    dumped = record.model_dump_json()
    assert "\n" not in dumped
    assert "\r" not in dumped
    assert json.loads(dumped)["result"] == "line1\nline2\r\nline3"


def test_json_field_order_and_determinism():
    """Json field order and determinism.

    What:     JSON output is stable: the same record gives the same string, with keys in field
              order.
    Why:      Stable output keeps diffs of saved sessions readable in git.
    Expected: Dumping twice gives identical strings, and the keys appear in field order.
    """
    record = make_record(arguments={"b": 1, "a": 2})
    first, second = record.model_dump_json(), record.model_dump_json()
    assert first == second
    assert list(json.loads(first)) == list(CallRecord.model_fields)


def test_json_round_trip_unicode_and_large_payload():
    """Json round trip unicode and large payload.

    What:     Unicode names and a result of about 1 MB survive a JSON round trip.
    Why:      Real servers return non-English text and large payloads.
    Expected: The restored record equals the original.
    """
    record = make_record(tool="búsqueda_📝_検索", result="é" * 1_000_000)
    restored = CallRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_json_round_trip_deeply_nested_result():
    """Json round trip deeply nested result.

    What:     A result nested 10 levels deep round-trips through JSON.
    Why:      Servers can return nested data. Very deep nesting (50+ levels) hits pydantic's
              recursion limit, which varies by version, so this uses a depth real servers could
              plausibly return.
    Expected: The restored record equals the original.
    """
    nested = "leaf"
    for _ in range(10):
        nested = {"child": [nested]}
    record = make_record(result=nested)
    assert CallRecord.model_validate_json(record.model_dump_json()) == record


def test_non_serializable_result_fails_at_dump_time_not_construction():
    """Non serializable result fails at dump time not construction.

    What:     A non-JSON result is only caught when the record is written to JSON.
    Why:      result accepts any value, so the check cannot happen earlier. This documents where the
              error appears.
    Expected: Building the record works, and model_dump_json() raises a PydanticSerializationError.
    """
    record = make_record(result=object())
    with pytest.raises(PydanticSerializationError):
        record.model_dump_json()


# --- Type coercion and strange inputs ------------------------------------------

def test_is_error_truthy_values_coerce_to_true():
    """Is error truthy values coerce to true.

    What:     Common truthy values are converted to True.
    Why:      Servers and files can report the flag in different forms.
    Expected: is_error is True for True, 1, "true", "True", "yes" and "1".
    """
    for value in [True, 1, "true", "True", "yes", "1"]:
        assert make_record(is_error=value).is_error is True


def test_is_error_rejects_non_boolean_looking_values():
    """Is error rejects non boolean looking values.

    What:     Values that do not look like booleans are rejected.
    Why:      A wrong value should fail loudly and not be guessed.
    Expected: "maybe", [], {} and None raise a ValidationError.
    """
    for value in ["maybe", [], {}, None]:
        with pytest.raises(ValidationError):
            make_record(is_error=value)


def test_extra_fields_are_rejected():
    """Extra fields are rejected.

    What:     Unknown fields are rejected.
    Why:      A typo in a field name should fail, not be silently dropped.
    Expected: Passing surprise=1 raises a ValidationError.
    """
    with pytest.raises(ValidationError):
        make_record(surprise=1)


# --- Latency edge cases --------------------------------------------------------

def test_latency_boundaries_are_accepted():
    """Latency boundaries are accepted.

    What:     Zero, tiny and enormous latencies are valid.
    Why:      The valid range must not be cut off at the edges.
    Expected: 0, 0.0001 and 1e12 are stored as floats equal to the input.
    """
    for value in [0, 0.0001, 1e12]:
        assert make_record(latency_ms=value).latency_ms == float(value)


def test_negative_latency_is_rejected():
    """Negative latency is rejected.

    What:     A duration cannot be negative.
    Why:      A negative latency means a bug in the timing code, and it should not be saved.
    Expected: latency_ms=-1 and latency_ms=-0.001 raise a ValidationError.
    """
    for bad in (-1, -0.001):
        with pytest.raises(ValidationError):
            make_record(latency_ms=bad)


def test_nan_and_infinite_latency_are_rejected():
    """Nan and infinite latency are rejected.

    What:     NaN and infinity are rejected as a latency.
    Why:      They make no sense as a duration and are not valid JSON.
    Expected: latency_ms=nan, inf and -inf all raise a ValidationError.
    """
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            make_record(latency_ms=bad)


# --- The .text helper ----------------------------------------------------------

def test_text_from_plain_string():
    """Text from plain string.

    What:     A plain string result is returned as it is by .text.
    Why:      Some servers return a plain string, not content blocks.
    Expected: result="hello" gives .text == "hello".
    """
    assert make_record(result="hello").text == "hello"


def test_text_joins_text_blocks_and_skips_others():
    """Text joins text blocks and skips others.

    What:     Text blocks are joined with newlines, and other blocks are skipped.
    Why:      A result can mix text with images, and only the text should be read.
    Expected: A text "a", an image and a text "b" give .text == "a" + newline + "b".
    """
    result = [
        {"type": "text", "text": "a"},
        {"type": "image", "data": "...", "mimeType": "image/png"},
        {"type": "text", "text": "b"},
    ]
    assert make_record(result=result).text == "a\nb"


def test_text_is_empty_for_none():
    """Text is empty for none.

    What:     A None result gives an empty string.
    Why:      A call can return nothing, and reading .text must not crash.
    Expected: result=None gives .text == "".
    """
    assert make_record(result=None).text == ""


def test_text_is_empty_for_empty_list():
    """Text is empty for empty list.

    What:     An empty list of blocks gives an empty string.
    Why:      A server can return no content blocks.
    Expected: result=[] gives .text == "".
    """
    assert make_record(result=[]).text == ""


def test_text_is_empty_for_unsupported_result_type():
    """Text is empty for unsupported result type.

    What:     A result that is neither a string nor a list gives an empty string.
    Why:      Any other type has no text to read, and .text must not crash.
    Expected: result=123 gives .text == "".
    """
    assert make_record(result=123).text == ""


def test_text_ignores_blocks_without_text_key():
    """Text ignores blocks without text key.

    What:     A text block with no 'text' key is skipped.
    Why:      Malformed blocks should not break reading the good ones.
    Expected: A text block without text, followed by "kept", gives .text == "kept".
    """
    result = [{"type": "text"}, {"type": "text", "text": "kept"}]
    assert make_record(result=result).text == "kept"


def test_text_ignores_non_dict_items():
    """Text ignores non dict items.

    What:     Items in the list that are not dicts are ignored.
    Why:      Malformed content should not crash .text.
    Expected: The list ["stray string", 42, text block "kept"] gives .text == "kept".
    """
    result = ["stray string", 42, {"type": "text", "text": "kept"}]
    assert make_record(result=result).text == "kept"


def test_text_survives_non_string_text_values():
    """Text survives non string text values.

    What:     A block whose 'text' is not a string does not crash .text.
    Why:      Servers can send malformed blocks, and one bad block must not hide the good ones.
    Expected: Blocks whose text is None, a number, a bool, a list or a dict are skipped, and .text
              == "kept".
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
    """Text is repeatable and does not mutate result.

    What:     Reading .text twice gives the same value and leaves result untouched.
    Why:      A read-only helper must never change the data it reads.
    Expected: Both calls match, and result still equals its original value.
    """
    result = [{"type": "text", "text": "a"}, {"type": "image"}]
    snapshot = copy.deepcopy(result)
    record = make_record(result=result)
    assert record.text == record.text == "a"
    assert record.result == snapshot


def test_text_handles_ten_thousand_blocks():
    """Text handles ten thousand blocks.

    What:     A huge list of blocks is joined correctly.
    Why:      Large results are realistic, and joining must stay correct at scale.
    Expected: The text has 10,000 lines, and the first and last lines are right.
    """
    result = [{"type": "text", "text": f"line{i}"} for i in range(10_000)]
    lines = make_record(result=result).text.split("\n")
    assert len(lines) == 10_000
    assert lines[0] == "line0"
    assert lines[-1] == "line9999"


# --- Concurrency ---------------------------------------------------------------

def test_records_are_safe_to_share_across_threads():
    """Records are safe to share across threads.

    What:     Many threads reading one record all see the same data.
    Why:      Runners may read records from several threads at once.
    Expected: All 200 concurrent reads of .text return "a" + newline + "b".
    """
    record = make_record(
        result=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    )
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: record.text, range(200)))
    assert results == ["a\nb"] * 200
