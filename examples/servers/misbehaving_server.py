"""A hand-written MCP server that misbehaves on purpose, to test how clients cope.

Usage: python misbehaving_server.py MODE [HEARTBEAT_FILE]
Modes:
  chatty              writes 100 lines to stderr, then works normally
  flood-at-start      writes to stderr as fast as it can before answering anything
  flood-stderr        answers the handshake, then floods stderr when asked for its tools
  exit-after-handshake  exits when asked for its tools
  half-line           starts a reply to the tools request, then exits
  stdout-noise-hang   prints a line that is not an MCP message, then never answers
  hang-on-tools       never answers the tools request and ignores the stop signal
  ignore-shutdown     works normally, then keeps running after its input is closed

With HEARTBEAT_FILE, the server rewrites that file with the time every 0.1 seconds, so a
test can check that the process is really gone. Anything that waits or floods stops by
itself after a minute, so a bug in a test cannot leave a process running. It speaks the
older protocol. Keep this file free of imports from ads_preflight and mcp.
"""

import json
import signal
import sys
import threading
import time

MAX_SECONDS = 60
TOOL = {
    "name": "ok",
    "description": "A fine tool.",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True},
}


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def flood() -> None:
    line = "flood line " + "x" * 1000 + "\n"
    end = time.time() + MAX_SECONDS
    while time.time() < end:
        sys.stderr.write(line * 100)
        sys.stderr.flush()


def start_heartbeat(path: str) -> None:
    def beat() -> None:
        while True:
            with open(path, "w", encoding="utf-8") as heartbeat:
                heartbeat.write(str(time.time()))
            time.sleep(0.1)

    threading.Thread(target=beat, daemon=True).start()


def wait_a_long_time() -> None:
    time.sleep(MAX_SECONDS)


def on_tools_request(mode: str, request_id: int) -> None:
    if mode == "flood-stderr":
        flood()
    elif mode == "exit-after-handshake":
        sys.exit(3)
    elif mode == "half-line":
        sys.stdout.write(f'{{"jsonrpc": "2.0", "id": {request_id}, "res')
        sys.stdout.flush()
        sys.exit(0)
    elif mode == "stdout-noise-hang":
        print("server v1.0 listening on stdio", flush=True)
        wait_a_long_time()
    elif mode == "hang-on-tools":
        wait_a_long_time()
    send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL]}})


def main(mode: str, heartbeat_file: str | None) -> None:
    if heartbeat_file:
        start_heartbeat(heartbeat_file)
    if mode in ("hang-on-tools", "ignore-shutdown"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode == "chatty":
        for number in range(100):
            print(f"log line {number}", file=sys.stderr)
        sys.stderr.flush()
    elif mode == "flood-at-start":
        flood()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "misbehaving", "version": "0"},
            }
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "tools/list":
            on_tools_request(mode, request_id)
        elif request_id is not None:
            error = {"code": -32601, "message": "Method not found"}
            send({"jsonrpc": "2.0", "id": request_id, "error": error})
    if mode == "ignore-shutdown":
        wait_a_long_time()  # The input is closed: a well-behaved server would exit here.


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
