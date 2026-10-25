"""Normalize CallRecords so volatile values don't break comparisons."""

import json
import re
from typing import Any

from mcpdoctor.models import CallRecord

Scrubber = tuple[re.Pattern[str], str]  # (pattern to find, replacement text)

DEFAULT_SCRUBBERS: list[Scrubber] = [
    (
        re.compile(
            r"(?<![0-9a-f])"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            r"(?![0-9a-f])",
            re.IGNORECASE,
        ),
        "<UUID>",
    ),
    (
        re.compile(
            r"(?<!\d)"
            r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?"
            r"(?!\d)"
        ),
        "<TIMESTAMP>",
    ),
]


def scrub(value: Any, scrubbers: list[Scrubber]) -> Any:
    """Return a copy of value with every matching string replaced.

    Walks dicts and lists at any depth and never modifies the input.
    """
    if isinstance(value, str):
        for pattern, replacement in scrubbers:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {key: scrub(item, scrubbers) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, scrubbers) for item in value]
    return value

# --- Ignore paths -----------------------------------------------------------

_Token = tuple[str, Any]  # ("key", name) | ("all", None) | ("index", n)

_TOKEN_RE = re.compile(r"\.(?P<key>[^.\[\]]+)|\[(?P<star>\*)\]|\[(?P<index>\d+)\]")


def parse_path(path: str) -> list[_Token]:
    """Parse a path like '$.items[*].updated_at' into tokens.

    Supports: '.key', '[*]' (every list item) and '[0]' (one list item).
    The last step must be a key, because only dict keys can be removed.
    """
    if not path.startswith("$"):
        raise ValueError(f"path must start with '$': {path!r}")
    tokens: list[_Token] = []
    pos = 1
    while pos < len(path):
        match = _TOKEN_RE.match(path, pos)
        if match is None:
            raise ValueError(f"invalid path {path!r} at position {pos}")
        if match.group("key") is not None:
            tokens.append(("key", match.group("key")))
        elif match.group("star") is not None:
            tokens.append(("all", None))
        else:
            tokens.append(("index", int(match.group("index"))))
        pos = match.end()
    if not tokens or tokens[-1][0] != "key":
        raise ValueError(f"path must end with a key: {path!r}")
    return tokens

def _remove(value: Any, tokens: list[_Token]) -> Any:
    kind, arg = tokens[0]
    rest = tokens[1:]
    if kind == "key":
        if not isinstance(value, dict) or arg not in value:
            return value
        if not rest:
            return {k: v for k, v in value.items() if k != arg}
        return {**value, arg: _remove(value[arg], rest)}
    if not isinstance(value, list):
        return value
    if kind == "all":
        return [_remove(item, rest) for item in value]
    # kind == "index"
    return [_remove(item, rest) if i == arg else item for i, item in enumerate(value)]


def remove_paths(value: Any, paths: list[str]) -> Any:
    """Return a copy of value with every key matching one of the paths removed.

    Paths that match nothing are ignored. The input is never modified.
    """
    for path in paths:
        value = _remove(value, parse_path(path))
    return value

# --- Sorting lists ----------------------------------------------------------


def _sort_key(item: Any) -> str:
    """Canonical text of an item, so any mix of JSON values can be ordered."""
    try:
        return json.dumps(item, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return repr(item)


def _sort_at(value: Any, tokens: list[_Token]) -> Any:
    kind, arg = tokens[0]
    rest = tokens[1:]
    if kind == "key":
        if not isinstance(value, dict) or arg not in value:
            return value
        if not rest:
            if not isinstance(value[arg], list):
                return value
            return {**value, arg: sorted(value[arg], key=_sort_key)}
        return {**value, arg: _sort_at(value[arg], rest)}
    if not isinstance(value, list):
        return value
    if kind == "all":
        return [_sort_at(item, rest) for item in value]
    # kind == "index"
    return [_sort_at(item, rest) if i == arg else item for i, item in enumerate(value)]


def sort_lists(value: Any, paths: list[str]) -> Any:
    """Return a copy of value with the lists at the given paths put in stable order.

    Each path must end with the key that holds the list, like '$.items'.
    Paths that match nothing, or match something that is not a list, are ignored.
    The input is never modified.
    """
    for path in paths:
        value = _sort_at(value, parse_path(path))
    return value


# --- Normalizing a record ---------------------------------------------------


def normalize(
    record: CallRecord,
    *,
    scrubbers: list[Scrubber] | None = None,
    ignore: list[str] | None = None,
    sort: list[str] | None = None,
) -> CallRecord:
    """Return a copy of record whose result is cleaned for fair comparison.

    Steps, in this order: remove the `ignore` paths, scrub volatile strings, then
    sort the lists at the `sort` paths. Sorting is last so that lists are ordered
    by their final, stable content and not by values that change on every run.

    scrubbers: None uses DEFAULT_SCRUBBERS. An empty list turns scrubbing off.
    Only `result` changes. The input record is never modified.
    """
    result = record.result
    result = remove_paths(result, ignore or [])
    result = scrub(result, DEFAULT_SCRUBBERS if scrubbers is None else scrubbers)
    result = sort_lists(result, sort or [])
    return CallRecord.model_validate({**record.model_dump(), "result": result})