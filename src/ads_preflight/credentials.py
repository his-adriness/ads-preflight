"""Credentials for an HTTP server, read from environment variables by name.

Like `--env`, the command line takes names only. A token typed there ends up in shell
history and in process listings, so the value is always read from the caller's environment.
"""

import os
import re
from collections.abc import Mapping, Sequence

from ads_preflight.env import NAME_PATTERN

HEADER_NAME_PATTERN = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
# Headers the MCP connection itself depends on. Overriding one would break it in confusing ways.
RESERVED_HEADERS = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "host",
        "mcp-protocol-version",
        "mcp-session-id",
        "transfer-encoding",
    }
)


class CredentialsError(Exception):
    """A --bearer-env or --header-env option could not be used. The message is ready to show."""


def build_headers(
    bearer_env: str | None,
    header_env: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the HTTP headers to send, with values read from the named variables.

    `bearer_env` is a variable name; its value is sent as `Authorization: Bearer <value>`.
    Each `header_env` item is `HEADER=NAME`: the value of variable NAME is sent as HEADER.
    Raises CredentialsError. No message repeats a value, or the text after `=`.
    """
    source = os.environ if environ is None else environ
    headers: dict[str, str] = {}
    if bearer_env is not None:
        headers["Authorization"] = "Bearer " + _read("--bearer-env", bearer_env, source)
    seen = {name.lower() for name in headers}
    for spec in header_env:
        header, separator, variable = spec.partition("=")
        if not separator:
            raise CredentialsError(
                "--header-env takes HEADER=NAME, for example X-Api-Key=MY_KEY_VARIABLE, "
                "where NAME is the name of an environment variable"
            )
        if not HEADER_NAME_PATTERN.fullmatch(header):
            raise CredentialsError(f"{header!r} is not a valid HTTP header name")
        if header.lower() in RESERVED_HEADERS:
            raise CredentialsError(f"--header-env cannot set {header}: the connection needs it")
        if header.lower() in seen:
            raise CredentialsError(f"the header {header} is given more than once")
        seen.add(header.lower())
        headers[header] = _read("--header-env", variable, source)
    return headers


def _read(option: str, name: str, source: Mapping[str, str]) -> str:
    if "=" in name or not NAME_PATTERN.fullmatch(name):
        # The text may be a pasted secret, so it is not repeated.
        raise CredentialsError(
            f"{option} takes the name of an environment variable (letters, digits and "
            "underscores), not a value: a value typed on the command line is kept in shell "
            "history. Set the variable in your environment and pass its name only"
        )
    value = source.get(name)
    if value is None:
        raise CredentialsError(f"environment variable {name} is not set")
    value = value.strip()
    if not value:
        raise CredentialsError(f"environment variable {name} is empty")
    if not value.isascii() or not value.isprintable():
        raise CredentialsError(
            f"environment variable {name} cannot be sent as an HTTP header value: it must be "
            "plain text on one line"
        )
    return value


def secret_values(headers: Mapping[str, str]) -> list[str]:
    """Every part of the header values that must be hidden in our output.

    The whole value, and the part after the first space, so that the token of
    `Bearer <token>` is hidden even when it appears without the word Bearer.
    """
    secrets: list[str] = []
    for value in headers.values():
        secrets.append(value)
        _, space, rest = value.partition(" ")
        if space and rest:
            secrets.append(rest)
    return secrets

