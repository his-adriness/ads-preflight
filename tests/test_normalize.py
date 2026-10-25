"""Tests for scrub().

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import copy
import re

import pytest

from mcpdoctor.normalize import parse_path, scrub

DIGITS = [(re.compile(r"\d+"), "<N>")]


# --- Basic string behavior --------------------------------------------------


def test_scrub_replaces_a_match_in_a_string():
    """Replace matching text inside a string.

    What:     A part of a string that matches a pattern is swapped for the
              replacement text.
    Why:      This is the core job of scrub(). If it fails, nothing else works.
    Expected: scrub("order 123", DIGITS) == "order <N>"
    """
    assert scrub("order 123", DIGITS) == "order <N>"


def test_scrub_replaces_every_occurrence_in_a_string():
    """Replace all matches in one string, not just the first.

    What:     A string containing several matches has every one replaced.
    Why:      Guards against a version that stops after the first match (for
              example by using count=1), which would leave volatile values behind.
    Expected: scrub("1 and 22 and 333", DIGITS) == "<N> and <N> and <N>"
    """
    assert scrub("1 and 22 and 333", DIGITS) == "<N> and <N> and <N>"


def test_scrub_leaves_non_matching_strings_alone():
    """Return strings with no match unchanged.

    What:     A string that contains nothing to scrub comes back as it was.
    Why:      Guards against code that alters or damages text it shouldn't touch.
    Expected: scrub("hello", DIGITS) == "hello"
    """
    assert scrub("hello", DIGITS) == "hello"


def test_scrub_with_no_scrubbers_changes_nothing():
    """Treat an empty scrubber list as a no-op.

    What:     With no scrubbers, nested data comes back identical.
    Why:      "Nothing to apply" is a valid input and must not crash or alter data.
    Expected: scrub({"a": ["x1", {"b": "y2"}], "n": 3}, []) returns an equal value.
    """
    data = {"a": ["x1", {"b": "y2"}], "n": 3}
    assert scrub(data, []) == data


def test_scrub_applies_scrubbers_in_order():
    """Run scrubbers one after another, in list order.

    What:     A later scrubber sees the output of the earlier ones.
    Why:      Order matters when scrubbers overlap. If the list were reordered or
              run in parallel, results would change.
    Expected: "abc" -> "xbc" (a becomes x) -> "xyc" (xb becomes xy). In the reverse
              order the second pattern would never match.
    """
    scrubbers = [(re.compile("a"), "x"), (re.compile("xb"), "xy")]
    assert scrub("abc", scrubbers) == "xyc"


def test_scrub_supports_backreferences_in_the_replacement():
    """Allow the replacement to reuse captured groups.

    What:     A replacement such as \\1 inserts the text captured by the pattern.
    Why:      Confirms scrub() uses real regex substitution (pattern.sub), not a
              plain text replace.
    Expected: scrub("user=bob", ...) == "user=<redacted>"
    """
    scrubbers = [(re.compile(r"(user)=\w+"), r"\1=<redacted>")]
    assert scrub("user=bob", scrubbers) == "user=<redacted>"


# --- Walking nested data ----------------------------------------------------


def test_scrub_walks_dict_values_but_not_keys():
    """Scrub dict values and leave keys alone.

    What:     Only the values of a dict are changed. Keys stay exactly as they are.
    Why:      Keys are structure (field names). Changing them would break lookups
              and make results unreadable.
    Expected: scrub({"id1": "a1"}, DIGITS) == {"id1": "a<N>"}
    """
    assert scrub({"id1": "a1"}, DIGITS) == {"id1": "a<N>"}


def test_scrub_walks_lists():
    """Scrub every item of a list.

    What:     Each string in a list is scrubbed.
    Why:      Guards against code that handles only the first item or skips lists.
              MCP results are lists of content blocks, so this matters.
    Expected: scrub(["a1", "b2"], DIGITS) == ["a<N>", "b<N>"]
    """
    assert scrub(["a1", "b2"], DIGITS) == ["a<N>", "b<N>"]


def test_scrub_walks_mixed_nesting():
    """Reach strings at any depth in mixed dicts and lists.

    What:     Dicts inside lists inside dicts are all scrubbed.
    Why:      Confirms the recursion works for every combination, not just one level.
    Expected: Every string at every depth has its digits replaced by <N>.
    """
    data = {"items": [{"name": "n1", "tags": ["t2", "t3"]}], "meta": {"id": "i4"}}
    assert scrub(data, DIGITS) == {
        "items": [{"name": "n<N>", "tags": ["t<N>", "t<N>"]}],
        "meta": {"id": "i<N>"},
    }


def test_scrub_leaves_non_string_values_unchanged():
    """Return numbers, booleans and None as they are.

    What:     Non-string values pass through with the same value and type.
    Why:      Guards against turning 42 into "42", or damaging booleans, which would
              change the meaning of the data.
    Expected: 42, 1.5 and None come back equal. True and False come back as booleans.
    """
    for value in (42, 1.5, None):
        assert scrub(value, DIGITS) == value
    assert scrub(True, DIGITS) is True
    assert scrub(False, DIGITS) is False


def test_scrub_handles_empty_values():
    """Accept empty strings and containers.

    What:     "", [] and {} are valid inputs.
    Why:      Empty values are a classic edge case that crashes code assuming
              there is content to loop over.
    Expected: "" -> "", [] -> [], {} -> {}
    """
    assert scrub("", DIGITS) == ""
    assert scrub([], DIGITS) == []
    assert scrub({}, DIGITS) == {}


def test_scrub_passes_tuples_and_sets_through_unchanged():
    """Document that only str, dict and list are walked.

    What:     Tuples and sets are returned as they are, without scrubbing.
    Why:      Records the current limitation, so a change in behavior is noticed.
              JSON data never contains these types, so it is acceptable for now.
    Expected: scrub(("a1",), DIGITS) == ("a1",) and scrub({"a1"}, DIGITS) == {"a1"}
    """
    assert scrub(("a1",), DIGITS) == ("a1",)
    assert scrub({"a1"}, DIGITS) == {"a1"}


# --- Safety guarantees ------------------------------------------------------


def test_scrub_does_not_modify_its_input():
    """Leave the original data untouched.

    What:     After scrubbing, the input still equals a copy taken before the call.
    Why:      Records and snapshots depend on the original staying intact.
              Changing data in place would cause hard-to-trace bugs.
    Expected: data == snapshot (a deep copy made before scrubbing)
    """
    data = {"items": [{"id": "x1"}], "n": "y2"}
    snapshot = copy.deepcopy(data)
    scrub(data, DIGITS)
    assert data == snapshot


def test_scrub_returns_new_containers():
    """Return new dicts and lists, not the originals.

    What:     The result is a different object from the input at every level.
    Why:      Guards against returning the input as it is, which would let later
              edits to the result silently change the original.
    Expected: out is not data, and out["a"] is not data["a"]
    """
    data = {"a": ["x1"]}
    out = scrub(data, DIGITS)
    assert out is not data
    assert out["a"] is not data["a"]


def test_scrub_preserves_dict_key_order():
    """Keep keys in their original order.

    What:     Keys come out in the order they went in.
    Why:      Snapshots and JSON diffs depend on stable ordering. Rebuilding dicts
              carelessly (for example from a set) can scramble it.
    Expected: list(result) == ["z", "a", "m"]
    """
    assert list(scrub({"z": "1", "a": "2", "m": "3"}, DIGITS)) == ["z", "a", "m"]


# --- Stress -----------------------------------------------------------------


def test_scrub_handles_deep_nesting_and_large_lists():
    """Cope with deep and large data.

    What:     200 levels of nesting and a 10,000-item list are scrubbed correctly.
    Why:      Confirms the recursion does not hit Python's recursion limit and that
              large results do not break or crawl.
    Expected: The deepest string becomes "leaf<N>" and the last list item becomes
              "item<N>".
    """
    nested = "leaf1"
    for _ in range(200):
        nested = {"child": nested}
    out = scrub(nested, DIGITS)
    for _ in range(200):
        out = out["child"]
    assert out == "leaf<N>"

    big = [f"item{i}" for i in range(10_000)]
    assert scrub(big, DIGITS)[-1] == "item<N>"


# --- parse_path: valid paths ------------------------------------------------


def test_parse_path_single_key():
    """Parse the simplest path.

    What:     A path with one key becomes one "key" step.
    Why:      The baseline case. If this fails, every other path fails too.
    Expected: parse_path("$.request_id") == [("key", "request_id")]
    """
    assert parse_path("$.request_id") == [("key", "request_id")]


def test_parse_path_nested_keys():
    """Parse keys separated by dots.

    What:     Each key becomes its own step, in order.
    Why:      Guards against steps being merged, dropped or reordered.
    Expected: parse_path("$.a.b.c") == [("key", "a"), ("key", "b"), ("key", "c")]
    """
    assert parse_path("$.a.b.c") == [("key", "a"), ("key", "b"), ("key", "c")]


def test_parse_path_wildcard_over_list_items():
    """Parse [*] as "every item of a list".

    What:     [*] becomes an "all" step between two keys.
    Why:      This is the form used for lists of records, like
              $.items[*].updated_at, so it is the most important one.
    Expected: [("key", "items"), ("all", None), ("key", "updated_at")]
    """
    assert parse_path("$.items[*].updated_at") == [
        ("key", "items"),
        ("all", None),
        ("key", "updated_at"),
    ]


def test_parse_path_list_index():
    """Parse [0] as "the item at position 0".

    What:     [n] becomes an "index" step holding the number as an int.
    Why:      Guards against keeping the index as a string, which would never
              match a list position.
    Expected: [("key", "items"), ("index", 0), ("key", "id")]
    """
    assert parse_path("$.items[0].id") == [("key", "items"), ("index", 0), ("key", "id")]


def test_parse_path_multi_digit_index():
    """Parse indexes with more than one digit.

    What:     [12] is read as the number 12, not as 1 and 2.
    Why:      Guards against a pattern that only matches a single digit.
    Expected: parse_path("$.a[12].b") == [("key", "a"), ("index", 12), ("key", "b")]
    """
    assert parse_path("$.a[12].b") == [("key", "a"), ("index", 12), ("key", "b")]


def test_parse_path_wildcard_directly_on_the_root():
    """Parse a path that starts by walking a top-level list.

    What:     [*] may follow $ directly, for results that are lists.
    Why:      MCP results are lists of content blocks, so paths often start here.
    Expected: parse_path("$[*].x") == [("all", None), ("key", "x")]
    """
    assert parse_path("$[*].x") == [("all", None), ("key", "x")]


def test_parse_path_accepts_hyphens_underscores_and_digits_in_keys():
    """Allow common characters in key names.

    What:     Keys such as my-key_2 are read whole.
    Why:      Real field names are not always plain letters.
    Expected: parse_path("$.my-key_2.x") == [("key", "my-key_2"), ("key", "x")]
    """
    assert parse_path("$.my-key_2.x") == [("key", "my-key_2"), ("key", "x")]


# --- parse_path: invalid paths ----------------------------------------------


def test_parse_path_must_start_with_a_dollar_sign():
    """Reject paths that do not start at the root.

    What:     "items" and "" are errors, not guesses.
    Why:      A missing $ is almost always a typo. Failing loudly beats silently
              ignoring a path and leaving a volatile field in place.
    Expected: ValueError mentioning that the path must start with '$'.
    """
    with pytest.raises(ValueError, match="must start with"):
        parse_path("items")
    with pytest.raises(ValueError, match="must start with"):
        parse_path("")


@pytest.mark.parametrize("path", ["$", "$.a[*]", "$.a[0]"])
def test_parse_path_must_end_with_a_key(path):
    """Reject paths whose last step is not a key.

    What:     "$", "$.a[*]" and "$.a[0]" are errors.
    Why:      Only dict keys can be removed. Removing list items would shift the
              positions of the others and give surprising results.
    Expected: ValueError mentioning that the path must end with a key.
    """
    with pytest.raises(ValueError, match="must end with a key"):
        parse_path(path)


@pytest.mark.parametrize("path", ["$.a..b", "$.a[x]", "$.a[", "$.a[*", "$.", "$.a[-1]"])
def test_parse_path_rejects_malformed_paths(path):
    """Reject paths with broken syntax.

    What:     Double dots, non-numeric or negative indexes, unclosed brackets and a
              trailing dot are errors.
    Why:      A half-understood path must never be accepted, because it would
              silently ignore the field the user meant to remove.
    Expected: ValueError mentioning that the path is invalid.
    """
    with pytest.raises(ValueError, match="invalid path"):
        parse_path(path)


def test_parse_path_error_points_at_the_problem():
    """Include the path and the position in the error message.

    What:     The message shows the bad path and where parsing stopped.
    Why:      A user with a long path list needs to know which one is wrong, and
              where.
    Expected: The ValueError message contains '$.a..b' and 'position 3'.
    """
    with pytest.raises(ValueError) as error:
        parse_path("$.a..b")
    assert "$.a..b" in str(error.value)
    assert "position 3" in str(error.value)