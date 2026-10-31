"""Tests for talking to a server over HTTP, with the client and the command line.

These start the example server on a free port of this computer.

Each test docstring has three parts:
  What:     the behavior being checked
  Why:      the mistake or risk the test protects against
  Expected: the output that counts as a pass
"""

import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED
from typer.testing import CliRunner

from ads_preflight import cli
from ads_preflight.cli import app
from ads_preflight.client import (
    HttpTarget,
    ServerError,
    ServerSession,
    connect_http,
    inspect_server,
    list_server_tools,
)
from ads_preflight.models import ServerInfo, ToolDef

HTTP_SERVER = Path(__file__).parent.parent / "examples" / "servers" / "notes_server_http.py"
runner = CliRunner()


def run(coroutine_function):
    return anyio.run(coroutine_function)


def free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def running_server(**settings):
    """Run the example HTTP server on a free port and give its URL. It is stopped after."""
    port = free_port()
    environment = {k: v for k, v in os.environ.items() if k not in ("EXPECTED_TOKEN", "FAIL_WITH")}
    environment.update(settings)
    process = subprocess.Popen(
        [sys.executable, str(HTTP_SERVER), str(port)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("the example HTTP server stopped while starting") from None
                time.sleep(0.1)
        else:
            raise RuntimeError("the example HTTP server did not start in 30 seconds")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def inspect_url(url, timeout=20):
    return run(lambda: inspect_server(HttpTarget(url), timeout=timeout))


# --- Client over HTTP ---------------------------------------------------------------------


def test_inspect_server_reads_a_server_over_http():
    """Connect to the example server over HTTP.

    What:     The server's name, a protocol version and its four tools.
    Why:      This is the whole path: HTTP connection, handshake, tool list.
    Expected: Name "Notes" and the four tool names.
    """
    with running_server() as url:
        info, tools = inspect_url(url)
    assert info.name == "Notes"
    assert info.protocol_version
    assert [tool.name for tool in tools] == ["list_notes", "get_note", "add_note", "delete_note"]


def test_list_server_tools_works_over_http():
    """List the tools of the example server over HTTP with the one-call helper.

    What:     Four tools, with the access each declares.
    Why:      `lint` uses this helper.
    Expected: list_notes is "read" and add_note is "write".
    """
    with running_server() as url:
        tools = run(lambda: list_server_tools(HttpTarget(url), timeout=20))
    access = {tool.name: tool.declared_access for tool in tools}
    assert access["list_notes"] == "read"
    assert access["add_note"] == "write"


def test_a_server_that_wants_credentials_gives_a_clear_message():
    """Connect to a server that answers HTTP 401 to a request without a token.

    What:     ServerError that names the status and says credentials are probably needed.
    Why:      The SDK only says "Server returned an error response".
    Expected: ServerError with "HTTP 401" and "credentials".
    """
    with running_server(EXPECTED_TOKEN="a-secret-token") as url:
        with pytest.raises(ServerError) as error:
            inspect_url(url)
    assert "HTTP 401" in str(error.value)
    assert "credentials" in str(error.value)


def test_a_wrong_path_gives_a_clear_message():
    """Connect to a path where there is no MCP server.

    What:     ServerError that says nothing was found, with the status.
    Why:      A typo in the path is the most common mistake with a URL.
    Expected: ServerError with "HTTP 404" and "check the URL".
    """
    with running_server() as url:
        with pytest.raises(ServerError) as error:
            inspect_url(url.replace("/mcp", "/nope"))
    assert "HTTP 404" in str(error.value)
    assert "check the URL" in str(error.value)


def test_a_server_that_fails_gives_a_clear_message():
    """Connect to a server that answers HTTP 500 to everything.

    What:     ServerError that says the server failed, with the status.
    Why:      A server error is not the caller's mistake and should say so.
    Expected: ServerError with "failed (HTTP 500)".
    """
    with running_server(FAIL_WITH="500") as url:
        with pytest.raises(ServerError) as error:
            inspect_url(url)
    assert "failed (HTTP 500)" in str(error.value)


def test_nothing_listening_gives_a_clear_message():
    """Connect to a port where nothing is listening.

    What:     ServerError that names the host and port.
    Why:      "All connection attempts failed" alone does not say where.
    Expected: ServerError with "cannot connect to 127.0.0.1:" and the port.
    """
    port = free_port()
    with pytest.raises(ServerError) as error:
        inspect_url(f"http://127.0.0.1:{port}/mcp")
    assert f"cannot connect to 127.0.0.1:{port}" in str(error.value)


def test_a_server_that_never_answers_times_out():
    """Connect to a port that accepts connections and never replies.

    What:     ServerError about the handshake time limit, in bounded time.
    Why:      A silent server must not hang the command.
    Expected: ServerError with "did not finish the handshake within 1 seconds", in under 15
              seconds.
    """
    with closing(socket.socket()) as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        url = f"http://127.0.0.1:{silent.getsockname()[1]}/mcp"
        start = time.perf_counter()
        with pytest.raises(ServerError) as error:
            inspect_url(url, timeout=1)
    assert time.perf_counter() - start < 15
    assert "did not finish the handshake within 1 seconds" in str(error.value)


def test_environment_variables_cannot_be_passed_to_a_url():
    """Inspect a URL while asking for environment variables to be passed.

    What:     ServerError.
    Why:      There is no process to give them to, and ignoring them would hide a mistake.
    Expected: ServerError mentioning "environment variables".
    """
    with pytest.raises(ServerError) as error:
        run(lambda: inspect_server(HttpTarget("http://127.0.0.1:1/mcp"), env={"A": "bcde"}))
    assert "environment variables" in str(error.value)


def test_connect_http_hands_the_opener_what_it_needs():
    """Connect with a fake opener.

    What:     The opener gets the URL, the time limit, a list for raw replies, a list for
              status codes and the headers. The session works.
    Why:      The real opener fills those lists as replies arrive and sends the headers.
    Expected: The values passed, and one tool from the fake client.
    """
    seen = []

    @asynccontextmanager
    async def open_client(url, connect_timeout, raw_pages, statuses, headers):
        seen.append((url, connect_timeout, type(raw_pages).__name__, statuses, dict(headers)))
        client = SimpleNamespace(
            list_tools=lambda cursor=None: _page(), server_info=None, protocol_version="p"
        )
        yield client

    async def _page():
        tool = SimpleNamespace(model_dump=lambda **_: {"name": "a"})
        return SimpleNamespace(tools=[tool], next_cursor=None)

    async def main():
        async with connect_http(
            "https://example.com/mcp", connect_timeout=7, open_client=open_client
        ) as active:
            return await active.list_tools()

    tools = run(main)
    assert [tool.name for tool in tools] == ["a"]
    assert seen == [("https://example.com/mcp", 7, "RawPages", [], {})]


def test_a_later_failure_mentions_the_http_status():
    """Fail a request after an HTTP error response was seen.

    What:     The error ends with the status the server answered.
    Why:      The SDK's own message does not carry it.
    Expected: ServerError with "the server answered HTTP 401".
    """

    class Closed:
        async def list_tools(self, cursor=None):
            raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

    async def main():
        await ServerSession(Closed(), statuses=[401]).list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert "the server answered HTTP 401" in str(error.value)


# --- Command line ---------------------------------------------------------------------------


def test_inspect_url_prints_the_server():
    """Inspect the example server with --url.

    What:     The server line and the tools.
    Why:      This is the whole path for a user: command line, HTTP, report.
    Expected: exit code 0, "Server: Notes" and "4 tools: 2 read, 2 write".
    """
    with running_server() as url:
        result = runner.invoke(app, ["inspect", "--url", url, "--timeout", "20"])
    assert result.exit_code == 0, result.output
    assert "Server: Notes" in result.output
    assert "4 tools: 2 read, 2 write" in result.output


def test_lint_url_checks_the_tools():
    """Lint the example server with --url.

    What:     The tools are checked and the report names the URL.
    Why:      Linting over HTTP must give the same kind of report as over stdio.
    Expected: exit code 0, "4 tools" and the URL in the output.
    """
    with running_server() as url:
        result = runner.invoke(app, ["lint", "--url", url, "--timeout", "20"])
    assert result.exit_code == 0, result.output
    assert "4 tools" in result.output
    assert url in result.output


def test_a_server_that_wants_credentials_exits_2():
    """Inspect a server that answers HTTP 401.

    What:     Exit code 2 and the clear message.
    Why:      A check that could not run is not a failed check.
    Expected: exit code 2 and "HTTP 401" in the output.
    """
    with running_server(EXPECTED_TOKEN="a-secret-token") as url:
        result = runner.invoke(app, ["inspect", "--url", url, "--timeout", "20"])
    assert result.exit_code == 2
    assert "HTTP 401" in result.output


def test_a_command_and_a_url_together_are_refused():
    """Inspect with both a server command and --url.

    What:     Exit code 2.
    Why:      It is unclear which one to use.
    Expected: exit code 2 and "not both" in the output.
    """
    result = runner.invoke(
        app, ["inspect", "--url", "https://example.com/mcp", "--", "python", "s.py"]
    )
    assert result.exit_code == 2
    assert "not both" in result.output


def test_env_cannot_be_used_with_a_url():
    """Inspect a URL with --env.

    What:     Exit code 2 that says --env is for a server started from a command.
    Why:      There is no process to give the variable to.
    Expected: exit code 2 and "started from a command" in the output.
    """
    result = runner.invoke(app, ["inspect", "--url", "https://example.com/mcp", "--env", "A"])
    assert result.exit_code == 2
    assert "started from a command" in result.output


def test_lint_cannot_combine_a_tools_file_and_a_url(tmp_path):
    """Lint with both --tools-file and --url.

    What:     Exit code 2.
    Why:      It is unclear which one to check.
    Expected: exit code 2 and "only one" in the output.
    """
    path = tmp_path / "tools.json"
    path.write_text('{"tools": []}', encoding="utf-8")
    result = runner.invoke(
        app, ["lint", "--tools-file", str(path), "--url", "https://example.com/mcp"]
    )
    assert result.exit_code == 2
    assert "only one" in result.output


def test_plain_http_to_another_computer_exits_2():
    """Inspect an http:// URL for another computer.

    What:     Exit code 2 that says to use https.
    Why:      Anything sent over plain http can be read on the way.
    Expected: exit code 2 and "https://" in the output.
    """
    result = runner.invoke(app, ["inspect", "--url", "http://example.com/mcp"])
    assert result.exit_code == 2
    assert "https://" in result.output


def test_a_password_in_the_url_exits_2_and_is_not_echoed():
    """Inspect a URL with a password in it.

    What:     Exit code 2, and the password does not appear in the output.
    Why:      The tool must not print a secret that was typed on the command line.
    Expected: exit code 2 and "hunter2" absent from the output.
    """
    result = runner.invoke(app, ["inspect", "--url", "https://admin:hunter2@example.com/mcp"])
    assert result.exit_code == 2
    assert "hunter2" not in result.output


def test_a_token_in_the_query_is_hidden_in_the_report(monkeypatch):
    """Inspect a URL whose query carries a token, with the connection faked.

    What:     The report shows the URL with ?*** in place of the query.
    Why:      Reports end up in CI logs.
    Expected: "https://example.com/mcp?***" in the JSON source, and no token anywhere.
    """
    seen = []

    def fetch(target, timeout, env):
        seen.append(target)
        return ServerInfo(name="x"), [ToolDef(name="t")]

    monkeypatch.setattr(cli, "_fetch_inspection", fetch)
    result = runner.invoke(
        app, ["inspect", "--url", "https://example.com/mcp?token=abc123", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert '"source": "https://example.com/mcp?***"' in result.output
    assert "abc123" not in result.output
    assert seen == [HttpTarget("https://example.com/mcp?token=abc123")]


# --- Credentials ------------------------------------------------------------------------------

TOKEN = "tok-xyz-123456"


def inspect_with(url, headers, timeout=20):
    return run(lambda: inspect_server(HttpTarget(url, headers), timeout=timeout))


def test_a_bearer_token_is_sent_with_every_request():
    """Inspect a server that wants a bearer token, giving the right one.

    What:     The handshake and the tool list work.
    Why:      The headers must reach the SDK's HTTP client, not only the first request.
    Expected: The server name and its four tools.
    """
    with running_server(EXPECTED_TOKEN=TOKEN) as url:
        info, tools = inspect_with(url, {"Authorization": f"Bearer {TOKEN}"})
    assert info.name == "Notes"
    assert len(tools) == 4


def test_a_custom_header_is_sent():
    """Inspect a server that wants a key in a custom header.

    What:     The handshake works when the header is given.
    Why:      --header-env must work for servers that do not use Authorization.
    Expected: Four tools.
    """
    with running_server(EXPECTED_HEADER="X-Api-Key=key-98765") as url:
        _, tools = inspect_with(url, {"X-Api-Key": "key-98765"})
    assert len(tools) == 4


def test_a_server_without_credentials_points_to_the_options():
    """Inspect a server that answers 401 when no credentials are given.

    What:     The message names --bearer-env and --header-env.
    Why:      The user should not have to look up how to pass a token.
    Expected: ServerError with both option names.
    """
    with running_server(EXPECTED_TOKEN=TOKEN) as url:
        with pytest.raises(ServerError) as error:
            inspect_url(url)
    assert "--bearer-env" in str(error.value)
    assert "--header-env" in str(error.value)


def test_wrong_credentials_are_reported_without_repeating_them():
    """Inspect a server with a token it does not accept.

    What:     The message says the credentials were refused and shows no secret.
    Why:      Telling "needs credentials" to someone who sent some would send them the wrong
              way, and the token must not appear in logs.
    Expected: "refused the credentials", and the token absent from the message.
    """
    wrong = "tok-wrong-000000"
    with running_server(EXPECTED_TOKEN=TOKEN) as url:
        with pytest.raises(ServerError) as error:
            inspect_with(url, {"Authorization": f"Bearer {wrong}"})
    assert "refused the credentials" in str(error.value)
    assert wrong not in str(error.value)


def test_a_redirect_is_not_followed_so_credentials_stay_with_the_given_url():
    """Inspect a server that redirects every request to a closed port.

    What:     A clear error about the redirect, and no connection to the new address.
    Why:      Following it could send the credentials to a host the user never named.
    Expected: ServerError with "redirected" and "HTTP 307".
    """
    with running_server(REDIRECT_TO="http://127.0.0.1:1/mcp") as url:
        with pytest.raises(ServerError) as error:
            inspect_with(url, {"Authorization": f"Bearer {TOKEN}"})
    assert "redirected" in str(error.value)
    assert "HTTP 307" in str(error.value)
    assert TOKEN not in str(error.value)


def test_headers_do_not_appear_in_repr_or_comparison():
    """Print an HttpTarget that carries a token.

    What:     repr shows the URL only, and two targets with different headers are equal.
    Why:      A secret in a repr ends up in a failed assertion or a log line.
    Expected: The token absent from repr, and equality by URL.
    """
    target = HttpTarget("https://example.com/mcp", {"Authorization": f"Bearer {TOKEN}"})
    assert TOKEN not in repr(target)
    assert target == HttpTarget("https://example.com/mcp")


def test_a_secret_in_a_start_up_failure_is_hidden():
    """Fail the handshake with an error text that contains the token.

    What:     The message shows *** in place of the token and of the bare token.
    Why:      Error text comes from the SDK or the server and may echo what was sent.
    Expected: ServerError without the token.
    """

    @asynccontextmanager
    async def open_client(url, connect_timeout, raw_pages, statuses, headers):
        raise RuntimeError(f"bad handshake for {TOKEN} and Bearer {TOKEN}")
        yield

    async def main():
        async with connect_http(
            "https://example.com/mcp",
            headers={"Authorization": f"Bearer {TOKEN}"},
            open_client=open_client,
        ):
            pass

    with pytest.raises(ServerError) as error:
        run(main)
    assert TOKEN not in str(error.value)
    assert "***" in str(error.value)


def test_a_secret_in_a_later_error_is_hidden():
    """Fail a request with an MCP error that echoes the token.

    What:     The message shows *** in place of the token.
    Why:      A server may repeat the credentials it received.
    Expected: ServerError without the token.
    """

    class Client:
        async def list_tools(self, cursor=None):
            raise MCPError(code=-32601, message=f"unknown credentials {TOKEN}")

    @asynccontextmanager
    async def open_client(url, connect_timeout, raw_pages, statuses, headers):
        yield Client()

    async def main():
        async with connect_http(
            "https://example.com/mcp",
            headers={"Authorization": f"Bearer {TOKEN}"},
            open_client=open_client,
        ) as active:
            await active.list_tools()

    with pytest.raises(ServerError) as error:
        run(main)
    assert TOKEN not in str(error.value)
    assert "***" in str(error.value)


# --- Command line: credentials ------------------------------------------------------------


def test_inspect_passes_the_bearer_token_to_the_client(monkeypatch):
    """Inspect a URL with --bearer-env, with the connection faked.

    What:     The target carries `Authorization: Bearer <value of the variable>`.
    Why:      The value is read from the environment and must reach the client unchanged.
    Expected: The header on the target, and the token absent from the output.
    """
    seen = []

    def fetch(target, timeout, env):
        seen.append(target)
        return ServerInfo(name="x"), [ToolDef(name="t")]

    monkeypatch.setattr(cli, "_fetch_inspection", fetch)
    result = runner.invoke(
        app,
        ["inspect", "--url", "https://example.com/mcp", "--bearer-env", "MY_TOKEN"],
        env={"MY_TOKEN": TOKEN},
    )
    assert result.exit_code == 0, result.output
    assert seen[0].headers == {"Authorization": f"Bearer {TOKEN}"}
    assert TOKEN not in result.output


def test_lint_passes_header_env_to_the_client(monkeypatch):
    """Lint a URL with --header-env, with the connection faked.

    What:     The target carries the header read from the named variable.
    Why:      lint takes the same credential options as inspect.
    Expected: The header on the target and exit code 0.
    """
    seen = []

    def fetch(target, timeout, env):
        seen.append(target)
        return [ToolDef(name="t", description="Does a thing.")]

    monkeypatch.setattr(cli, "_fetch_tools", fetch)
    result = runner.invoke(
        app,
        ["lint", "--url", "https://example.com/mcp", "--header-env", "X-Api-Key=MY_KEY"],
        env={"MY_KEY": "key-98765"},
    )
    assert result.exit_code in (0, 1), result.output
    assert seen[0].headers == {"X-Api-Key": "key-98765"}


def test_inspect_with_a_token_works_end_to_end():
    """Inspect a real local server that wants a token, through the command line.

    What:     Exit code 0 and the tool list.
    Why:      Checks the whole path from the environment variable to the HTTP header.
    Expected: exit code 0, "4 tools" in the output.
    """
    with running_server(EXPECTED_TOKEN=TOKEN) as url:
        result = runner.invoke(
            app,
            ["inspect", "--url", url, "--bearer-env", "MY_TOKEN", "--timeout", "20"],
            env={"MY_TOKEN": TOKEN},
        )
    assert result.exit_code == 0, result.output
    assert "4 tools" in result.output


def test_a_missing_token_variable_exits_2():
    """Name a variable that is not set.

    What:     Exit code 2 and a message naming the variable.
    Why:      Sending no credentials would look like a server problem.
    Expected: exit code 2 and "NOPE_TOKEN is not set".
    """
    result = runner.invoke(
        app, ["inspect", "--url", "https://example.com/mcp", "--bearer-env", "NOPE_TOKEN"]
    )
    assert result.exit_code == 2
    assert "NOPE_TOKEN is not set" in result.output


def test_a_token_typed_on_the_command_line_exits_2_and_is_not_echoed():
    """Type a token where the variable name belongs.

    What:     Exit code 2, and the token does not appear in the output.
    Why:      The tool must not print a secret that was typed on the command line.
    Expected: exit code 2 and the token absent.
    """
    result = runner.invoke(
        app, ["inspect", "--url", "https://example.com/mcp", "--bearer-env", "sk-live-hunter2xx"]
    )
    assert result.exit_code == 2
    assert "hunter2xx" not in result.output


def test_credential_options_need_a_url():
    """Give --bearer-env together with a server command.

    What:     Exit code 2 and a message about --url.
    Why:      A started server gets its secrets through --env, not headers.
    Expected: exit code 2 and "--url" in the output.
    """
    result = runner.invoke(
        app, ["inspect", "--bearer-env", "MY_TOKEN", "--", "python", "server.py"]
    )
    assert result.exit_code == 2
    assert "--url" in result.output


def test_lint_credential_options_cannot_be_used_with_a_tools_file(tmp_path):
    """Give --header-env while linting a saved file.

    What:     Exit code 2.
    Why:      A file has nothing to authenticate to.
    Expected: exit code 2 and "live server" in the output.
    """
    path = tmp_path / "tools.json"
    path.write_text('{"tools": []}', encoding="utf-8")
    result = runner.invoke(
        app, ["lint", "--tools-file", str(path), "--header-env", "X-Api-Key=MY_KEY"]
    )
    assert result.exit_code == 2
    assert "live server" in result.output


def test_a_401_without_credentials_points_to_the_options_on_the_command_line():
    """Inspect a server that wants a token without giving one.

    What:     Exit code 2 and a message that names --bearer-env.
    Why:      The next step should be clear from the message alone.
    Expected: exit code 2 and "--bearer-env" in the output.
    """
    with running_server(EXPECTED_TOKEN=TOKEN) as url:
        result = runner.invoke(app, ["inspect", "--url", url, "--timeout", "20"])
    assert result.exit_code == 2
    assert "--bearer-env" in result.output
