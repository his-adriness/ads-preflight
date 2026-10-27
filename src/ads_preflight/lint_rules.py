"""The lint rules. Each one looks at a server's tools and reports problems.

A rule never calls the server. It reads `ToolDef` objects, so it works on a saved tools
file as well as on a live server.
"""

import reprlib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from ads_preflight.findings import Severity
from ads_preflight.models import ToolDef

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Keys other APIs use for the input schema. MCP uses `inputSchema`.
WRONG_SCHEMA_KEYS = ("input_schema", "parameters")


class Problem(NamedTuple):
    """What a rule found. The linter adds the rule's ID and severity."""

    tool: str | None
    message: str
    fix: str | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    summary: str
    check: Callable[[Sequence[ToolDef]], Iterable[Problem]]


RULES: list[Rule] = []


def _rule(rule_id: str, severity: Severity, summary: str):
    """Register a check function as a rule."""

    def register(check):
        if any(existing.id == rule_id for existing in RULES):
            raise ValueError(f"duplicate rule ID: {rule_id}")
        RULES.append(Rule(rule_id, severity, summary, check))
        return check

    return register


def _wrong_schema_key(tool: ToolDef) -> str | None:
    extra = tool.model_extra or {}
    return next((key for key in WRONG_SCHEMA_KEYS if key in extra), None)


@_rule("core.empty-tool-list", "warning", "The server lists no tools")
def empty_tool_list(tools):
    if not tools:
        yield Problem(None, "the server lists no tools", "check that this is the right server")


@_rule("core.duplicate-name", "error", "Two tools have the same name")
def duplicate_name(tools):
    for name, count in Counter(tool.name for tool in tools).items():
        if count > 1:
            yield Problem(name, f"listed {count} times", "give every tool a unique name")


@_rule("core.missing-description", "warning", "A tool has no description")
def missing_description(tools):
    for tool in tools:
        if tool.description is None or not tool.description.strip():
            yield Problem(
                tool.name,
                "has no description for the model to read",
                "describe what the tool does and when to use it",
            )


@_rule("core.missing-input-schema", "warning", "A tool has no input schema")
def missing_input_schema(tools):
    for tool in tools:
        # A wrong key is reported by core.wrong-schema-key, so do not repeat it here.
        if tool.inputSchema is None and _wrong_schema_key(tool) is None:
            yield Problem(
                tool.name,
                "has no inputSchema, so the model gets no guidance on its arguments",
                "add an inputSchema, even if it is only {'type': 'object'}",
            )


@_rule("core.wrong-schema-key", "error", "A tool uses another API's name for its input schema")
def wrong_schema_key(tools):
    for tool in tools:
        key = _wrong_schema_key(tool)
        if key:
            yield Problem(
                tool.name,
                f"has '{key}', which MCP clients do not read",
                "rename it to 'inputSchema'",
            )


@_rule("core.hint-not-boolean", "error", "An annotation hint is not true or false")
def hint_not_boolean(tools):
    for tool in tools:
        if tool.annotations is None:
            continue
        for hint in HINTS:
            value = getattr(tool.annotations, hint)
            if value is not None and not isinstance(value, bool):
                yield Problem(
                    tool.name,
                    f"{hint} is {reprlib.repr(value)}, not true or false",
                    "use a real JSON true or false",
                )


@_rule("core.no-access-hint", "warning", "A tool does not say whether it is read-only")
def no_access_hint(tools):
    for tool in tools:
        hints = tool.annotations
        if hints is None or hints.readOnlyHint is None:
            yield Problem(
                tool.name,
                "does not say whether it is read-only (no readOnlyHint)",
                "set annotations.readOnlyHint to true or false",
            )


@_rule("core.hint-conflict", "warning", "A tool is marked both read-only and destructive")
def hint_conflict(tools):
    for tool in tools:
        hints = tool.annotations
        if hints is not None and hints.readOnlyHint is True and hints.destructiveHint is True:
            yield Problem(
                tool.name,
                "is marked read-only and destructive, which contradict each other",
                "a read-only tool cannot destroy anything, so fix one of the two hints",
            )
