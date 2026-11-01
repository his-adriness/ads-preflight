"""Tests for checking a tool call's result against a test's expectations.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

from types import SimpleNamespace

import anyio
import pytest

from ads_preflight.assertions import Failure, evaluate, same_json
from ads_preflight.client import ServerSession
from ads_preflight.models import CallRecord
from ads_preflight.testfile import ExpectSpec


def record(**overrides):
    data = {
        "tool": "t",
        "arguments": {},
        "result": [{"type": "text", "text": "hello world"}],
        "latency_ms": 10.0,
    }
    data.update(overrides)
    return CallRecord(**data)


def checks(expect, call=None):
    """The names of the expectations that failed."""
    return [failure.check for failure in evaluate(expect, call or record())]


# --- is_error ---------------------------------------------------------------------------------


def test_a_successful_call_passes_the_default_expectation():
    """Check a call that succeeded against an empty expectation.

    What:     No failures.
    Why:      The default is "must succeed".
    Expected: [].
    """
    assert evaluate(ExpectSpec(), record()) == []


def test_a_failed_call_fails_the_default_expectation():
    """Check a call that reported an error against an empty expectation.

    What:     One failure on is_error.
    Why:      A passing test must not hide a tool error.
    Expected: ["is_error"], with a message that says what happened.
    """
    failures = evaluate(ExpectSpec(), record(is_error=True))
    assert [f.check for f in failures] == ["is_error"]
    assert "expected the call to succeed, but it failed" in failures[0].message


def test_an_expected_error_passes_and_a_missing_one_fails():
    """Expect an error, with and without one.

    What:     Passes when the call failed; fails when it succeeded.
    Why:      Error handling is worth testing.
    Expected: [] and ["is_error"].
    """
    expect = ExpectSpec(is_error=True)
    assert checks(expect, record(is_error=True)) == []
    assert checks(expect) == ["is_error"]


# --- Text -----------------------------------------------------------------------------------------


def test_text_contains_needs_every_piece():
    """Expect two pieces of text when only one is there.

    What:     One failure, for the missing piece.
    Why:      All pieces listed must be present.
    Expected: One text_contains failure naming "absent".
    """
    failures = evaluate(ExpectSpec(text_contains=("hello", "absent")), record())
    assert [f.check for f in failures] == ["text_contains"]
    assert "absent" in failures[0].message


def test_text_not_contains_fails_when_the_text_is_there():
    """Forbid text that appears.

    What:     One failure.
    Why:      Used to check that a secret or an error phrase is not returned.
    Expected: ["text_not_contains"].
    """
    assert checks(ExpectSpec(text_not_contains=("world",))) == ["text_not_contains"]
    assert checks(ExpectSpec(text_not_contains=("absent",))) == []


def test_text_equals_is_exact():
    """Compare the whole text.

    What:     Passes only when equal, including case and spacing.
    Why:      "Equals" must not mean "close to".
    Expected: [] for the exact text, a failure for a different case.
    """
    assert checks(ExpectSpec(text_equals="hello world")) == []
    assert checks(ExpectSpec(text_equals="Hello world")) == ["text_equals"]


def test_text_joins_all_text_blocks():
    """Check a result with two text blocks.

    What:     The blocks are joined with a newline before comparing.
    Why:      Tools often return several blocks.
    Expected: text_equals "a\\nb" passes.
    """
    result = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert checks(ExpectSpec(text_equals="a\nb"), record(result=result)) == []


def test_long_text_is_cut_in_messages():
    """Fail a check against a very long text.

    What:     The message holds only the start of it, then "...".
    Why:      A server can return megabytes; a report must stay readable.
    Expected: A message under 400 characters.
    """
    call = record(result=[{"type": "text", "text": "x" * 100_000}])
    failure = evaluate(ExpectSpec(text_equals="y"), call)[0]
    assert len(failure.message) < 400 and "..." in failure.message


def test_control_characters_are_escaped_in_messages():
    """Fail a check against text with an escape sequence.

    What:     The message shows the escape as characters, not as a control code.
    Why:      The text comes from the server and is printed to a terminal.
    Expected: No ESC byte in the message.
    """
    call = record(result=[{"type": "text", "text": "\x1b[31mred"}])
    failure = evaluate(ExpectSpec(text_equals="y"), call)[0]
    assert "\x1b" not in failure.message


# --- Structured content ---------------------------------------------------------------------------


def test_structured_equals_compares_the_structured_result():
    """Compare structured content.

    What:     Passes when equal and fails when different.
    Why:      This is the exact-data check.
    Expected: [] and ["structured_equals"].
    """
    call = record(structured_content={"count": 2, "items": ["a", "b"]})
    assert checks(ExpectSpec(structured_equals={"count": 2, "items": ["a", "b"]}), call) == []
    assert checks(ExpectSpec(structured_equals={"count": 3, "items": ["a", "b"]}), call) == [
        "structured_equals"
    ]


def test_structured_equals_fails_when_the_server_sent_none():
    """Expect structured content from a server that sent only text.

    What:     A failure that says so.
    Why:      The message should say "sent none", not show a confusing difference.
    Expected: "sent no structured content".
    """
    failure = evaluate(ExpectSpec(structured_equals={"a": 1}), record())[0]
    assert "no structured content" in failure.message


def test_structured_equals_is_not_checked_when_left_out():
    """Leave structured_equals out while the server sends structured content.

    What:     No failure.
    Why:      Only the checks written in the file are made.
    Expected: [].
    """
    assert checks(ExpectSpec(), record(structured_content={"a": 1})) == []


@pytest.mark.parametrize(
    ("left", "right", "equal"),
    [
        (1, 1.0, True),
        (True, 1, False),
        (False, 0, False),
        (True, True, True),
        ("1", 1, False),
        (None, 0, False),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True),
        ({"a": 1}, {"a": 1, "b": 2}, False),
        ([1, 2], [2, 1], False),
        ([1, [2, {"x": True}]], [1, [2, {"x": True}]], True),
        ([1, [2, {"x": True}]], [1, [2, {"x": 1}]], False),
        ({"a": 1}, [("a", 1)], False),
        ("a", "a", True),
        ("a", "b", False),
    ],
)
def test_json_equality_is_strict_about_types(left, right, equal):
    """Compare JSON-style values.

    What:     Numbers match as in JSON; booleans, strings and null never match another type.
    Why:      Python's `True == 1` would let a wrong result pass.
    Expected: The stated answer, in both directions.
    """
    assert same_json(left, right) is equal
    assert same_json(right, left) is equal


# --- Latency --------------------------------------------------------------------------------------


def test_latency_over_the_limit_fails():
    """Check a slow call against max_latency_ms.

    What:     Fails above the limit, passes at it.
    Why:      "At most" includes the limit.
    Expected: [] at 100 ms, a failure at 100.1 ms.
    """
    expect = ExpectSpec(max_latency_ms=100)
    assert checks(expect, record(latency_ms=100.0)) == []
    failures = evaluate(expect, record(latency_ms=100.1))
    assert [f.check for f in failures] == ["max_latency_ms"]
    assert "100 ms" in failures[0].message


def test_every_failure_is_reported_together():
    """Break three expectations at once.

    What:     Three failures, in one list.
    Why:      One run should show everything that is wrong.
    Expected: is_error, text_contains and max_latency_ms.
    """
    expect = ExpectSpec(text_contains=("nope",), max_latency_ms=1)
    assert checks(expect, record(is_error=True, latency_ms=50.0)) == [
        "is_error",
        "text_contains",
        "max_latency_ms",
    ]


def test_a_failure_is_a_plain_value():
    """Build a Failure by hand.

    What:     It holds a check name and a message and compares by value.
    Why:      Reports and tests rely on that.
    Expected: Two equal Failures are equal.
    """
    assert Failure("a", "b") == Failure("a", "b")


# --- Where structured content comes from ---------------------------------------------------------


def test_the_client_keeps_the_structured_content():
    """Call a tool through a session whose fake client returns structured content.

    What:     The record holds it. A result without the attribute gives None.
    Why:      structured_equals can only work if the client records it.
    Expected: {"count": 1}, then None.
    """

    class Block:
        def model_dump(self, by_alias=False, exclude_none=False):
            return {"type": "text", "text": "ok"}

    class Client:
        def __init__(self, result):
            self.result = result

        async def call_tool(self, name, arguments):
            return self.result

    def call(result):
        session = ServerSession(Client(result), allowed_tools=["t"])
        return anyio.run(lambda: session.call_tool("t", {}))

    with_structure = SimpleNamespace(
        content=[Block()], is_error=False, structured_content={"count": 1}
    )
    without = SimpleNamespace(content=[Block()], is_error=False)
    assert call(with_structure).structured_content == {"count": 1}
    assert call(without).structured_content is None
