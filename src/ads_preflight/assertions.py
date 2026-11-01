"""Checks of a tool call's result against what a test expects.

`evaluate` compares one `CallRecord` with an `ExpectSpec` and returns what did not match.
The text of a result comes from the server being checked, so a reporter must pass these
messages through `clean_text` before printing them.
"""

from dataclasses import dataclass
from typing import Any

from ads_preflight.models import CallRecord
from ads_preflight.testfile import ExpectSpec

EXCERPT_CHARS = 120


@dataclass(frozen=True)
class Failure:
    """One expectation that did not hold."""

    check: str  # The name of the expectation, as written in the test file.
    message: str  # What was expected and what happened, ready to show.


def evaluate(expect: ExpectSpec, record: CallRecord) -> list[Failure]:
    """Return every expectation of `expect` that `record` does not meet. Empty means a pass.

    All expectations are checked, so one report shows everything that is wrong.
    """
    failures: list[Failure] = []
    if record.is_error != expect.is_error:
        wanted = "to fail" if expect.is_error else "to succeed"
        got = "failed" if record.is_error else "succeeded"
        failures.append(Failure("is_error", f"expected the call {wanted}, but it {got}"))
    text = record.text
    for wanted_text in expect.text_contains:
        if wanted_text not in text:
            failures.append(
                Failure("text_contains", f"the text does not contain {_excerpt(wanted_text)}")
            )
    for unwanted in expect.text_not_contains:
        if unwanted in text:
            failures.append(
                Failure("text_not_contains", f"the text contains {_excerpt(unwanted)}")
            )
    if expect.text_equals is not None and text != expect.text_equals:
        failures.append(
            Failure(
                "text_equals",
                f"expected the text {_excerpt(expect.text_equals)} but got {_excerpt(text)}",
            )
        )
    if "structured_equals" in expect.model_fields_set:
        if record.structured_content is None:
            failures.append(
                Failure("structured_equals", "the server sent no structured content")
            )
        elif not same_json(record.structured_content, expect.structured_equals):
            failures.append(
                Failure(
                    "structured_equals",
                    f"expected {_excerpt(repr(expect.structured_equals))} but got "
                    f"{_excerpt(repr(record.structured_content))}",
                )
            )
    if expect.max_latency_ms is not None and record.latency_ms > expect.max_latency_ms:
        failures.append(
            Failure(
                "max_latency_ms",
                f"the call took {record.latency_ms:.0f} ms, more than the {expect.max_latency_ms:g}"
                " ms allowed",
            )
        )
    return failures


def same_json(left: Any, right: Any) -> bool:
    """True if two JSON-style values are equal.

    Stricter than `==`: `True` is not `1`, and `1` equals `1.0` as in JSON. Objects must have
    the same keys, and lists the same items in the same order.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_json(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict | list) or isinstance(right, dict | list):
        return False
    if type(left) is type(right) or {type(left), type(right)} == {int, float}:
        return left == right
    return False


def _excerpt(text: str) -> str:
    """A short, quoted piece of `text` for a message. Escapes show control characters."""
    if len(text) > EXCERPT_CHARS:
        return repr(text[:EXCERPT_CHARS]) + "..."
    return repr(text)
