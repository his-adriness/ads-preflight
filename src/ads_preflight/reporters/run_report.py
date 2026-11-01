"""Terminal report for `run`: a summary line, then one entry per test."""

import json

import click

from ads_preflight.reporters.terminal import clean_text
from ads_preflight.runner import CaseResult, RunResult

_LABELS = {"passed": "passed", "failed": "failed", "not_run": "not run"}
_COLORS = {"passed": "green", "failed": "red", "not_run": "yellow"}
_LABEL_WIDTH = len("not run")


def render_run_terminal(result: RunResult, source: str, color: bool = False) -> str:
    """Format a run as text.

    Everything that came from the server or the test file goes through `clean_text`. With
    `color`, the status label is wrapped in color codes. The caller prints with
    `typer.echo`, which removes them when the output is not a terminal.
    """
    lines = [_summary(result, source), _server_line(result)]
    if result.stopped:
        lines.append(f"The run {clean_text(result.stopped)}")
    if result.cases:
        lines.append("")
    for case in result.cases:
        lines.extend(_case_lines(case, color))
    return "\n".join(lines)


def _summary(result: RunResult, source: str) -> str:
    total = len(result.cases)
    parts = [f"{result.count('passed')} passed", f"{result.count('failed')} failed"]
    if result.count("not_run"):
        parts.append(f"{result.count('not_run')} not run")
    noun = "test" if total == 1 else "tests"
    return f"{clean_text(source)}: {total} {noun}, " + ", ".join(parts)


def _server_line(result: RunResult) -> str:
    info = result.server
    text = clean_text(info.name) if info.name else "(did not give a name)"
    if info.version:
        text += f" {clean_text(info.version)}"
    return f"Server: {text}"


def _case_lines(case: CaseResult, color: bool) -> list[str]:
    label = f"{_LABELS[case.status]:<{_LABEL_WIDTH}}"
    if color:
        label = click.style(label, fg=_COLORS[case.status])
    detail = f"({clean_text(case.tool)}"
    if case.record is not None:
        detail += f", {case.record.latency_ms:.0f} ms"
    lines = [f"{label}  {clean_text(case.name)} {detail})"]
    for failure in case.failures:
        problem = f"{clean_text(failure.check)}: {clean_text(failure.message)}"
        lines.append(f"{'':<{_LABEL_WIDTH + 2}}  {problem}")
    return lines


def render_run_json(result: RunResult, source: str) -> str:
    """Format a run as JSON text, for programs to read.

    A test's result from the server is not included, only the reason a check failed. A
    report goes to CI logs and must not carry account data by default.
    """
    report = {
        "source": source,
        "server": {
            "name": result.server.name,
            "version": result.server.version,
            "protocolVersion": result.server.protocol_version,
        },
        "summary": {
            "tests": len(result.cases),
            "passed": result.count("passed"),
            "failed": result.count("failed"),
            "notRun": result.count("not_run"),
        },
        "stopped": result.stopped,
        "tests": [_case_json(case) for case in result.cases],
    }
    return json.dumps(report, indent=2)


def _case_json(case: CaseResult) -> dict[str, object]:
    return {
        "name": case.name,
        "tool": case.tool,
        "status": case.status,
        "latencyMs": round(case.record.latency_ms, 3) if case.record is not None else None,
        "failures": [{"check": f.check, "message": f.message} for f in case.failures],
    }
