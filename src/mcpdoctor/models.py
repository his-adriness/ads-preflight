"""Data models shared across mcp-doctor."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class CallRecord(BaseModel):
    """Immutable record of a single MCP tool call, shared by every mcp-doctor feature.

    The client creates one per call, and the runner, linter, reporters and snapshots
    all read from it. Records serialize to JSON, one per line in JSONL sessions.


    Use `.text` to read the text content of `result`. To change a record, build a new one.
    """
    model_config = ConfigDict(frozen=True,extra="forbid")
    tool: str #Name of the tool that was called.
    arguments: dict[str, Any] #Arguments sent to the tool, as a JSON-style dict.
    result: Any #The server's response, either a string or a list of content blocks.
    is_error: bool = False #True if the server reported the call as failed. Defaults to False.
    latency_ms: float #Time the call took, in milliseconds.

    @property
    def text(self) -> str:
        """Text content of the result, joined with newlines."""
        if isinstance(self.result, str):
            return self.result
        if isinstance(self.result, list):
            parts = [
                block.get("text", "")
                for block in self.result
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(parts)
        """Return True if the call was successful."""
        return ""