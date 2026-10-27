"""Terminal report: plain text, with colored severity labels on request."""

from collections import Counter
from collections.abc import Sequence

import click

from ads_preflight.findings import Finding, Severity

_SEVERITIES: tuple[Severity, ...] = ("error", "warning", "info")
_COLORS = {"error": "red", "warning": "yellow", "info": "blue"}
_LABEL_WIDTH = len("warning")


def clean_text(text: str) -> str:
    """Show control characters as escapes, so a server's text cannot rewrite the terminal.

    Tool names and descriptions come from the server being checked, which may be hostile.
    """
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode() for char in text
    )


def render_terminal(
    findings: Sequence[Finding], source: str, tool_count: int, color: bool = False
) -> str:
    """Format findings as text: a summary line, then one entry per finding.

    With `color`, the severity label is wrapped in color codes. The caller prints with
    `typer.echo`, which removes them when the output is not a terminal.
    """
    lines = [_summary(findings, source, tool_count)]
    if findings:
        lines.append("")
        rule_width = max(len(clean_text(f.rule)) for f in findings)
        tool_width = max(len(_where(f)) for f in findings)
        for finding in findings:
            label = f"{finding.severity:<{_LABEL_WIDTH}}"
            if color:
                bold = finding.severity == "error"
                label = click.style(label, fg=_COLORS[finding.severity], bold=bold)
            rule = clean_text(finding.rule)
            lines.append(
                f"{label}  {rule:<{rule_width}}  {_where(finding):<{tool_width}}  "
                f"{clean_text(finding.message)}"
            )
            if finding.fix:
                lines.append(f"{' ' * (_LABEL_WIDTH + 2)}fix: {clean_text(finding.fix)}")
    return "\n".join(lines)


def _where(finding: Finding) -> str:
    return clean_text(finding.tool) if finding.tool is not None else "(server)"


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def _summary(findings: Sequence[Finding], source: str, tool_count: int) -> str:
    parts = [_count(tool_count, "tool")]
    if not findings:
        parts.append("no problems")
    counts = Counter(finding.severity for finding in findings)
    parts.extend(_count(counts[severity], severity) for severity in _SEVERITIES if counts[severity])
    return f"{clean_text(source)}: " + ", ".join(parts)
