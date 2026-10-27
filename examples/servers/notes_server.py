"""A small, well-formed MCP server for trying ads-preflight. It stores notes in memory.

Try it with: ads-preflight inspect -- python examples/servers/notes_server.py
"""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("Notes")

NOTES: dict[str, str] = {"1": "first note", "2": "second note"}


@server.tool(
    title="List notes",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def list_notes() -> list[str]:
    """List the ids of all notes."""
    return sorted(NOTES)


@server.tool(
    title="Get a note",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def get_note(note_id: str) -> str:
    """Return the text of one note. Fails if the note does not exist."""
    if note_id not in NOTES:
        raise ValueError(f"no note with id {note_id}")
    return NOTES[note_id]


@server.tool(
    title="Add a note",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
)
def add_note(note_id: str, text: str) -> str:
    """Add a note, or replace the text of an existing one."""
    NOTES[note_id] = text
    return f"saved {note_id}"


@server.tool(
    title="Delete a note",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
)
def delete_note(note_id: str) -> str:
    """Delete a note. This cannot be undone."""
    NOTES.pop(note_id, None)
    return f"deleted {note_id}"


if __name__ == "__main__":
    server.run()
