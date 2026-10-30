"""Tests for the checks on server URLs.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import pytest

from ads_preflight.urls import UrlError, check_url, display_url, host_and_port


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/mcp",
        "https://example.com:8443/mcp?x=1",
        "http://localhost/mcp",
        "http://127.0.0.1:8000/mcp",
        "http://[::1]:8000/mcp",
    ],
)
def test_safe_urls_are_accepted(url):
    """Check URLs that use https, or http on this computer.

    What:     The URL comes back unchanged.
    Why:      These are the connections that are safe to make.
    Expected: The same URL.
    """
    assert check_url(url) == url


def test_surrounding_spaces_are_removed():
    """Check a URL pasted with spaces around it.

    What:     The spaces are dropped.
    Why:      A copied URL often carries a space or a newline.
    Expected: The URL without them.
    """
    assert check_url("  https://example.com/mcp\n") == "https://example.com/mcp"


@pytest.mark.parametrize("url", ["http://example.com/mcp", "http://10.0.0.5/mcp"])
def test_plain_http_to_another_computer_is_refused(url):
    """Check an http:// URL for a server that is not on this computer.

    What:     UrlError that says to use https.
    Why:      Anything sent over plain http can be read on the way.
    Expected: UrlError mentioning "https://".
    """
    with pytest.raises(UrlError) as error:
        check_url(url)
    assert "https://" in str(error.value)


@pytest.mark.parametrize("url", ["ftp://example.com/mcp", "example.com/mcp", "file:///etc/passwd"])
def test_other_schemes_are_refused(url):
    """Check URLs that are not http or https.

    What:     UrlError.
    Why:      Only HTTP servers can be reached, and a missing scheme is a typo.
    Expected: UrlError.
    """
    with pytest.raises(UrlError):
        check_url(url)


def test_a_password_in_the_url_is_refused_and_not_repeated():
    """Check a URL with a user name and password in it.

    What:     UrlError whose message does not contain the secret.
    Why:      Secrets in URLs end up in logs, and our message would copy it there.
    Expected: UrlError mentioning "password", without "hunter2".
    """
    with pytest.raises(UrlError) as error:
        check_url("https://admin:hunter2@example.com/mcp")
    assert "password" in str(error.value)
    assert "hunter2" not in str(error.value)


def test_a_user_name_cannot_disguise_the_host():
    """Check http://localhost@example.com/, where "localhost" is only a user name.

    What:     UrlError.
    Why:      The real host is example.com, and treating it as local would allow plain http.
    Expected: UrlError.
    """
    with pytest.raises(UrlError):
        check_url("http://localhost@example.com/mcp")


@pytest.mark.parametrize("url", ["https:///mcp", "http://localhost:abc/mcp", "http://localhost:0/"])
def test_a_url_without_a_host_or_with_a_bad_port_is_refused(url):
    """Check URLs with no host or a port that cannot be used.

    What:     UrlError.
    Why:      The connection would fail later with a less helpful message.
    Expected: UrlError.
    """
    with pytest.raises(UrlError):
        check_url(url)


def test_display_hides_the_query_and_the_fragment():
    """Format a URL with a token in its query for printing.

    What:     The query becomes ?*** and the fragment is dropped.
    Why:      A query string may carry a token, and reports end up in CI logs.
    Expected: "https://example.com/mcp?***".
    """
    shown = display_url("https://example.com/mcp?token=abc123#frag")
    assert shown == "https://example.com/mcp?***"


def test_display_leaves_a_url_without_a_query_alone():
    """Format a URL that has no query.

    What:     The URL is unchanged.
    Why:      There is nothing to hide.
    Expected: The same URL.
    """
    assert display_url("https://example.com:8443/mcp") == "https://example.com:8443/mcp"


def test_host_and_port_uses_the_default_port_of_the_scheme():
    """Format the host and port of URLs with and without a port.

    What:     host:port with 443 for https and 80 for http.
    Why:      "cannot connect to" messages need a port to be useful.
    Expected: The three expected strings.
    """
    assert host_and_port("https://example.com/mcp") == "example.com:443"
    assert host_and_port("http://localhost/mcp") == "localhost:80"
    assert host_and_port("http://127.0.0.1:8000/mcp") == "127.0.0.1:8000"
