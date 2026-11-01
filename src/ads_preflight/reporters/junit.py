"""JUnit XML report for `run`, in the form CI systems read.

One test suite, one test case per test. The tool name is the case's class name. A failed
test has a `failure` with the reason of each check. A test that did not run is `skipped`,
except the one where a stopped run broke off, which is an `error`: a run that did not
finish must not look green in a CI summary.

The XML is built with an XML library, never by joining strings. Text from the server and
the test file goes through `clean_text` first, which leaves only characters XML allows.
"""

import xml.etree.ElementTree as ET

from ads_preflight.reporters.terminal import clean_text
from ads_preflight.runner import CaseResult, RunResult

DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


def render_run_junit(result: RunResult, source: str) -> str:
    """Format a run as JUnit XML text."""
    first_unrun = next((c for c in result.cases if c.status == "not_run"), None)
    broke_at = first_unrun if result.stopped is not None else None
    errors = 1 if broke_at is not None else 0
    skipped = result.count("not_run") - errors
    seconds = sum(c.record.latency_ms for c in result.cases if c.record is not None) / 1000

    suites = ET.Element("testsuites")
    suite = ET.SubElement(
        suites,
        "testsuite",
        name=clean_text(source),
        tests=str(len(result.cases)),
        failures=str(result.count("failed")),
        errors=str(errors),
        skipped=str(skipped),
        time=f"{seconds:.3f}",
    )
    for case in result.cases:
        _add_case(suite, case, broke_at, result.stopped)
    if result.stopped is not None and broke_at is None:
        ET.SubElement(suite, "system-err").text = clean_text(result.stopped)
    ET.indent(suites)
    return DECLARATION + ET.tostring(suites, encoding="unicode")


def _add_case(
    suite: ET.Element, case: CaseResult, broke_at: CaseResult | None, stopped: str | None
) -> None:
    seconds = case.record.latency_ms / 1000 if case.record is not None else 0.0
    element = ET.SubElement(
        suite,
        "testcase",
        name=clean_text(case.name),
        classname=clean_text(case.tool),
        time=f"{seconds:.3f}",
    )
    if case.status == "failed":
        lines = [f"{clean_text(f.check)}: {clean_text(f.message)}" for f in case.failures]
        lines = lines or ["the test failed"]
        kind = case.failures[0].check if case.failures else "failed"
        failure = ET.SubElement(element, "failure", message=lines[0], type=kind)
        failure.text = "\n".join(lines)
    elif case.status == "not_run" and case is broke_at:
        ET.SubElement(element, "error", message=clean_text(stopped or ""), type="ServerError")
    elif case.status == "not_run":
        reason = "the run stopped before this test" if stopped else "not run"
        ET.SubElement(element, "skipped", message=reason)
