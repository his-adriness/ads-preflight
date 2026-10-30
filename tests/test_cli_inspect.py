"""Tests for the inspect command, run through the command line.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ads_preflight import cli
from ads_preflight.cli import app
from ads_preflight.client import ServerError
from ads_preflight.models import ServerInfo, ToolDef

runner = CliRunner()

SERVER_FILE = Path(__file__).parent.parent / "examples" / "servers" / "notes_server.py"
OLD_SERVER = SERVER_FILE.parent / "broken_server_old_protocol.py"
PROBE_SERVER = SERVER_FILE.parent / "env_probe_server.py"

INFO = ServerInfo(name="Notes", version="1.0", protocol_version="2026-07-28")
TOOLS = [
    ToolDef.model_validate(
        {"name": "list_notes", "description": "List notes.", "annotations": {"readOnlyHint": True}}
    ),
    ToolDef.model_validate({"name": "add_note", "annotations": {"readOnlyHint": False}}),
]


def fake_server(monkeypatch, info=INFO, tools=TOOLS, error=None):
    """Replace the live fetch with canned data or an error. Returns the calls made."""
    calls = []

    def fetch(server, timeout, env):
        calls.append((list(server), timeout, env))
        if error:
            raise error
        return info, list(tools)

    monkeypatch.setattr(cli, "_fetch_inspection", fetch)
    return calls


def test_inspect_prints_the_server_and_its_tools(monkeypatch):
    """Inspect a server, with the server faked.

    What:     The server line, the count line and a row per tool.
    Why:      This is the whole point of the command.
    Expected: exit code 0 and the expected text in the output.
    """
    calls = fake_server(monkeypatch)
    result = runner.invoke(app, ["inspect", "--", "python", "server.py"])
    assert result.exit_code == 0
    assert "Server: Notes 1.0 (protocol 2026-07-28)" in result.output
    assert "2 tools: 1 read, 1 write" in result.output
    assert "list_notes" in result.output
    assert calls == [(["python", "server.py"], 30.0, None)]


def test_inspect_never_fails_on_what_it_finds(monkeypatch):
    """Inspect a server whose only tool says nothing about access.

    What:     Exit code 0.
    Why:      Judging is the job of lint. Inspect only describes.
    Expected: exit code 0 and "1 unknown" in the output.
    """
    silent = [ToolDef(name="mystery")]
    fake_server(monkeypatch, tools=silent)
    result = runner.invoke(app, ["inspect", "--", "python", "server.py"])
    assert result.exit_code == 0
    assert "1 unknown" in result.output


def test_inspect_json_output_parses(monkeypatch):
    """Inspect with --format json.

    What:     The whole output is one JSON document.
    Why:      A program reading the output must not meet extra text.
    Expected: The parsed source and summary are right.
    """
    fake_server(monkeypatch)
    result = runner.invoke(app, ["inspect", "--format", "json", "--", "python", "my server.py"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["source"] == "python 'my server.py'"
    assert data["summary"] == {"tools": 2, "read": 1, "write": 1, "unknown": 0}


def test_timeout_reaches_the_client(monkeypatch):
    """Inspect with --timeout 5.

    What:     The value is passed on as a number of seconds.
    Why:      An option that is parsed but not used would silently do nothing.
    Expected: The fetch was called with 5.0.
    """
    calls = fake_server(monkeypatch)
    runner.invoke(app, ["inspect", "--timeout", "5", "--", "python", "server.py"])
    assert calls[0][1] == 5.0


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_timeout_must_be_a_positive_number(value):
    """Inspect with a timeout that makes no sense.

    What:     Exit code 2.
    Why:      A zero or negative limit would fail every server for the wrong reason.
    Expected: exit code 2.
    """
    result = runner.invoke(app, ["inspect", "--timeout", value, "--", "python", "server.py"])
    assert result.exit_code == 2


def test_unknown_format_is_a_usage_error():
    """Inspect with a format that does not exist.

    What:     Exit code 2.
    Why:      A typo must not silently fall back to another format.
    Expected: exit code 2.
    """
    result = runner.invoke(app, ["inspect", "--format", "xml", "--", "python", "server.py"])
    assert result.exit_code == 2


def test_a_server_that_cannot_be_used_exits_2_with_its_message(monkeypatch):
    """Inspect a server that fails, with the client faked.

    What:     Exit code 2, the message shown with its lines kept apart.
    Why:      The server's own output helps the developer fix it.
    Expected: exit code 2 and both lines of the message in the output.
    """
    fake_server(monkeypatch, error=ServerError("cannot start the server\n  boom"))
    result = runner.invoke(app, ["inspect", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "cannot start the server\n  boom" in result.output


def test_inspect_of_the_example_server_end_to_end():
    """Inspect the real example server in a child process.

    What:     The four tools with the access each declares.
    Why:      This is the whole path: command line, client, report.
    Expected: exit code 0, the server name, and read/write on the right rows.
    """
    result = runner.invoke(app, ["inspect", "--", sys.executable, str(SERVER_FILE)])
    assert result.exit_code == 0, result.output
    assert "Server: Notes" in result.output
    assert "4 tools: 2 read, 2 write" in result.output
    rows = {line.split()[0]: line.split()[1] for line in result.output.splitlines()[4:]}
    assert rows == {
        "list_notes": "read",
        "get_note": "read",
        "add_note": "write",
        "delete_note": "write",
    }


def test_inspect_shows_a_malformed_server_instead_of_failing():
    """Inspect the hand-written server whose tool has no inputSchema.

    What:     The tool is listed with access "unknown", and the exit code is 0.
    Why:      Inspect should show what a server sent even when it is flawed.
    Expected: exit code 0, the old protocol revision, and "unknown" in the output.
    """
    result = runner.invoke(app, ["inspect", "--", sys.executable, str(OLD_SERVER), "no-schema"])
    assert result.exit_code == 0, result.output
    assert "Server: raw 0 (protocol 2025-06-18)" in result.output
    assert "unknown" in result.output


# --- --env --------------------------------------------------------------------------------------


def test_env_values_are_read_from_the_environment_and_passed_on(monkeypatch):
    """Inspect with --env API_TOKEN, with the variable set.

    What:     The fetch gets the variable's value.
    Why:      The value must come from the caller's environment, not the command line.
    Expected: env equal to {"API_TOKEN": "tok-12345"}.
    """
    monkeypatch.setenv("API_TOKEN", "tok-12345")
    calls = fake_server(monkeypatch)
    result = runner.invoke(app, ["inspect", "--env", "API_TOKEN", "--", "python", "server.py"])
    assert result.exit_code == 0
    assert calls[0][2] == {"API_TOKEN": "tok-12345"}


def test_a_missing_variable_exits_2_without_starting_the_server(monkeypatch):
    """Inspect with --env for a variable that is not set.

    What:     Exit code 2, a message naming the variable, and no server started.
    Why:      Starting the server without it would give a confusing failure.
    Expected: exit code 2, "API_TOKEN" and "not set" in the output, and no fetch.
    """
    monkeypatch.delenv("API_TOKEN", raising=False)
    calls = fake_server(monkeypatch)
    result = runner.invoke(app, ["inspect", "--env", "API_TOKEN", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "API_TOKEN" in result.output
    assert "not set" in result.output
    assert calls == []


def test_a_value_after_the_name_is_rejected_and_not_echoed(monkeypatch):
    """Inspect with --env API_TOKEN=hunter2-secret.

    What:     Exit code 2, and the value does not appear in the output.
    Why:      The tool must not print a secret that was typed on the command line.
    Expected: exit code 2 and "hunter2-secret" absent from the output.
    """
    fake_server(monkeypatch)
    result = runner.invoke(
        app, ["inspect", "--env", "API_TOKEN=hunter2-secret", "--", "python", "server.py"]
    )
    assert result.exit_code == 2
    assert "hunter2-secret" not in result.output


def test_a_variable_reaches_a_real_server(monkeypatch):
    """Inspect the probe server with and without --env.

    What:     The tool name shows whether the variable arrived.
    Why:      This is the whole path: option, environment lookup, child process.
    Expected: "PROBE_VAR_is_set" with --env, "PROBE_VAR_is_missing" without.
    """
    monkeypatch.setenv("PROBE_VAR", "value1234")
    probe = [sys.executable, str(PROBE_SERVER), "PROBE_VAR"]
    with_env = runner.invoke(app, ["inspect", "--env", "PROBE_VAR", "--", *probe])
    without = runner.invoke(app, ["inspect", "--", *probe])
    assert "PROBE_VAR_is_set" in with_env.output
    assert "PROBE_VAR_is_missing" in without.output


def test_a_crashing_server_does_not_leak_the_value(monkeypatch):
    """Inspect a server that prints the variable's value to stderr and exits.

    What:     Exit code 2, the stderr line shown with *** and not the value.
    Why:      CI logs are often public or widely shared.
    Expected: exit code 2, "PROBE_VAR=***" in the output, and no value.
    """
    monkeypatch.setenv("PROBE_VAR", "value1234")
    command = [sys.executable, str(PROBE_SERVER), "PROBE_VAR", "leak"]
    result = runner.invoke(app, ["inspect", "--env", "PROBE_VAR", "--", *command])
    assert result.exit_code == 2
    assert "PROBE_VAR=***" in result.output
    assert "value1234" not in result.output
