"""A hand-written MCP server that misbehaves on purpose, to test how clients cope.

Usage: python misbehaving_server.py MODE
Modes:
  chatty         writes 100 lines to stderr, then works normally
  flood-at-start writes to stderr as fast as it can before answering anything
  flood-stderr   answers the handshake, then floods stderr when asked for its tools

Flooding stops by itself after 30 seconds, so a bug in a test cannot leave it running.
It speaks the older protocol. Keep this file free of imports from ads_preflight and mcp.
"""

import json
import sys
import time

MAX_FLOOD_SECONDS = 30
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
    end = time.time() + MAX_FLOOD_SECONDS
    while time.time() < end:
        sys.stderr.write(line * 100)
        sys.stderr.flush()


def main(mode: str) -> None:
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
            if mode == "flood-stderr":
                flood()
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL]}})
        elif request_id is not None:
            error = {"code": -32601, "message": "Method not found"}
            send({"jsonrpc": "2.0", "id": request_id, "error": error})


if __name__ == "__main__":
    main(sys.argv[1])
