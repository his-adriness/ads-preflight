"""Tests for reading credentials from environment variables by name.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest

from ads_preflight.credentials import CredentialsError, build_headers, secret_values

ENVIRON = {
    "API_TOKEN": "tok-1234567890",
    "KEY_VAR": "key-abcdef",
    "EMPTY": "   ",
    "TWO_LINES": "abc\r\nX-Evil: 1",
}


def test_a_bearer_variable_becomes_an_authorization_header():
    """Build headers from --bearer-env.

    What:     The token is sent as `Authorization: Bearer <token>`.
    Why:      This is the form almost every hosted server expects.
    Expected: One header with the token read from the environment.
    """
    assert build_headers("API_TOKEN", [], ENVIRON) == {"Authorization": "Bearer tok-1234567890"}


def test_a_header_variable_becomes_that_header():
    """Build headers from --header-env.

    What:     The named variable's value is sent under the given header name.
    Why:      Some servers use a key header instead of a bearer token.
    Expected: {"X-Api-Key": the value}.
    """
    assert build_headers(None, ["X-Api-Key=KEY_VAR"], ENVIRON) == {"X-Api-Key": "key-abcdef"}


def test_no_options_means_no_headers():
    """Build headers with nothing asked for.

    What:     An empty dictionary.
    Why:      Servers without credentials must keep working unchanged.
    Expected: {}.
    """
    assert build_headers(None, [], ENVIRON) == {}


def test_a_trailing_newline_in_the_value_is_removed():
    """Read a token that ends with a newline, as one read from a file does.

    What:     Leading and trailing white space is dropped.
    Why:      A newline in a header value would be refused or break the request.
    Expected: The token without the newline.
    """
    headers = build_headers("API_TOKEN", [], {"API_TOKEN": "abcd1234\n"})
    assert headers == {"Authorization": "Bearer abcd1234"}


def test_authorization_can_be_set_with_header_env_when_bearer_is_not_used():
    """Send a non-bearer scheme such as `Basic ...` through --header-env.

    What:     The value is sent as it is.
    Why:      Not every server uses Bearer.
    Expected: Authorization with the full value.
    """
    headers = build_headers(None, ["Authorization=API_TOKEN"], {"API_TOKEN": "Basic dXNlcjpwdw=="})
    assert headers == {"Authorization": "Basic dXNlcjpwdw=="}


@pytest.mark.parametrize(
    ("bearer", "specs", "message"),
    [
        ("API_TOKEN", ["authorization=KEY_VAR"], "more than once"),
        (None, ["X-Api-Key=KEY_VAR", "x-api-key=API_TOKEN"], "more than once"),
        (None, ["Host=KEY_VAR"], "the connection needs it"),
        (None, ["Content-Type=KEY_VAR"], "the connection needs it"),
        (None, ["Mcp-Session-Id=KEY_VAR"], "the connection needs it"),
        (None, ["Bad Header=KEY_VAR"], "not a valid HTTP header name"),
        (None, ["=KEY_VAR"], "not a valid HTTP header name"),
        (None, ["X-Api-Key"], "takes HEADER=NAME"),
        ("MISSING", [], "environment variable MISSING is not set"),
        (None, ["X-Api-Key=MISSING"], "environment variable MISSING is not set"),
        ("EMPTY", [], "environment variable EMPTY is empty"),
        ("TWO_LINES", [], "plain text on one line"),
    ],
)
def test_unusable_options_are_refused_with_a_clear_message(bearer, specs, message):
    """Give options that cannot work.

    What:     CredentialsError with a message that says what to change.
    Why:      A silent fallback would send no credentials, or the wrong ones.
    Expected: The message contains the expected text.
    """
    with pytest.raises(CredentialsError) as error:
        build_headers(bearer, specs, ENVIRON)
    assert message in str(error.value)


@pytest.mark.parametrize(
    ("bearer", "specs"),
    [
        ("sk-live-SECRETVALUE99", []),
        ("SECRET=VALUE99", []),
        (None, ["X-Api-Key=sk-live-SECRETVALUE99"]),
        (None, ["X-Api-Key=SECRET=VALUE99"]),
    ],
)
def test_a_value_typed_instead_of_a_name_is_refused_and_not_repeated(bearer, specs):
    """Type a secret where a variable name belongs.

    What:     CredentialsError, and the typed text does not appear in the message.
    Why:      The message is printed to the terminal and may land in a CI log.
    Expected: The message mentions "a value" and contains no part of the secret.
    """
    with pytest.raises(CredentialsError) as error:
        build_headers(bearer, specs, ENVIRON)
    text = str(error.value)
    assert "not a value" in text
    assert "SECRETVALUE99" not in text and "VALUE99" not in text


def test_secret_values_include_the_token_without_its_scheme():
    """List what must be hidden for a bearer header.

    What:     Both the whole value and the part after the first space.
    Why:      A server may echo the bare token, without the word Bearer.
    Expected: ["Bearer tok-123", "tok-123"].
    """
    assert secret_values({"Authorization": "Bearer tok-123"}) == ["Bearer tok-123", "tok-123"]


def test_secret_values_of_a_plain_key_is_the_key():
    """List what must be hidden for a header with a single-word value.

    What:     Only the value itself.
    Why:      There is no scheme to strip.
    Expected: ["key-abcdef"].
    """
    assert secret_values({"X-Api-Key": "key-abcdef"}) == ["key-abcdef"]
