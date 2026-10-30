"""Tests for passing environment variables by name and hiding their values.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest

from ads_preflight.env import EnvError, redact, resolve_env

# --- resolve_env ---------------------------------------------------------------------------


def test_names_are_looked_up_in_the_environment():
    """Resolve two variables that are set.

    What:     A dictionary of name to value for exactly the names asked for.
    Why:      The server must get what was asked for, and nothing else from our environment.
    Expected: Only API_TOKEN and REGION, with their values.
    """
    environ = {"API_TOKEN": "s3cret-value", "REGION": "eu", "OTHER": "no"}
    assert resolve_env(["API_TOKEN", "REGION"], environ) == {
        "API_TOKEN": "s3cret-value",
        "REGION": "eu",
    }


def test_the_real_environment_is_used_by_default(monkeypatch):
    """Resolve a variable without passing an environment.

    What:     The value comes from the process environment.
    Why:      This is what the command line does.
    Expected: The value set with monkeypatch.
    """
    monkeypatch.setenv("ADS_PREFLIGHT_TEST_VAR", "from-process")
    assert resolve_env(["ADS_PREFLIGHT_TEST_VAR"]) == {"ADS_PREFLIGHT_TEST_VAR": "from-process"}


def test_no_names_gives_an_empty_result():
    """Resolve an empty list.

    What:     An empty dictionary.
    Why:      Most runs pass no variables.
    Expected: {}.
    """
    assert resolve_env([], {"A": "b"}) == {}


def test_a_variable_set_to_an_empty_string_is_passed():
    """Resolve a variable whose value is empty.

    What:     It is passed on, not treated as missing.
    Why:      An empty value is a value, and a server may check for it.
    Expected: The name maps to "".
    """
    assert resolve_env(["FLAG"], {"FLAG": ""}) == {"FLAG": ""}


def test_a_missing_variable_is_an_error_that_names_it():
    """Resolve a variable that is not set.

    What:     EnvError that says which variable.
    Why:      Starting the server without it would fail later in a confusing way.
    Expected: EnvError with "API_TOKEN" and "not set".
    """
    with pytest.raises(EnvError) as error:
        resolve_env(["API_TOKEN"], {})
    assert "API_TOKEN" in str(error.value)
    assert "not set" in str(error.value)


def test_a_value_on_the_command_line_is_rejected_and_never_repeated():
    """Resolve "API_TOKEN=hunter2-secret", as if typed on the command line.

    What:     EnvError that explains the rule and does not contain the value.
    Why:      The value would reach logs and CI output through our own error message.
    Expected: EnvError mentioning API_TOKEN, and "hunter2-secret" absent from the message.
    """
    with pytest.raises(EnvError) as error:
        resolve_env(["API_TOKEN=hunter2-secret"], {})
    assert "API_TOKEN" in str(error.value)
    assert "hunter2-secret" not in str(error.value)


@pytest.mark.parametrize("name", ["", "1ABC", "A-B", "A B", "A.B"])
def test_an_invalid_name_is_an_error(name):
    """Resolve names that cannot be variable names.

    What:     EnvError saying the name is not valid.
    Why:      A typo should be reported, not looked up and reported as "not set".
    Expected: EnvError with "not a valid".
    """
    with pytest.raises(EnvError) as error:
        resolve_env([name], {})
    assert "not a valid" in str(error.value)


def test_repeating_a_name_gives_one_entry():
    """Resolve the same name twice.

    What:     One entry.
    Why:      The dictionary has one key per variable.
    Expected: A single key.
    """
    assert resolve_env(["A", "A"], {"A": "value"}) == {"A": "value"}


# --- redact --------------------------------------------------------------------------------


def test_secret_values_are_replaced_everywhere_in_the_text():
    """Redact a text that contains a secret twice.

    What:     Both occurrences become ***.
    Why:      A crashing server may print its configuration more than once.
    Expected: No secret left in the text.
    """
    text = "token=abcd1234 retry token=abcd1234"
    assert redact(text, ["abcd1234"]) == "token=*** retry token=***"


def test_the_longest_secret_is_replaced_first():
    """Redact when one secret contains another.

    What:     The longer value is hidden completely.
    Why:      Replacing the shorter one first would leave part of the longer one visible.
    Expected: The whole text becomes ***.
    """
    assert redact("abcd-efgh", ["abcd", "abcd-efgh"]) == "***"


def test_very_short_values_are_left_alone():
    """Redact with a value of three characters.

    What:     The text is unchanged.
    Why:      Replacing "eu" or "on" would mangle ordinary words and hide nothing useful.
    Expected: The original text.
    """
    assert redact("region eu is on", ["eu", "on"]) == "region eu is on"


def test_redacting_with_no_secrets_changes_nothing():
    """Redact with an empty list.

    What:     The text is unchanged.
    Why:      Most runs pass no variables.
    Expected: The original text.
    """
    assert redact("plain text", []) == "plain text"
