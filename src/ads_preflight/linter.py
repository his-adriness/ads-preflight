"""Run the lint rules over a list of tools."""

from collections.abc import Sequence

from ads_preflight.findings import SEVERITY_RANK, Finding
from ads_preflight.lint_rules import RULES, Rule
from ads_preflight.models import ToolDef


def lint(tools: Sequence[ToolDef], rules: Sequence[Rule] | None = None) -> list[Finding]:
    """Check tools against the rules and return the findings, errors first.

    Within one severity, findings keep the order of the rules, then of the tools.
    Pass `rules` to run a subset. By default every registered rule runs.
    """
    findings = [
        Finding(
            rule=rule.id,
            severity=rule.severity,
            tool=problem.tool,
            message=problem.message,
            fix=problem.fix,
        )
        for rule in (RULES if rules is None else rules)
        for problem in rule.check(tools)
    ]
    return sorted(findings, key=lambda finding: SEVERITY_RANK[finding.severity])


def failed(findings: Sequence[Finding], strict: bool = False) -> bool:
    """True if the findings should fail a run: any error, or any warning with `strict`."""
    blocking = {"error", "warning"} if strict else {"error"}
    return any(finding.severity in blocking for finding in findings)
