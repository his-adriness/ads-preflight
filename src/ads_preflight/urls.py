"""Checks on the URL of an HTTP server, and a version of it that is safe to print."""

from urllib.parse import urlsplit, urlunsplit

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UrlError(Exception):
    """The server URL cannot be used. The message is ready to show."""


def check_url(url: str) -> str:
    """Return the URL if it is safe to connect to, or raise UrlError.

    Only http and https are accepted. Plain http is allowed only for the local machine,
    because anything sent over it can be read on the way. A user name or password in
    the URL is refused, because secrets in a URL end up in logs and shell history.
    """
    text = url.strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise UrlError(f"not a valid URL: {error}") from error
    if parts.scheme not in ("http", "https"):
        raise UrlError("the URL must start with https:// (or http:// for localhost)")
    if not parts.hostname:
        raise UrlError("the URL has no host name")
    if parts.username is not None or parts.password is not None:
        raise UrlError(
            "the URL must not contain a user name or password: secrets in a URL end up in "
            "logs and shell history"
        )
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise UrlError(
            "http:// is only allowed for localhost, because anything sent over it can be "
            "read on the way. Use https://"
        )
    if port == 0:
        raise UrlError("the URL has port 0, which cannot be connected to")
    return text


def display_url(url: str) -> str:
    """The URL without its fragment, and with any query replaced by ?***.

    Used wherever a URL is printed, because a query string may carry a token.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "(invalid URL)"
    query = "***" if parts.query else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def host_and_port(url: str) -> str:
    """The host and port of a URL as `host:port`, using the default port of the scheme."""
    parts = urlsplit(url.strip())
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname}:{port}"
