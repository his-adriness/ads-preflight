"""Tests for Finding and the lint runner.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest
from pydantic import ValidationError

from ads_preflight.findings import Finding
from ads_preflight.lint_rules import RULES
from ads_preflight.linter import lint
from ads_preflight.models import ToolDef


def tool(**fields):
    return ToolDef.model_validate({"name": "t", **fields})


GOOD = {
    "description": "Does a thing.",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True},
}


# --- Finding ----------------------------------------------------------------------


def test_finding_needs_a_rule_severity_and_message():
    """Build a finding without the required fields.

    What:     Only tool and fix are optional.
    Why:      A finding without a rule ID cannot be suppressed or looked up.
    Expected: ValidationError for each missing field.
    """
    with pytest.raises(ValidationError) as error:
        Finding.model_validate({})
    text = str(error.value)
    assert "rule" in text and "severity" in text and "message" in text


def test_finding_rejects_an_unknown_severity():
    """Build a finding with a severity that does not exist.

    What:     "critical" is rejected.
    Why:      The exit code depends on the severity, so a typo must not pass.
    Expected: ValidationError.
    """
    with pytest.raises(ValidationError):
        Finding(rule="core.x", severity="critical", message="m")


def test_finding_is_frozen():
    """Try to change a finding.

    What:     Assigning to a field raises.
    Why:      Reporters share findings, and none should change what another prints.
    Expected: ValidationError.
    """
    finding = Finding(rule="core.x", severity="error", message="m")
    with pytest.raises(ValidationError):
        finding.message = "other"


# --- The runner --------------------------------------------------------------------


def test_clean_tools_give_no_findings():
    """Lint tools that every rule accepts.

    What:     An empty result.
    Why:      A clean server must pass.
    Expected: [].
    """
    assert lint([tool(**GOOD)]) == []


def test_findings_carry_the_rule_id_severity_and_tool():
    """Lint a tool with no description.

    What:     The finding has the rule's ID and severity and the tool's name.
    Why:      Reporters and exit codes read these fields.
    Expected: One warning, core.missing-description, for the tool "t".
    """
    findings = lint([tool(**{**GOOD, "description": None})])
    assert [(f.rule, f.severity, f.tool) for f in findings] == [
        ("core.missing-description", "warning", "t")
    ]
    assert findings[0].fix


def test_errors_come_before_warnings_and_order_is_otherwise_stable():
    """Lint tools with both errors and warnings.

    What:     Errors are listed first, and each group keeps rule order, then tool order.
    Why:      A reader should see what blocks a build before what is only advice.
    Expected: The error first, then the warnings in the order of the rules.
    """
    tools = [
        tool(name="a", inputSchema={"type": "object"}, description=None),
        tool(name="a", inputSchema={"type": "object"}, annotations={"readOnlyHint": "yes"}),
    ]
    findings = lint(tools)
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key={"error": 0, "warning": 1}.__getitem__)
    assert findings[0].severity == "error"
    warning_rules = [f.rule for f in findings if f.severity == "warning"]
    rule_order = [rule.id for rule in RULES]
    assert warning_rules == sorted(warning_rules, key=rule_order.index)


def test_empty_list_is_reported_once():
    """Lint no tools at all.

    What:     One warning about the empty list.
    Why:      An empty server is odd, but it is not a crash.
    Expected: One finding with the rule core.empty-tool-list and no tool.
    """
    findings = lint([])
    assert [(f.rule, f.tool) for f in findings] == [("core.empty-tool-list", None)]


def test_a_subset_of_rules_can_be_run():
    """Lint with only one rule.

    What:     Other rules do not run.
    Why:      Later, users will turn rules off.
    Expected: Only findings from the chosen rule.
    """
    only = [rule for rule in RULES if rule.id == "core.no-access-hint"]
    findings = lint([tool(name="a"), tool(name="a")], rules=only)
    assert {f.rule for f in findings} == {"core.no-access-hint"}


def test_the_same_mistake_is_not_reported_twice():
    """Lint a tool copied from the Claude API, with 'input_schema' and a bad hint.

    What:     One error for the key, none for the missing schema, one error for the hint,
              none for the missing read-only hint.
    Why:      Overlapping rules must not pile up findings for one mistake.
    Expected: Exactly the two errors.
    """
    pasted = tool(
        description="Does a thing.",
        input_schema={"type": "object"},
        annotations={"readOnlyHint": "true"},
    )
    assert sorted(f.rule for f in lint([pasted])) == [
        "core.hint-not-boolean",
        "core.wrong-schema-key",
    ]
