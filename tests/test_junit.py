"""Tests for the JUnit XML report of a run.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import xml.etree.ElementTree as ET

from ads_preflight.assertions import Failure
from ads_preflight.models import CallRecord, ServerInfo
from ads_preflight.reporters.junit import render_run_junit
from ads_preflight.runner import CaseResult, RunResult


def record(latency):
    return CallRecord(tool="t", arguments={}, result="ok", latency_ms=latency)


def passed(name="first", tool="read_a", latency=1500.0):
    return CaseResult(name, tool, "passed", record=record(latency))


def failed(name="second", messages=("the text does not contain 'x'",), latency=500.0):
    failures = tuple(Failure("text_contains", m) for m in messages)
    return CaseResult(name, "read_b", "failed", failures, record(latency))


def not_run(name="third"):
    return CaseResult(name, "read_a", "not_run")


def parse(cases, stopped=None, source="tests.yaml"):
    text = render_run_junit(RunResult(ServerInfo(name="Notes"), tuple(cases), stopped), source)
    return text, ET.fromstring(text)


def test_the_output_is_well_formed_xml_with_a_declaration():
    """Render a run.

    What:     The text starts with an XML declaration and parses.
    Why:      CI systems reject malformed XML.
    Expected: A declaration and a root `testsuites` element.
    """
    text, root = parse([passed()])
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert root.tag == "testsuites"


def test_the_suite_has_the_counts_and_the_total_time():
    """Render one pass, one failure and one skipped test (the run did not stop).

    What:     The suite counts tests, failures, errors and skipped, and adds up the call times.
    Why:      CI summaries read these numbers.
    Expected: tests 3, failures 1, errors 0, skipped 1, time 2.000.
    """
    _, root = parse([passed(), failed(), not_run()])
    suite = root.find("testsuite")
    assert suite.attrib == {
        "name": "tests.yaml",
        "tests": "3",
        "failures": "1",
        "errors": "0",
        "skipped": "1",
        "time": "2.000",
    }


def test_each_test_is_a_case_with_the_tool_as_class_and_time_in_seconds():
    """Render a passing test.

    What:     A `testcase` with the test name, the tool as classname and the time in seconds.
    Why:      Dashboards group by classname.
    Expected: name first, classname read_a, time 1.500, and no child elements.
    """
    _, root = parse([passed()])
    case = root.find("testsuite/testcase")
    assert case.attrib == {"name": "first", "classname": "read_a", "time": "1.500"}
    assert len(case) == 0


def test_a_failed_test_has_a_failure_with_every_reason():
    """Render a test with two failed checks.

    What:     A `failure` whose message is the first reason and whose text lists all of them.
    Why:      The first line shows in a summary; the details show when it is opened.
    Expected: message and body as described, and type text_contains.
    """
    _, root = parse([failed(messages=("first problem", "second problem"))])
    failure = root.find("testsuite/testcase/failure")
    assert failure.attrib["message"] == "text_contains: first problem"
    assert failure.attrib["type"] == "text_contains"
    assert failure.text.splitlines() == [
        "text_contains: first problem",
        "text_contains: second problem",
    ]


def test_the_test_where_a_run_stopped_is_an_error_and_the_rest_are_skipped():
    """Render a run that stopped at the second test.

    What:     The second test has an `error` with the reason; the third is `skipped`.
    Why:      A run that did not finish must not look green in a CI summary.
    Expected: errors 1, skipped 1, and the reason in the error's message.
    """
    _, root = parse(
        [passed(), not_run("second"), not_run("third")],
        stopped="stopped at the test 'second': boom",
    )
    suite = root.find("testsuite")
    assert suite.attrib["errors"] == "1" and suite.attrib["skipped"] == "1"
    cases = suite.findall("testcase")
    assert "boom" in cases[1].find("error").attrib["message"]
    assert cases[2].find("skipped") is not None


def test_a_problem_after_the_last_test_is_a_system_err_note():
    """Render a run whose tests all finished but whose server failed to stop.

    What:     No error case; the reason is in `system-err`.
    Why:      Every test did run, so none can carry the error.
    Expected: errors 0, and the reason in system-err.
    """
    _, root = parse([passed()], stopped="after the last test: shutdown failed")
    suite = root.find("testsuite")
    assert suite.attrib["errors"] == "0"
    assert "shutdown failed" in suite.find("system-err").text


def test_text_with_markup_characters_is_escaped_not_injected():
    """Render a test name and a message that contain XML markup.

    What:     They come back as text, and no extra elements appear.
    Why:      Text from the server and the file must not be able to change the document.
    Expected: The parsed values equal the original text, and there is one testcase.
    """
    nasty = '</failure><testcase name="evil"/>&amp; "quote"'
    _, root = parse([failed(name=nasty, messages=(nasty,))])
    cases = root.findall("testsuite/testcase")
    assert len(cases) == 1
    assert cases[0].attrib["name"] == nasty
    assert nasty in cases[0].find("failure").attrib["message"]


def test_characters_that_xml_does_not_allow_are_shown_as_escapes():
    """Render a message with control characters that XML 1.0 cannot hold.

    What:     The document still parses, and the characters appear as visible escapes.
    Why:      A NUL or ESC byte makes a CI system refuse the whole report.
    Expected: Parsing works and the message holds the text \\x00 and \\x1b.
    """
    text, root = parse([failed(messages=("a\x00b\x1bc\ud800d",))])
    message = root.find("testsuite/testcase/failure").attrib["message"]
    assert "\\x00" in message and "\\x1b" in message
    assert "\x00" not in text and "\x1b" not in text


def test_an_empty_run_renders():
    """Render a result with no tests.

    What:     A suite with zero tests and no crash.
    Why:      The report must not depend on the file having tests.
    Expected: tests "0".
    """
    _, root = parse([])
    assert root.find("testsuite").attrib["tests"] == "0"
