"""Command-line interface: ads-preflight inspect, run and lint.

`lint --tools-file` works. `inspect`, `run` and linting a live server are stubs for now:
they only parse their options and will call library code once that code exists.
"""

import shlex
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from ads_preflight import __version__, linter
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
    """Stop with exit code 2, which means the input was wrong, not that checks failed."""
    typer.echo(f"ads-preflight {command}: {clean_text(message)}", err=True)
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


class OutputFormat(StrEnum):
    terminal = "terminal"
    json = "json"


@app.command()
def lint(
    server: Annotated[
        list[str] | None,
        typer.Argument(help="Server command after a double dash. Not built yet."),
    ] = None,
    tools_file: Annotated[
        Path | None,
        typer.Option("--tools-file", help="A saved tools/list response (JSON) to check."),
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="How to print the findings.")
    ] = OutputFormat.terminal,
    strict: Annotated[bool, typer.Option("--strict", help="Also fail on warnings.")] = False,
) -> None:
    """Check a server's tools for common problems.

    Exit code 0: no errors. 1: errors (or warnings, with --strict). 2: input could not be read.

    Example: ads-preflight lint --tools-file tools.json
    """
    if tools_file is None and not server:
        _usage_error("lint", "give --tools-file FILE (checking a live server is not built yet)")
    if tools_file is None:
        _not_implemented("lint", server)
    if server:
        _usage_error("lint", "give either --tools-file or a server command, not both")
    try:
        tools = load_tools(tools_file)
    except ToolsFileError as error:
        _usage_error("lint", str(error))
    findings = linter.lint(tools)
    if output_format is OutputFormat.json:
        typer.echo(render_json(findings, str(tools_file), len(tools)))
    else:
        typer.echo(render_terminal(findings, str(tools_file), len(tools), color=True))
    if linter.failed(findings, strict):
        raise typer.Exit(code=1)
