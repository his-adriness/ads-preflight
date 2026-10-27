"""Tests for the lint rules.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest

from ads_preflight.lint_rules import RULES
from ads_preflight.models import ToolDef

RULE = {rule.id: rule for rule in RULES}

# A tool that every rule accepts.
GOOD = {
    "name": "list_notes",
    "description": "List the user's notes.",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True},
}


def tools(*specs):
    """Build tools from dicts. A spec can change GOOD: {"description": None}."""
    return [ToolDef.model_validate({**GOOD, **spec}) for spec in specs]


def run(rule_id, *specs):
    """Run one rule over tools built from the specs and return its problems."""
    return list(RULE[rule_id].check(tools(*specs)))


# --- The set of rules ---------------------------------------------------------


def test_every_rule_has_a_unique_namespaced_id():
    """Look at the registered rules.

    What:     IDs are unique and start with "core.".
    Why:      Findings and suppressions refer to rules by ID, and the "ads." rules come later.
    Expected: No duplicates, every ID starts with "core.".
    """
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(rule_id.startswith("core.") for rule_id in ids)


def test_there_are_eight_rules():
    """Count the registered rules.

    What:     The first lint release has eight rules.
    Why:      A rule that fails to register would silently stop being checked.
    Expected: The eight IDs, in order.
    """
    assert [rule.id for rule in RULES] == [
        "core.empty-tool-list",
        "core.duplicate-name",
        "core.missing-description",
        "core.missing-input-schema",
        "core.wrong-schema-key",
        "core.hint-not-boolean",
        "core.no-access-hint",
        "core.hint-conflict",
    ]


def test_severities():
    """Read each rule's severity.

    What:     Three rules are errors and five are warnings.
    Why:      Errors fail a lint run, so the split decides what blocks a build.
    Expected: The errors are the three rules named below.
    """
    errors = {rule.id for rule in RULES if rule.severity == "error"}
    assert errors == {"core.duplicate-name", "core.wrong-schema-key", "core.hint-not-boolean"}
    assert sum(rule.severity == "warning" for rule in RULES) == 5


def test_a_good_tool_passes_every_rule():
    """Run every rule on a well-formed tool.

    What:     A tool with a description, a schema and a boolean readOnlyHint has no problems.
    Why:      A rule that fires on good input would make the linter unusable.
    Expected: No problems from any rule.
    """
    for rule in RULES:
        assert list(rule.check(tools({}))) == [], rule.id


# --- core.empty-tool-list -------------------------------------------------------


def test_empty_tool_list_is_reported_once_for_the_server():
    """Check an empty list of tools.

    What:     One problem, not tied to any tool.
    Why:      It is about the whole server, so there is no tool name to give.
    Expected: One problem with tool None.
    """
    problems = list(RULE["core.empty-tool-list"].check([]))
    assert len(problems) == 1
    assert problems[0].tool is None


def test_non_empty_list_is_not_reported_as_empty():
    """Check a list with one tool.

    What:     No problem.
    Why:      The rule must only fire on an empty list.
    Expected: No problems.
    """
    assert run("core.empty-tool-list", {}) == []


# --- core.duplicate-name --------------------------------------------------------


def test_duplicate_name_is_reported_once_with_the_count():
    """Check three tools where two share a name.

    What:     One problem for the repeated name, with the number of times.
    Why:      Reporting each copy would repeat the same problem.
    Expected: One problem for "a" that says "2 times".
    """
    problems = run("core.duplicate-name", {"name": "a"}, {"name": "b"}, {"name": "a"})
    assert [p.tool for p in problems] == ["a"]
    assert "2 times" in problems[0].message


def test_unique_names_are_not_reported():
    """Check two tools with different names.

    What:     No problem.
    Why:      Names that only look alike are different tools.
    Expected: No problems.
    """
    assert run("core.duplicate-name", {"name": "a"}, {"name": "A"}) == []


# --- core.missing-description ---------------------------------------------------


@pytest.mark.parametrize("description", [None, "", "   ", "\n"])
def test_missing_or_blank_description_is_reported(description):
    """Check a tool whose description is missing or only whitespace.

    What:     Both are reported.
    Why:      A blank description gives the model nothing to choose the tool by.
    Expected: One problem for the tool.
    """
    assert [p.tool for p in run("core.missing-description", {"description": description})] == [
        "list_notes"
    ]


def test_description_present_is_not_reported():
    """Check a tool with a description.

    What:     No problem.
    Why:      Only a missing description is a problem, not a short one.
    Expected: No problems.
    """
    assert run("core.missing-description", {"description": "x"}) == []


# --- core.missing-input-schema --------------------------------------------------


def test_missing_input_schema_is_reported():
    """Check a tool with no inputSchema.

    What:     One problem for the tool.
    Why:      Without a schema the model has to guess the arguments.
    Expected: One problem.
    """
    assert len(run("core.missing-input-schema", {"inputSchema": None})) == 1


def test_empty_input_schema_counts_as_present():
    """Check a tool whose inputSchema is an empty object.

    What:     No problem from this rule.
    Why:      An empty schema is a schema, and its quality is a different question.
    Expected: No problems.
    """
    assert run("core.missing-input-schema", {"inputSchema": {}}) == []


@pytest.mark.parametrize("key", ["input_schema", "parameters"])
def test_missing_input_schema_is_not_repeated_when_the_key_is_wrong(key):
    """Check a tool that has its schema under another API's key.

    What:     This rule stays quiet, because core.wrong-schema-key reports it.
    Why:      Two findings for one mistake would be noise.
    Expected: No problems.
    """
    assert run("core.missing-input-schema", {"inputSchema": None, key: {"type": "object"}}) == []


# --- core.wrong-schema-key ------------------------------------------------------


@pytest.mark.parametrize("key", ["input_schema", "parameters"])
def test_wrong_schema_key_is_reported_and_named(key):
    """Check a tool with the schema under a key MCP does not use.

    What:     The problem names the key and says what to use.
    Why:      The Claude API and OpenAI use these keys, so copying from them is a common slip.
    Expected: One problem that mentions the key and "inputSchema".
    """
    problems = run("core.wrong-schema-key", {key: {"type": "object"}})
    assert len(problems) == 1
    assert key in problems[0].message
    assert "inputSchema" in problems[0].fix


def test_correct_schema_key_is_not_reported():
    """Check a tool with a proper inputSchema.

    What:     No problem.
    Why:      The rule must not fire on the right key.
    Expected: No problems.
    """
    assert run("core.wrong-schema-key", {}) == []


# --- core.hint-not-boolean ------------------------------------------------------


def test_non_boolean_hint_is_reported_with_its_value():
    """Check a tool with readOnlyHint set to the string "true".

    What:     One problem that shows the value.
    Why:      The string "true" is not a boolean, and a client may read it as false.
    Expected: One problem that mentions readOnlyHint and 'true'.
    """
    problems = run("core.hint-not-boolean", {"annotations": {"readOnlyHint": "true"}})
    assert len(problems) == 1
    assert "readOnlyHint" in problems[0].message
    assert "'true'" in problems[0].message


def test_each_bad_hint_is_reported_separately():
    """Check a tool with two non-boolean hints.

    What:     One problem per hint.
    Why:      Each hint needs its own fix.
    Expected: Two problems.
    """
    annotations = {"readOnlyHint": 1, "openWorldHint": "no"}
    assert len(run("core.hint-not-boolean", {"annotations": annotations})) == 2


@pytest.mark.parametrize("annotations", [None, {}, {"readOnlyHint": True, "idempotentHint": False}])
def test_real_booleans_and_missing_hints_are_not_reported(annotations):
    """Check tools whose hints are booleans or absent.

    What:     No problem.
    Why:      A missing hint is a different rule's concern.
    Expected: No problems.
    """
    assert run("core.hint-not-boolean", {"annotations": annotations}) == []


# --- core.no-access-hint --------------------------------------------------------


@pytest.mark.parametrize("annotations", [None, {}, {"destructiveHint": False}])
def test_missing_read_only_hint_is_reported(annotations):
    """Check tools that do not say whether they are read-only.

    What:     No annotations, empty annotations, or other hints only: all reported.
    Why:      Silence is not a safety claim, and a gate must treat the tool as unknown.
    Expected: One problem for the tool.
    """
    assert len(run("core.no-access-hint", {"annotations": annotations})) == 1


@pytest.mark.parametrize("value", [True, False])
def test_either_boolean_read_only_hint_is_accepted(value):
    """Check tools that say they are, or are not, read-only.

    What:     No problem for either answer.
    Why:      The rule asks for a claim, not for a particular one.
    Expected: No problems.
    """
    assert run("core.no-access-hint", {"annotations": {"readOnlyHint": value}}) == []


def test_non_boolean_read_only_hint_is_not_repeated_here():
    """Check a tool whose readOnlyHint is the string "true".

    What:     This rule treats the value as present, because core.hint-not-boolean reports it.
    Why:      Two findings for one mistake would be noise.
    Expected: No problems from this rule.
    """
    assert run("core.no-access-hint", {"annotations": {"readOnlyHint": "true"}}) == []


# --- core.hint-conflict ---------------------------------------------------------


def test_read_only_and_destructive_is_reported():
    """Check a tool marked both read-only and destructive.

    What:     One problem for the tool.
    Why:      A tool cannot do both, so at least one hint is wrong.
    Expected: One problem.
    """
    annotations = {"readOnlyHint": True, "destructiveHint": True}
    assert len(run("core.hint-conflict", {"annotations": annotations})) == 1


@pytest.mark.parametrize(
    "annotations",
    [
        {"readOnlyHint": True, "destructiveHint": False},
        {"readOnlyHint": False, "destructiveHint": True},
        {"readOnlyHint": "true", "destructiveHint": True},
        {"destructiveHint": True},
    ],
)
def test_other_combinations_are_not_a_conflict(annotations):
    """Check hint combinations that do not contradict.

    What:     No problem, including when readOnlyHint is not a real boolean.
    Why:      Only a real true with a real true is a contradiction.
    Expected: No problems.
    """
    assert run("core.hint-conflict", {"annotations": annotations}) == []
