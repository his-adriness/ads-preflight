"""Tests for the command-line skeleton."""

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

import ads_preflight
from ads_preflight.cli import app

runner = CliRunner()


# --- The app and its version ------------------------------------------------


def test_app_is_a_typer_app():
    """Expose the app that pyproject.toml points to.

    What:     ads_preflight.cli.app is a Typer app.
    Why:      pyproject.toml maps the ads-preflight command to ads_preflight.cli:app. If it is
              missing or has another type, the installed command breaks.
    Expected: isinstance(app, typer.Typer)
    """
    assert isinstance(app, typer.Typer)


def test_version_is_known():
    """Read the version from the installed package.

    What:     __version__ is a real version string and not the fallback.
    Why:      The fallback "unknown" means the package metadata was not found, which
              would also hide the real version in every message that prints it.
    Expected: __version__ is a non-empty string other than "unknown".
    """
    assert isinstance(ads_preflight.__version__, str)
    assert ads_preflight.__version__ not in ("", "unknown")


def test_version_flag_prints_name_and_version():
    """Print the version for --version.

    What:     The output is "ads-preflight <version>" and the exit code is 0.
    Why:      The first thing people run to check the install.
    Expected: output == "ads-preflight " + __version__
    """
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"ads-preflight {ads_preflight.__version__}"


# --- Help -------------------------------------------------------------------


def test_exactly_the_three_v01_commands_are_registered():
    """Register only the commands that are planned for v0.1.

    What:     The app has inspect, run and lint, and nothing else.
    Why:      Help should not advertise commands that are far from existing. Checking
              the registered names, not the help text, avoids matching words in
              descriptions.
    Expected: The registered command names are exactly inspect, lint and run.
    """
    assert sorted(get_command(app).commands) == ["inspect", "lint", "run"]


def test_help_lists_the_three_commands():
    """Show every command in --help.

    What:     --help exits 0 and mentions inspect, run and lint.
    Why:      Users find commands through the help text.
    Expected: exit code 0 and the three names in the output.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("inspect", "run", "lint"):
        assert name in result.output


def test_no_arguments_shows_the_help():
    """Show help instead of failing silently when nothing is typed.

    What:     Running with no arguments prints the usage text and the commands.
    Why:      A helpful first impression. The exit code is not checked, because it
              differs between Click versions.
    Expected: "Usage" and the three command names appear in the output.
    """
    result = runner.invoke(app, [])
    assert "Usage" in result.output
    for name in ("inspect", "run", "lint"):
        assert name in result.output


@pytest.mark.parametrize("command", ["inspect", "run", "lint"])
def test_command_help_works(command):
    """Show help for each command.

    What:     <command> --help exits 0 and prints the usage text.
    Why:      Confirms each command is registered and its arguments are declared.
    Expected: exit code 0, with "Usage" in the output.
    """
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


# --- Usage errors -----------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [["inspect"], ["inspect", "--"]],
    ids=["inspect", "inspect-empty-dash"],
)
def test_inspect_requires_a_server(args):
    """Reject a missing server command.

    What:     Without a server, the command is a usage error.
    Why:      inspect has nothing to do without a server to talk to.
    Expected: exit code 2 and "--url" in the output, which names the other way to give one.
    """
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "--url" in result.output


def test_run_requires_a_test_file():
    """Reject run without a test file.

    What:     "run" alone is a usage error.
    Why:      There is nothing to run.
    Expected: exit code 2 and "Missing argument" in the output.
    """
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output


@pytest.mark.parametrize("command", ["frobnicate", "snapshot", "bench"])
def test_unknown_and_unregistered_commands_are_usage_errors(command):
    """Reject commands that do not exist.

    What:     A typo, or a command not planned for v0.1, is a usage error.
    Why:      Failing loudly beats silently doing nothing.
    Expected: exit code 2 and "No such command" in the output.
    """
    result = runner.invoke(app, [command])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_forgetting_the_double_dash_with_server_flags_is_an_error():
    """Document what happens when -- is forgotten and the server has flags.

    What:     "inspect python server.py --port 0" is a usage error.
    Why:      The flag --port looks like one of our own options. This records the
              current behavior, so a friendlier message can be added later.
    Expected: exit code 2 and "No such option" in the output.
    """
    result = runner.invoke(app, ["inspect", "python", "server.py", "--port", "0"])
    assert result.exit_code == 2
    assert "No such option" in result.output
