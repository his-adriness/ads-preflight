"""A hand-written MCP server that shows whether an environment variable reached it.

Usage: python env_probe_server.py NAME [leak]

It lists one tool, named `NAME_is_set` or `NAME_is_missing`, and never prints the value.
With `leak` it instead writes the value to stderr and exits, like a server that crashes
and prints its own configuration. It speaks the older protocol and answers `initialize`
and `tools/list` only. Keep this file free of imports from ads_preflight and from mcp.
"""

import json
import os
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main(name: str, leak: bool) -> None:
    value = os.environ.get(name)
    if leak:
        print(f"starting with {name}={value}", file=sys.stderr, flush=True)
        sys.exit(1)
    tool = {
        "name": f"{name}_is_{'set' if value is not None else 'missing'}",
        "description": "Shows whether the variable reached the server.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    }
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "env-probe", "version": "0"},
            }
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [tool]}})
        elif request_id is not None:
            error = {"code": -32601, "message": "Method not found"}
            send({"jsonrpc": "2.0", "id": request_id, "error": error})


if __name__ == "__main__":
    main(sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == "leak")
