"""Load a saved `tools/list` response, so tools can be checked without a live server."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ads_preflight.models import ToolDef


class ToolsFileError(Exception):
    """The tools file could not be read, or does not hold a valid list of tools.

    The message is written to be shown to the user as it is.
    """


def load_tools(path: str | Path) -> list[ToolDef]:
    """Read a tools file and return its tools in file order.

    The file is JSON in one of the shapes `parse_tools` accepts. Raises ToolsFileError,
    with a message that starts with the file name, if it cannot be read or is invalid.
    """
    path = Path(path)
    try:
        # utf-8-sig also reads files that start with a byte order mark, as Windows
        # editors often write them.
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise ToolsFileError(f"{path}: not a UTF-8 text file. Save it as UTF-8.") from error
    except OSError as error:
        raise ToolsFileError(f"{path}: cannot read file ({error.strerror or error})") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        where = f"line {error.lineno}, column {error.colno}"
        message = f"{path}: not valid JSON ({error.msg} at {where}){_json_hint(text)}"
        raise ToolsFileError(message) from error
    try:
        return parse_tools(data)
    except ToolsFileError as error:
        raise ToolsFileError(f"{path}: {error}") from error


def parse_tools(data: Any) -> list[ToolDef]:
    """Turn parsed JSON into a list of tools, in the order given.

    Accepts a bare list of tools, an object with a `tools` list (as in a `tools/list`
    result), or a whole JSON-RPC response with that object under `result`. Other keys,
    such as `nextCursor`, are ignored. An empty list is valid, and duplicate names are
    kept, because a lint rule should report them.

    Raises ToolsFileError for the first tool that is invalid, naming it by position
    and, if it has one, by name.
    """
    if isinstance(data, dict) and "tools" not in data and isinstance(data.get("error"), dict):
        detail = data["error"].get("message", "no message given")
        raise ToolsFileError(f"this is an error response from a server, not a tools list: {detail}")
    if isinstance(data, dict) and "tools" not in data and isinstance(data.get("result"), dict):
        data = data["result"]
    if isinstance(data, dict):
        if "tools" not in data:
            keys = ", ".join(sorted(data)) or "none"
            raise ToolsFileError(f"no 'tools' list found (keys found: {keys})")
        items = data["tools"]
        if not isinstance(items, list):
            raise ToolsFileError(f"'tools' must be a list, but found {_json_type_name(items)}")
    elif isinstance(data, list):
        items = data
    else:
        raise ToolsFileError(
            "expected a list of tools or an object with a 'tools' list, "
            f"but found {_json_type_name(data)}"
        )
    return [_parse_tool(item, number) for number, item in enumerate(items, start=1)]


def _parse_tool(item: Any, number: int) -> ToolDef:
    if not isinstance(item, dict):
        raise ToolsFileError(f"tool #{number} must be an object, but found {_json_type_name(item)}")
    hint = _other_format_hint(item)
    if hint:
        raise ToolsFileError(f"tool #{number} {hint}")
    try:
        return ToolDef.model_validate(item)
    except ValidationError as error:
        name = item.get("name")
        label = f"tool #{number}" + (f" ({name!r})" if isinstance(name, str) else "")
        problems = "; ".join(_format_problem(problem) for problem in error.errors())
        raise ToolsFileError(f"{label}: {problems}") from error


def _json_hint(text: str) -> str:
    """Suggest a fix when invalid JSON looks like a hand-edited file."""
    if re.search(r",\s*[}\]]", text):
        return ". JSON does not allow a comma before } or ]"
    if re.search(r"(?<![:/])//|/\*", text):
        return ". JSON does not allow comments"
    return ""


def _other_format_hint(item: dict[str, Any]) -> str | None:
    """Recognise tool definitions written for another API instead of MCP."""
    if "name" in item:
        return None
    if isinstance(item.get("function"), dict):
        return (
            "looks like an OpenAI function definition ('type' and 'function'). "
            "ads-preflight reads MCP tools, where 'name' is at the top level of each tool"
        )
    if "functionDeclarations" in item:
        return (
            "looks like a Gemini tool ('functionDeclarations'). "
            "ads-preflight reads MCP tools, where each tool has its own 'name'"
        )
    return None


def _format_problem(problem: Any) -> str:
    where = ".".join(str(part) for part in problem["loc"])
    return f"{where}: {problem['msg']}" if where else problem["msg"]


def _json_type_name(value: Any) -> str:
    """Name a parsed JSON value's type in plain words."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    return "an object"
