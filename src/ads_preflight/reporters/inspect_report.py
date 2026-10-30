"""Reports for `inspect`: what a server offers, with no judgement about it."""

import json
from collections import Counter
from collections.abc import Sequence

from ads_preflight.models import ServerInfo, ToolDef
from ads_preflight.reporters.terminal import clean_text

_ACCESS_ORDER = ("read", "write", "unknown")
_ACCESS_WIDTH = len("unknown")
_DESCRIPTION_WIDTH = 60


def render_inspect_terminal(info: ServerInfo, tools: Sequence[ToolDef]) -> str:
    """Format the server and its tools as text: a header, then one line per tool."""
    lines = [_server_line(info), _count_line(tools)]
    if tools:
        names = [clean_text(tool.name) for tool in tools]
        width = max(len("NAME"), *(len(name) for name in names))
        lines.append("")
        lines.append(f"{'NAME':<{width}}  {'ACCESS':<{_ACCESS_WIDTH}}  DESCRIPTION")
        for tool, name in zip(tools, names, strict=True):
            row = f"{name:<{width}}  {tool.declared_access:<{_ACCESS_WIDTH}}  "
            lines.append(row + _first_line(tool.description))
    return "\n".join(lines)


def render_inspect_json(source: str, info: ServerInfo, tools: Sequence[ToolDef]) -> str:
    """Format everything the server sent as JSON text, plus each tool's declared access."""
    counts = Counter(tool.declared_access for tool in tools)
    report = {
        "source": source,
        "server": {
            "name": info.name,
            "version": info.version,
            "protocolVersion": info.protocol_version,
        },
        "summary": {"tools": len(tools), **{access: counts[access] for access in _ACCESS_ORDER}},
        "tools": [_tool_json(tool) for tool in tools],
    }
    return json.dumps(report, indent=2)


def _tool_json(tool: ToolDef) -> dict[str, object]:
    data = tool.model_dump(mode="json", exclude_none=True)
    data["declaredAccess"] = tool.declared_access
    return data


def _server_line(info: ServerInfo) -> str:
    text = clean_text(info.name) if info.name else "(did not give a name)"
    if info.version:
        text += f" {clean_text(info.version)}"
    if info.protocol_version:
        text += f" (protocol {clean_text(info.protocol_version)})"
    return f"Server: {text}"


def _count_line(tools: Sequence[ToolDef]) -> str:
    if not tools:
        return "no tools"
    counts = Counter(tool.declared_access for tool in tools)
    parts = [f"{counts[access]} {access}" for access in _ACCESS_ORDER if counts[access]]
    noun = "tool" if len(tools) == 1 else "tools"
    return f"{len(tools)} {noun}: " + ", ".join(parts)


def _first_line(description: str | None) -> str:
    """The first non-empty line of a description, cut to fit."""
    for line in (description or "").splitlines():
        if line.strip():
            text = clean_text(line.strip())
            if len(text) > _DESCRIPTION_WIDTH:
                return text[: _DESCRIPTION_WIDTH - 3] + "..."
            return text
    return "(no description)"
