"""Test files: YAML that says which tools to call and what to expect from each.

`load_suite` reads and checks a file; every mistake is reported with its line number, and
nothing is run or connected. The file never holds a secret: it names environment variables
(`env`, `bearer_env`, `header_env`), exactly like the command-line options.

Writing tools is not supported yet. The file format and the checks already have a place for
it (`allow_writes`, `check_access`); `WRITES_SUPPORTED` is the one switch to turn it on.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ads_preflight.credentials import CredentialsError, build_headers
from ads_preflight.env import EnvError, resolve_env
from ads_preflight.models import ToolDef
from ads_preflight.urls import UrlError, check_url

WRITES_SUPPORTED = False
MAX_FILE_BYTES = 1_000_000
MAX_PROBLEMS_SHOWN = 8


class SuiteFileError(Exception):
    """A test file could not be read or is not valid. The message is ready to show."""


class _Strict(BaseModel):
    """Base for every part of the file: unknown fields are errors, and values are not coerced."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectSpec(_Strict):
    """What a call must look like. Every check that is given must hold."""

    is_error: StrictBool = False  # By default the call must succeed.
    text_contains: tuple[StrictStr, ...] = ()
    text_not_contains: tuple[StrictStr, ...] = ()
    text_equals: StrictStr | None = None
    structured_equals: Any = None  # Checked when the key is present, even if the value is null.
    max_latency_ms: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None

    @field_validator("text_contains", "text_not_contains", mode="before")
    @classmethod
    def _one_or_several(cls, value: Any) -> Any:
        return (value,) if isinstance(value, str) else value


class CaseSpec(_Strict):
    """One call to make and what to expect from it."""

    name: StrictStr = Field(min_length=1)
    call: StrictStr = Field(min_length=1)  # The tool to call.
    arguments: dict[StrictStr, Any] = Field(default_factory=dict)
    expect: ExpectSpec = Field(default_factory=ExpectSpec)


class ServerSpec(_Strict):
    """Which server to test. Everything here can be given on the command line instead."""

    command: list[StrictStr] | None = Field(default=None, min_length=1)
    url: StrictStr | None = None
    timeout: float = Field(default=30.0, ge=0.1, allow_inf_nan=False)
    env: list[StrictStr] = Field(default_factory=list)
    bearer_env: StrictStr | None = None
    header_env: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "ServerSpec":
        if self.command and self.url is not None:
            raise ValueError("give either command or url, not both")
        if self.url is not None and self.env:
            raise ValueError("env only applies to a server started from a command")
        if self.command and (self.bearer_env is not None or self.header_env):
            raise ValueError("bearer_env and header_env only apply together with url")
        try:
            if self.url is not None:
                check_url(self.url)
            # Check the shape of the names without reading any value: every name is
            # given a placeholder.
            resolve_env(self.env, dict.fromkeys(self.env, "x"))
            names = ([self.bearer_env] if self.bearer_env is not None else []) + [
                spec.partition("=")[2] for spec in self.header_env
            ]
            build_headers(self.bearer_env, self.header_env, dict.fromkeys(names, "x"))
        except (UrlError, EnvError, CredentialsError) as error:
            raise ValueError(_for_file(str(error))) from error
        return self


class SuiteSpec(_Strict):
    """A whole test file."""

    version: Literal[1]
    server: ServerSpec | None = None
    allow_tools: list[StrictStr] = Field(min_length=1)  # The only tools a test may call.
    allow_writes: list[StrictStr] = Field(default_factory=list)  # Not supported yet.
    tests: list[CaseSpec] = Field(min_length=1)

    @property
    def callable_tools(self) -> tuple[str, ...]:
        """Every tool a test may call: the read tools and the write tools."""
        return tuple(dict.fromkeys([*self.allow_tools, *self.allow_writes]))


def load_suite(path: Path) -> SuiteSpec:
    """Read a test file. Raises SuiteFileError with every problem found."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise SuiteFileError(f"cannot read {path}: {error.strerror or error}") from error
    if len(data) > MAX_FILE_BYTES:
        raise SuiteFileError(f"{path} is larger than {MAX_FILE_BYTES // 1000} KB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SuiteFileError(f"{path} is not valid UTF-8 text") from error
    return parse_suite(text, str(path))


def parse_suite(text: str, source: str = "the test file") -> SuiteSpec:
    """Check the text of a test file and return it as a SuiteSpec. See `load_suite`."""
    root, data = _read_yaml(text, source)
    problems = _duplicate_key_problems(root)
    if problems:
        raise SuiteFileError(_report(source, problems))
    try:
        suite = SuiteSpec.model_validate(data)
    except ValidationError as error:
        raise SuiteFileError(_report(source, _problems(root, error))) from None
    problems = [(_line_of(root, loc), _path(loc), message) for loc, message in _cross_checks(suite)]
    if problems:
        raise SuiteFileError(_report(source, problems))
    return suite


def check_access(suite: SuiteSpec, tools: Sequence[ToolDef]) -> list[str]:
    """Compare the allowed tools with what the server lists. Returns the problems found.

    Every allowed tool must exist. A tool in `allow_tools` must say it only reads: a server
    that says nothing, or says it changes data, is not trusted. A tool that writes must
    be listed in `allow_writes` instead.
    """
    listed = {tool.name: tool for tool in tools}
    problems: list[str] = []
    for name in suite.allow_tools:
        tool = listed.get(name)
        if tool is None:
            problems.append(f"the server does not list the tool {name!r}")
        elif tool.declared_access == "write":
            problems.append(f"{name!r} says it changes data. {_how_to_allow_writes()}")
        elif tool.declared_access == "unknown":
            problems.append(
                f"{name!r} does not say whether it only reads. {_how_to_allow_writes()}"
            )
    for name in suite.allow_writes:
        if name not in listed:
            problems.append(f"the server does not list the tool {name!r}")
    return problems


def _how_to_allow_writes() -> str:
    if WRITES_SUPPORTED:
        return "To run it anyway, list it in allow_writes instead of allow_tools"
    return "Tools that may change data cannot be run yet"


def _for_file(message: str) -> str:
    """Reword a command-line message so that it fits a problem found in a file."""
    for option, key in (("--bearer-env", "bearer_env"), ("--header-env", "header_env")):
        message = message.replace(option, key)
    return message.replace("--env", "env").replace(
        "typed on the command line is kept in shell history",
        "written in a file is kept in version control",
    )


# --- Reading and reporting ----------------------------------------------------------------


def _read_yaml(text: str, source: str) -> tuple[yaml.Node, Any]:
    loader = yaml.SafeLoader(text)
    try:
        root = loader.get_single_node()
        if root is None:
            raise SuiteFileError(f"{source} is empty")
        return root, loader.construct_document(root)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(error, "problem", None) or "not valid YAML"
        raise SuiteFileError(f"{source} is not valid YAML: {where}{problem}") from None
    finally:
        loader.dispose()


def _duplicate_key_problems(node: yaml.Node) -> list[tuple[int | None, str, str]]:
    """YAML allows a key twice and keeps the last one; here that would hide a test."""
    problems: list[tuple[int | None, str, str]] = []
    if isinstance(node, yaml.MappingNode):
        seen: set[Any] = set()
        for key_node, value_node in node.value:
            key = getattr(key_node, "value", None)
            if isinstance(key_node, yaml.ScalarNode) and key in seen:
                problems.append((key_node.start_mark.line + 1, str(key), "this key is given twice"))
            seen.add(key)
            problems += _duplicate_key_problems(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            problems += _duplicate_key_problems(item)
    return problems


def _cross_checks(suite: SuiteSpec) -> list[tuple[tuple[Any, ...], str]]:
    """Mistakes that involve more than one part of the file."""
    found: list[tuple[tuple[Any, ...], str]] = []
    if suite.allow_writes and not WRITES_SUPPORTED:
        found.append(
            (
                ("allow_writes",),
                "tools that change data cannot be run yet: this version runs read-only tests",
            )
        )
    for index, name in enumerate(suite.allow_writes):
        if name in suite.allow_tools:
            found.append((("allow_writes", index), f"{name!r} is also listed in allow_tools"))
    names: set[str] = set()
    for index, case in enumerate(suite.tests):
        if case.name in names:
            found.append((("tests", index, "name"), f"the test name {case.name!r} is used twice"))
        names.add(case.name)
        if case.call not in suite.callable_tools:
            found.append(
                (
                    ("tests", index, "call"),
                    f"{case.call!r} is not in allow_tools, so this test cannot call it",
                )
            )
    return found


def _problems(root: yaml.Node, error: ValidationError) -> list[tuple[int | None, str, str]]:
    problems = []
    for item in error.errors(include_url=False, include_input=False):
        loc = tuple(item["loc"])
        problems.append((_line_of(root, loc), _path(loc), _message(item)))
    return problems


def _message(item: Any) -> str:
    kind, loc = item["type"], tuple(item["loc"])
    if kind == "missing":
        return "is required"
    if loc == ("version",):
        return "must be 1, the only version of this file format"
    if kind == "extra_forbidden":
        return "unknown field"
    if kind == "too_short":
        return "must not be empty"
    if kind in ("model_type", "dict_type") and not loc:
        return "the file must hold key: value pairs at the top level"
    if kind == "value_error":
        return str(item["msg"]).removeprefix("Value error, ")
    return str(item["msg"])


def _path(loc: Sequence[Any]) -> str:
    path = ""
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path or "(file)"


def _line_of(root: yaml.Node, loc: Sequence[Any]) -> int | None:
    """The line of the deepest part of `loc` that can be found in the file."""
    node: yaml.Node = root
    line = root.start_mark.line + 1
    for part in loc:
        if isinstance(node, yaml.MappingNode) and isinstance(part, str):
            match = [(k, v) for k, v in node.value if getattr(k, "value", None) == part]
            if not match:
                break
            key_node, node = match[-1]
            line = key_node.start_mark.line + 1
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                break
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            break
    return line


def _report(source: str, problems: Sequence[tuple[int | None, str, str]]) -> str:
    ordered = sorted(problems, key=lambda p: (p[0] is None, p[0] or 0))
    count = len(ordered)
    lines = [f"{source} has {count} problem{'s' if count != 1 else ''}:"]
    for line, path, message in ordered[:MAX_PROBLEMS_SHOWN]:
        where = f"line {line}: " if line is not None else ""
        lines.append(f"  {where}{path}: {message}")
    if count > MAX_PROBLEMS_SHOWN:
        lines.append(f"  ... and {count - MAX_PROBLEMS_SHOWN} more")
    return "\n".join(lines)
