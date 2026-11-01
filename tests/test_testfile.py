"""Tests for reading and checking test files.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest

from ads_preflight import testfile
from ads_preflight.models import ToolAnnotations, ToolDef
from ads_preflight.testfile import SuiteFileError, check_access, load_suite, parse_suite

GOOD = """\
version: 1
server:
  command: [python, examples/servers/notes_server.py]
  timeout: 10
allow_tools: [list_notes, get_note]
tests:
  - name: lists the notes
    call: list_notes
    arguments: {limit: 2}
    expect:
      text_contains: Groceries
      max_latency_ms: 2000
  - name: a missing note fails
    call: get_note
    arguments: {id: nope}
    expect:
      is_error: true
"""


def minimal(extra_top="", extra_case=""):
    """The smallest valid file, with extra lines added at the top or in the one test."""
    return (
        "version: 1\nallow_tools: [a]\n"
        + extra_top
        + "tests:\n  - name: t\n    call: a\n"
        + extra_case
    )


def problems_of(text):
    with pytest.raises(SuiteFileError) as error:
        parse_suite(text, "f.yaml")
    return str(error.value)


def tool(name, read_only=None):
    annotations = None if read_only is None else ToolAnnotations(readOnlyHint=read_only)
    return ToolDef(name=name, annotations=annotations)


# --- A valid file ----------------------------------------------------------------------------


def test_a_good_file_is_read():
    """Read a complete, valid file.

    What:     The server, the allowlist and both tests come out as written.
    Why:      This is the shape every user's file will have.
    Expected: Two tests, the server command and timeout, and the allowed tools in order.
    """
    suite = parse_suite(GOOD)
    assert [case.name for case in suite.tests] == ["lists the notes", "a missing note fails"]
    assert suite.server.command == ["python", "examples/servers/notes_server.py"]
    assert suite.server.timeout == 10
    assert suite.callable_tools == ("list_notes", "get_note")
    assert suite.tests[0].arguments == {"limit": 2}
    assert suite.tests[1].expect.is_error is True


def test_defaults_are_a_call_that_succeeds():
    """Leave out `expect`.

    What:     The call must succeed, and nothing else is checked.
    Why:      A test with no expectation must not pass a failing call.
    Expected: is_error False, no text checks, no latency limit, no structured check.
    """
    expect = parse_suite(minimal()).tests[0].expect
    assert expect.is_error is False
    assert expect.text_contains == () and expect.text_equals is None
    assert expect.max_latency_ms is None
    assert "structured_equals" not in expect.model_fields_set


def test_text_contains_accepts_one_string_or_a_list():
    """Give text_contains as a string and as a list.

    What:     Both become a tuple of strings.
    Why:      One expected text is the common case and should not need brackets.
    Expected: ("a",) and ("a", "b").
    """
    one = parse_suite(minimal(extra_case="    expect: {text_contains: a}\n"))
    many = parse_suite(minimal(extra_case="    expect: {text_contains: [a, b]}\n"))
    assert one.tests[0].expect.text_contains == ("a",)
    assert many.tests[0].expect.text_contains == ("a", "b")


def test_structured_equals_can_be_null():
    """Write `structured_equals: null`.

    What:     The check is on, and expects null.
    Why:      "The server sends no structured content" is a legitimate thing to test, and
              a missing key must not be confused with it.
    Expected: "structured_equals" is among the fields that were set.
    """
    suite = parse_suite(minimal(extra_case="    expect: {structured_equals: null}\n"))
    assert "structured_equals" in suite.tests[0].expect.model_fields_set


def test_a_file_is_loaded_from_disk(tmp_path):
    """Load a file by path, with a byte order mark as some editors write it.

    What:     The file is read the same as its text.
    Why:      Windows editors add a BOM, and the loader must not choke on it.
    Expected: Two tests.
    """
    path = tmp_path / "t.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + GOOD.encode("utf-8"))
    assert len(load_suite(path).tests) == 2


def test_an_example_file_in_the_repository_is_valid():
    """Load the example test file that ships with the project.

    What:     examples/tests/notes.yaml is valid.
    Why:      The README will point at it, so it must never go out of date.
    Expected: At least one test.
    """
    from pathlib import Path

    path = Path(__file__).parent.parent / "examples" / "tests" / "notes.yaml"
    assert load_suite(path).tests


# --- Mistakes are reported with a line number ---------------------------------------------------


def test_a_missing_file_is_a_clear_error(tmp_path):
    """Load a path that does not exist.

    What:     SuiteFileError naming the file.
    Why:      The user must know it was the path, not the content.
    Expected: "cannot read" and the file name in the message.
    """
    with pytest.raises(SuiteFileError) as error:
        load_suite(tmp_path / "nope.yaml")
    assert "cannot read" in str(error.value) and "nope.yaml" in str(error.value)


def test_a_file_that_is_too_large_is_refused(tmp_path):
    """Load a file bigger than the limit.

    What:     SuiteFileError, without parsing it.
    Why:      A test file is small; a huge one is a mistake or an attack on memory.
    Expected: "larger than" in the message.
    """
    path = tmp_path / "big.yaml"
    path.write_bytes(b"#" * (testfile.MAX_FILE_BYTES + 1))
    with pytest.raises(SuiteFileError) as error:
        load_suite(path)
    assert "larger than" in str(error.value)


def test_a_file_that_is_not_text_is_refused(tmp_path):
    """Load bytes that are not UTF-8.

    What:     SuiteFileError saying so.
    Why:      The YAML error for it would be hard to read.
    Expected: "not valid UTF-8".
    """
    path = tmp_path / "bin.yaml"
    path.write_bytes(b"version: \xff\xfe")
    with pytest.raises(SuiteFileError) as error:
        load_suite(path)
    assert "not valid UTF-8" in str(error.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is empty"),
        ("a: [", "not valid YAML: line"),
        ("- 1\n- 2\n", "key: value pairs at the top level"),
        ("version: 1\n---\nversion: 1\n", "not valid YAML"),
    ],
)
def test_text_that_is_not_a_test_file_is_explained(text, message):
    """Parse text that is empty, broken YAML, a list, or two documents.

    What:     SuiteFileError with the matching message.
    Why:      Each is a different mistake and needs a different hint.
    Expected: The message contains the expected text.
    """
    assert message in problems_of(text)


def test_each_problem_has_a_line_and_a_path():
    """Break several things in one file.

    What:     One message lists all problems, each with its line and its place in the file.
    Why:      Fixing one error at a time, each needing a new run, wastes time.
    Expected: Lines and paths for the version, the bad value and the unknown field.
    """
    text = (
        "version: 2\n"
        "allow_tools: [a]\n"
        "tests:\n"
        "  - name: t\n"
        "    call: a\n"
        "    expect: {is_error: 'true', max_latency_ms: -1, foo: 1}\n"
    )
    message = problems_of(text)
    assert "line 1: version: must be 1" in message
    assert "line 6: tests[0].expect.is_error" in message
    assert "line 6: tests[0].expect.max_latency_ms" in message
    assert "line 6: tests[0].expect.foo: unknown field" in message


def test_a_string_is_not_accepted_as_a_boolean():
    """Write `is_error: "true"` with quotes.

    What:     The file is refused.
    Why:      Quietly turning text into true or false would hide typos.
    Expected: An error on is_error.
    """
    assert "is_error" in problems_of(minimal(extra_case="    expect: {is_error: 'true'}\n"))


def test_unknown_fields_are_refused_at_every_level():
    """Add an unknown key at the top, in a test and in the server block.

    What:     Each is reported.
    Why:      A misspelled `expect` key would otherwise silently check nothing.
    Expected: Three "unknown field" lines.
    """
    text = minimal(extra_top="oops: 1\nserver: {comand: [x]}\n", extra_case="    exepct: {}\n")
    assert problems_of(text).count("unknown field") == 3


def test_missing_required_parts_are_named():
    """Leave out version, allow_tools and tests.

    What:     All three are reported as required.
    Why:      The user should see everything missing at once.
    Expected: "is required" for each name.
    """
    message = problems_of("server: {timeout: 5}\n")
    for name in ("version", "allow_tools", "tests"):
        assert f"{name}: is required" in message


def test_empty_lists_are_refused():
    """Give empty allow_tools and tests.

    What:     Both are errors.
    Why:      A file with nothing to call and nothing to run would pass doing nothing.
    Expected: "must not be empty" twice.
    """
    assert problems_of("version: 1\nallow_tools: []\ntests: []\n").count("must not be empty") == 2


def test_a_key_given_twice_is_refused():
    """Write the same key twice in one test.

    What:     The second one is reported with its line.
    Why:      YAML keeps the last value, so a pasted duplicate `expect` would hide the first.
    Expected: "given twice" with the line number.
    """
    message = problems_of(minimal(extra_case="    call: a\n"))
    assert "line 6: call: this key is given twice" in message


def test_two_tests_with_the_same_name_are_refused():
    """Use one test name twice.

    What:     The second is reported.
    Why:      Reports are by name; two with one name cannot be told apart.
    Expected: "used twice" on the second test's line.
    """
    text = minimal() + "  - name: t\n    call: a\n"
    assert "line 6: tests[1].name: the test name 't' is used twice" in problems_of(text)


def test_a_test_may_only_call_an_allowed_tool():
    """Call a tool that is not in allow_tools.

    What:     The test is reported by line.
    Why:      The allowlist is the safety rule, so it is checked before anything runs.
    Expected: "is not in allow_tools".
    """
    text = minimal().replace("call: a", "call: b")
    assert "'b' is not in allow_tools" in problems_of(text)


def test_problems_shown_are_limited():
    """Write a file with many tests that all have the same mistake.

    What:     The report shows the first few and says how many more.
    Why:      A thousand lines of errors helps no one.
    Expected: "... and N more".
    """
    cases = "".join(f"  - name: t{i}\n    call: a\n    foo: 1\n" for i in range(20))
    message = problems_of("version: 1\nallow_tools: [a]\ntests:\n" + cases)
    assert "... and 12 more" in message


# --- The server block ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ("{command: [x], url: 'https://e.com/m'}", "either command or url"),
        ("{url: 'https://e.com/m', env: [A_B]}", "env only applies"),
        ("{command: [x], bearer_env: TOKEN}", "only apply together with url"),
        ("{url: 'http://example.com/m'}", "http:// is only allowed for localhost"),
        ("{url: 'https://user:pw@e.com/m'}", "must not contain a user name"),
        ("{command: []}", "must not be empty"),
        ("{timeout: 0}", "greater than or equal to 0.1"),
        ("{env: ['A=b']}", "not A=VALUE"),
        ("{url: 'https://e.com/m', header_env: ['Host=A']}", "the connection needs it"),
        ("{url: 'https://e.com/m', header_env: ['X-Key']}", "takes HEADER=NAME"),
    ],
)
def test_a_server_block_that_cannot_work_is_refused(server, message):
    """Write server settings that contradict each other or break a rule.

    What:     The same rules as the command-line options, with the file's line.
    Why:      A file must not get around what the options refuse.
    Expected: The message contains the expected text.
    """
    assert message in problems_of(minimal(extra_top=f"server: {server}\n"))


def test_a_secret_written_in_the_file_is_refused_and_not_repeated():
    """Write a token where a variable name belongs.

    What:     The file is refused, and the token is not in the message.
    Why:      The message is printed and may land in a CI log.
    Expected: "version control" in the message and no part of the token.
    """
    message = problems_of(
        minimal(extra_top="server: {url: 'https://e.com/m', bearer_env: 'sk-live-HUNTER2'}\n")
    )
    assert "version control" in message
    assert "HUNTER2" not in message


def test_credentials_in_the_server_block_are_names_only():
    """Write bearer_env and header_env with variable names.

    What:     They are accepted.
    Why:      Names are the supported way to give credentials.
    Expected: A suite with those names, and no value anywhere.
    """
    suite = parse_suite(
        minimal(
            extra_top=(
                "server: {url: 'https://e.com/m', bearer_env: API_TOKEN, "
                "header_env: ['X-Api-Key=KEY_VAR']}\n"
            )
        )
    )
    assert suite.server.bearer_env == "API_TOKEN"
    assert suite.server.header_env == ["X-Api-Key=KEY_VAR"]


# --- Writes are not supported yet, but have a place ---------------------------------------------


def test_allow_writes_is_refused_for_now():
    """List a tool in allow_writes.

    What:     The file is refused with a clear reason.
    Why:      This version runs read-only tests; a write silently accepted would be run
              by a later version without a decision.
    Expected: "cannot be run yet" on the allow_writes line.
    """
    message = problems_of(minimal(extra_top="allow_writes: [w]\n"))
    assert "line 3: allow_writes: tools that change data cannot be run yet" in message


def test_allow_writes_is_accepted_once_writes_are_supported(monkeypatch):
    """Turn the switch on and list a write tool.

    What:     The file loads, and the tool may be called by a test.
    Why:      Shows that adding write support later means changing one constant.
    Expected: callable_tools holds both lists.
    """
    monkeypatch.setattr(testfile, "WRITES_SUPPORTED", True)
    suite = parse_suite(minimal(extra_top="allow_writes: [w]\n"))
    assert suite.callable_tools == ("a", "w")


def test_a_tool_in_both_lists_is_refused(monkeypatch):
    """List the same tool in allow_tools and allow_writes.

    What:     The file is refused.
    Why:      The tool would be both "read-only" and "may write", and which one wins
              must never be a guess.
    Expected: "also listed in allow_tools".
    """
    monkeypatch.setattr(testfile, "WRITES_SUPPORTED", True)
    assert "also listed in allow_tools" in problems_of(minimal(extra_top="allow_writes: [a]\n"))


# --- Comparing the allowlist with the server -----------------------------------------------------


def test_read_only_tools_pass_the_access_check():
    """Allow tools the server declares read-only.

    What:     No problems.
    Why:      The normal case must not complain.
    Expected: An empty list.
    """
    suite = parse_suite(GOOD)
    assert check_access(suite, [tool("list_notes", True), tool("get_note", True)]) == []


def test_a_tool_the_server_does_not_list_is_a_problem():
    """Allow a tool that is not on the server.

    What:     A problem naming it.
    Why:      A typo or a changed server should stop the run, not skip the test.
    Expected: "does not list the tool 'get_note'".
    """
    suite = parse_suite(GOOD)
    problems = check_access(suite, [tool("list_notes", True)])
    assert problems == ["the server does not list the tool 'get_note'"]


@pytest.mark.parametrize(
    ("hint", "words"),
    [(False, "says it changes data"), (None, "does not say whether it only reads")],
)
def test_a_tool_not_declared_read_only_is_a_problem(hint, words):
    """Allow a tool that says it writes, or says nothing.

    What:     A problem for it.
    Why:      Silence and mistakes are never treated as safe.
    Expected: The matching words in the problem, and a note that writes cannot run yet.
    """
    suite = parse_suite(GOOD)
    problems = check_access(suite, [tool("list_notes", True), tool("get_note", hint)])
    assert len(problems) == 1
    assert words in problems[0] and "cannot be run yet" in problems[0]


def test_a_write_tool_in_allow_writes_passes_once_supported(monkeypatch):
    """Allow a write tool through allow_writes, with writes supported.

    What:     No problem for it, but a problem if the server does not list it.
    Why:      Shows the check is already built for the future feature.
    Expected: An empty list, then a "does not list" problem.
    """
    monkeypatch.setattr(testfile, "WRITES_SUPPORTED", True)
    suite = parse_suite(minimal(extra_top="allow_writes: [w]\n"))
    assert check_access(suite, [tool("a", True), tool("w", False)]) == []
    assert check_access(suite, [tool("a", True)]) == ["the server does not list the tool 'w'"]
