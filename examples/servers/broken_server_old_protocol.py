"""A hand-written MCP server that can send malformed tool lists. It does not use the SDK.

Usage: python broken_server_old_protocol.py VARIANT
Variants: good, wrong-key (`input_schema` instead of `inputSchema`), no-schema,
string-hint (`readOnlyHint` is the string "true").

It speaks the older protocol and answers `initialize` and `tools/list` only. The SDK's own
server refuses to produce malformed tool lists, so they are written by hand here. Keep this
file free of imports from ads_preflight and from mcp.
"""

import json
import sys

GOOD_TOOL = {
    "name": "ok",
    "description": "A fine tool.",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True},
}

VARIANTS = {
    "good": [GOOD_TOOL],
    "wrong-key": [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
    "no-schema": [{"name": "t", "description": "d"}],
    "string-hint": [
        {
            "name": "t",
            "description": "d",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": "true"},
        }
    ],
}


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main(variant: str) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "raw", "version": "0"},
            }
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": VARIANTS[variant]}})
        elif request_id is not None:
            error = {"code": -32601, "message": "Method not found"}
            send({"jsonrpc": "2.0", "id": request_id, "error": error})


if __name__ == "__main__":
    main(sys.argv[1])
