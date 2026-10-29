"""Command-line interface: ads-preflight inspect, run and lint.

`lint` works on a tools file and on a live server. `inspect` and `run` are stubs for now:
they only parse their options and will call library code once that code exists.
"""

import shlex
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import anyio
import typer

from ads_preflight import __version__, linter
from ads_preflight.client import ServerError, list_server_tools
from ads_preflight.models import ToolDef
from ads_preflight.reporters.json_reporter import render_json
from ads_preflight.reporters.terminal import clean_text, render_terminal
from ads_preflight.tools_file import ToolsFileError, load_tools

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


def _usage_error(command: str, message: str) -> NoReturn:
    """Stop with exit code 2: the input or the server could not be used, no check ran."""
    lines = [clean_text(line) for line in message.splitlines()] or [""]
    typer.echo(f"ads-preflight {command}: " + "\n".join(lines), err=True)
    raise typer.Exit(code=2)


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
    """List the tools a server offers.

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


def _fetch_tools(server: Sequence[str], timeout: float) -> list[ToolDef]:
    """List a live server's tools. Tests replace this function."""
    return anyio.run(lambda: list_server_tools(server, timeout=timeout))


class OutputFormat(StrEnum):
    terminal = "terminal"
    json = "json"


@app.command()
def lint(
    server: Annotated[
        list[str] | None,
        typer.Argument(help="Server command after a double dash. It is started and stopped."),
    ] = None,
    tools_file: Annotated[
        Path | None,
        typer.Option("--tools-file", help="A saved tools/list response (JSON) to check."),
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="How to print the findings.")
    ] = OutputFormat.terminal,
    strict: Annotated[bool, typer.Option("--strict", help="Also fail on warnings.")] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=0.1,
            help="Seconds to wait for the server to start and to answer.",
        ),
    ] = 30.0,
) -> None:
    """Check a server's tools for common problems.

    Exit code 0: no errors. 1: errors (or warnings, with --strict).
    2: the input or the server could not be used.

    A live server is checked as the MCP SDK reports it, so a tool list that breaks the
    protocol (for example a missing inputSchema) stops with exit code 2 and a message.
    Use --tools-file to get findings for such a list.

    Examples: ads-preflight lint --tools-file tools.json
              ads-preflight lint -- python server.py
    """
    if tools_file is None and not server:
        _usage_error("lint", "give --tools-file FILE or a server command after --")
    if tools_file is not None and server:
        _usage_error("lint", "give either --tools-file or a server command, not both")
    try:
        if tools_file is not None:
            source = str(tools_file)
            tools = load_tools(tools_file)
        else:
            source = shlex.join(server or [])
            tools = _fetch_tools(server or [], timeout)
    except (ToolsFileError, ServerError) as error:
        _usage_error("lint", str(error))
    findings = linter.lint(tools)
    if output_format is OutputFormat.json:
        typer.echo(render_json(findings, source, len(tools)))
    else:
        typer.echo(render_terminal(findings, source, len(tools), color=True))
    if linter.failed(findings, strict):
        raise typer.Exit(code=1)