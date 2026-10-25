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