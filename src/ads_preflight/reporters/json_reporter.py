"""JSON report: the same findings as the terminal report, for programs to read."""

import json
from collections import Counter
from collections.abc import Sequence

from ads_preflight.findings import Finding


def render_json(findings: Sequence[Finding], source: str, tool_count: int) -> str:
    """Format findings as JSON text. Every severity appears in `summary`, even at zero."""
    counts = Counter(finding.severity for finding in findings)
    report = {
        "source": source,
        "tools": tool_count,
        "summary": {severity: counts[severity] for severity in ("error", "warning", "info")},
        "findings": [finding.model_dump() for finding in findings],
    }
    return json.dumps(report, indent=2)
