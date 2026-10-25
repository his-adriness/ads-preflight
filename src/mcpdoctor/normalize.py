"""Normalize CallRecords so volatile values don't break comparisons."""

import re
from typing import Any

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