"""Command-line interface: ads-preflight inspect, run and lint.

The commands are stubs for now. Each one only parses its options and will call
library code in other modules once that code exists.
"""

import shlex
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ads_preflight import __version__

app = typer.Typer(
    name="ads-preflight",
    help="Preflight checks for ad-platform MCP servers: inspect, lint and test.",
    no_args_is_help=True,
    add_completion=False,
)

ServerArg = Annotated[
    list[str],
    typer.Argument(
        help="The command that starts the MCP server, written after a double dash.",
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ads-preflight {__version__}")
        raise typer.Exit()


def _not_implemented(command: str, server: list[str] | None = None) -> NoReturn:
    """Stop with exit code 1, so a pipeline that calls a stub fails loudly."""
    detail = f" (server: {shlex.join(server)})" if server else ""
    typer.echo(f"ads-preflight {command}: not implemented yet{detail}", err=True)
    raise typer.Exit(code=1)


@app.callback()
def main(
    show_version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Preflight checks for ad-platform MCP servers: inspect, lint and test."""


@app.command()
def inspect(server: ServerArg) -> None:
    """List a server's tools, resources and prompts.

    Example: ads-preflight inspect -- python server.py
    """
    _not_implemented("inspect", server)


@app.command()
def run(
    test_file: Annotated[Path, typer.Argument(help="YAML file with the tests to run.")],
    server: Annotated[
        list[str] | None,
        typer.Argument(help="Server command after a double dash. Overrides the file's own."),
    ] = None,
) -> None:
    """Run the tests in a YAML file against a server.

    Example: ads-preflight run tests/notes.yaml -- python server.py
    """
    _not_implemented("run", server)


@app.command()
def lint(server: ServerArg) -> None:
    """Audit a server for common problems, without needing a test file.

    Example: ads-preflight lint -- python server.py
    """
    _not_implemented("lint", server)
