"""Data models shared across ads-preflight."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# What a server claims about a tool: "read" (read-only), "write", or "unknown" (no claim).
DeclaredAccess = Literal["read", "write", "unknown"]


class CallRecord(BaseModel):
    """Immutable record of a single MCP tool call, shared by every ads-preflight feature.

    The client creates one per call, and the runner, linter, reporters and snapshots
    all read from it. Records serialize to JSON, one per line in JSONL sessions.

    Use `.text` to read the text content of `result`. To change a record, build a new one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str  # Name of the tool that was called.
    arguments: dict[str, Any]  # Arguments sent to the tool, as a JSON-style dict.
    result: Any  # The server's response, either a string or a list of content blocks.
    is_error: bool = False  # True if the server reported the call as failed.
    latency_ms: float = Field(ge=0, allow_inf_nan=False)  # Call time, in milliseconds.

    @property
    def text(self) -> str:
        """Text content of the result, joined with newlines."""
        if isinstance(self.result, str):
            return self.result
        if isinstance(self.result, list):
            parts = [
                block["text"]
                for block in self.result
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            return "\n".join(parts)
        return ""

class ToolAnnotations(BaseModel):
    """The hints a server gives about one of its tools.

    Hints are the server's claims, not guarantees. An unsent hint is None, not False.
    Hints are stored as sent, even if not boolean, so a lint rule can report them.
    Fields match the wire format (`readOnlyHint`). Unknown fields are kept.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    title: str | None = None
    readOnlyHint: Any = None
    destructiveHint: Any = None
    idempotentHint: Any = None
    openWorldHint: Any = None


class ToolDef(BaseModel):
    """One tool as a server lists it in `tools/list`.

    Lenient on purpose: only `name` is required, so a badly written tool can still be
    loaded and reported on. Unknown fields are kept. Fields match the wire format, so
    `input_schema` is kept as an unknown field, not read as `inputSchema`.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str  # The name used to call the tool.
    title: str | None = None  # Human-readable name.
    description: str | None = None  # What the tool does, for the model to read.
    inputSchema: dict[str, Any] | None = None  # JSON Schema of the arguments.
    outputSchema: dict[str, Any] | None = None  # JSON Schema of the result.
    annotations: ToolAnnotations | None = None  # The server's hints about the tool.

    @property
    def declared_access(self) -> DeclaredAccess:
        """What the server claims about whether this tool changes anything.

        "read" if `readOnlyHint` is True, "write" if it is False, and "unknown" if the
        server made no claim or sent something that is not a boolean, such as the string
        "true". Silence and mistakes are never read as safe. This is only the server's
        own word, so it can tell you a tool is risky but cannot prove it is safe.
        """
        hint = self.annotations.readOnlyHint if self.annotations else None
        if hint is True:
            return "read"
        if hint is False:
            return "write"
        return "unknown"
