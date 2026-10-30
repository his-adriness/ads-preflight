"""Pass environment variables to a server by name, and hide their values in our output.

Only names are accepted on the command line. A value typed there ends up in shell history
and in process listings, so the value is always read from the caller's own environment.
"""

import os
import re
from collections.abc import Iterable, Mapping, Sequence

NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_SECRET_LENGTH = 4  # Replacing shorter values would mangle ordinary text.
MASK = "***"


class EnvError(Exception):
    """An --env option could not be used. The message is ready to show."""


def resolve_env(names: Sequence[str], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Look up each named variable and return the values to give the server.

    Raises EnvError for `NAME=VALUE` (the value is never repeated in the message), for a
    name that is not a valid variable name, and for a variable that is not set.
    """
    source = os.environ if environ is None else environ
    resolved: dict[str, str] = {}
    for name in names:
        if "=" in name:
            raise EnvError(
                f"--env takes a variable name, not {name.split('=', 1)[0]}=VALUE: a value typed "
                "on the command line is kept in shell history. Set the variable in your "
                "environment and pass its name only"
            )
        if not NAME_PATTERN.fullmatch(name):
            raise EnvError(f"{name!r} is not a valid environment variable name")
        value = source.get(name)
        if value is None:
            raise EnvError(f"environment variable {name} is not set")
        resolved[name] = value
    return resolved


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every secret value in `text` with ***. Very short values are left alone."""
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= MIN_SECRET_LENGTH:
            text = text.replace(secret, MASK)
    return text
