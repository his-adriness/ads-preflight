"""The example notes server, served over HTTP on this computer only.

Usage: python notes_server_http.py PORT
Try it with: ads-preflight inspect --url http://127.0.0.1:PORT/mcp

Environment variables make it misbehave, so clients can be tested against failures:
  EXPECTED_TOKEN   answer HTTP 401 unless the request has "Authorization: Bearer <token>"
  EXPECTED_HEADER  answer HTTP 401 unless the request has this header, written NAME=VALUE
  FAIL_WITH        answer every request with this HTTP status code
  REDIRECT_TO      answer every request with a redirect (HTTP 307) to this URL
"""

import os
import sys

import uvicorn
from notes_server import server
from starlette.responses import JSONResponse, RedirectResponse


class Guard:
    """Wraps the server to add the failures asked for in the environment."""

    def __init__(
        self,
        app,
        token: str | None,
        fail_with: int | None,
        header: str | None = None,
        redirect_to: str | None = None,
    ):
        self.app = app
        self.token = token
        self.fail_with = fail_with
        self.redirect_to = redirect_to
        name, _, value = (header or "").partition("=")
        self.header = (name.lower().encode(), value.encode()) if name else None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if self.fail_with:
                response = JSONResponse({"error": "failing on purpose"}, self.fail_with)
                return await response(scope, receive, send)
            if self.redirect_to:
                response = RedirectResponse(self.redirect_to, status_code=307)
                return await response(scope, receive, send)
            if self.header and dict(scope["headers"]).get(self.header[0]) != self.header[1]:
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                return await response(scope, receive, send)
            if self.token:
                sent = dict(scope["headers"]).get(b"authorization", b"").decode()
                if sent != f"Bearer {self.token}":
                    response = JSONResponse({"error": "unauthorized"}, status_code=401)
                    return await response(scope, receive, send)
        await self.app(scope, receive, send)


if __name__ == "__main__":
    fail_with = os.environ.get("FAIL_WITH")
    guarded = Guard(
        server.streamable_http_app(),
        os.environ.get("EXPECTED_TOKEN") or None,
        int(fail_with) if fail_with else None,
        os.environ.get("EXPECTED_HEADER") or None,
        os.environ.get("REDIRECT_TO") or None,
    )
    uvicorn.run(guarded, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
