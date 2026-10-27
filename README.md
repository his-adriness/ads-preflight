# ads-preflight

[![CI](https://github.com/his-adriness/ads-preflight/actions/workflows/ci.yml/badge.svg)](https://github.com/his-adriness/ads-preflight/actions/workflows/ci.yml)

> Preflight checks for ad-platform MCP servers: lint their tools, test their behaviour, and gate what an AI agent is allowed to do.

**Status:** early development. `lint --tools-file` works on a saved `tools/list` response. `inspect`, `run` and linting a live server print "not implemented yet", because the MCP client is not built. This is an independent project, not affiliated with Amazon or any ad platform.

## Why

An AI agent connected to an ad account can change budgets, bids and campaigns. Chatting with it and hoping it behaves is not a safety check. `ads-preflight` is meant to make three checks repeatable:

1. **Lint:** are the server's tools defined safely, with honest read/write hints, clear descriptions and bounded inputs?
2. **Gate:** may a proposed call run, does it need human approval, or is it blocked? The decision is made in code, not in a prompt.
3. **Verify:** can the data the agent relied on be trusted (totals add up, data is fresh, currencies match)?

The core is generic MCP tooling. Ad-specific rules will live in a separate layer. A mock ads server with synthetic data is planned, so anyone can run the project without access to a real ad account.

## What works today

### `CallRecord`

An immutable record of one tool call: the tool name, the arguments, the result, an error flag and the latency in milliseconds. Every feature reads from this one shape. It serializes to JSON (one record per line in JSONL), and `.text` returns the text content of a result.

### `ToolDef`

One tool as a server lists it in its `tools/list` response: `name`, `title`, `description`, `inputSchema`, `outputSchema` and `annotations`. The linter and the inspector read from this shape.

```python
from ads_preflight.models import ToolDef

tool = ToolDef.model_validate({
    "name": "list_notes",
    "description": "List the user's notes.",
    "annotations": {"readOnlyHint": True},
})
tool.declared_access   # "read"
```

`declared_access` reports what the server *claims* about the tool: `"read"` if `readOnlyHint` is true, `"write"` if it is false, and `"unknown"` if the server said nothing or sent something that is not a boolean. Silence is never treated as safe, and a claim is not proof.

### Tools file

A saved `tools/list` response lets tools be checked without a live server and without calling anything. `parse_tools(data)` reads parsed JSON, and `load_tools(path)` reads a file. Both accept a bare list of tools, `{"tools": [...]}`, or a whole JSON-RPC response with the tools under `result`.

```python
from ads_preflight.tools_file import parse_tools

tools = parse_tools({"tools": [{"name": "list_notes"}, {"name": "create_note"}]})
[tool.name for tool in tools]   # ["list_notes", "create_note"]
```

A problem raises `ToolsFileError`, with a message that says where it is:

```
tools.json: tool #2 ('create_note'): description: Input should be a valid string
```

### `normalize`

Two runs of the same tool call rarely return identical output: IDs and timestamps change, and unordered collections come back in a different order. The `normalize` module removes those differences so results can be compared fairly.

| Function | What it does |
|---|---|
| `scrub(value, scrubbers)` | Replaces volatile strings (UUIDs, timestamps by default) with placeholders, at any depth |
| `remove_paths(value, paths)` | Removes fields by path, such as `$.request_id` or `$.items[*].updated_at` |
| `sort_lists(value, paths)` | Puts the lists at the given paths in a stable order |
| `normalize(record, ...)` | Applies all three to a `CallRecord`'s result, in that order, and returns a new record |

```python
from ads_preflight.models import CallRecord
from ads_preflight.normalize import normalize

a = CallRecord(
    tool="list_notes", arguments={}, latency_ms=12.0,
    result={
        "request_id": "x7Qp2",
        "created": "2026-10-25T14:30:00Z",
        "notes": [
            {"id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "title": "alpha"},
            {"id": "0e3c5d7a-1111-4222-8333-944455556666", "title": "beta"},
        ],
    },
)

# The same call on another run: different IDs, timestamps and list order.
b = CallRecord(
    tool="list_notes", arguments={}, latency_ms=15.0,
    result={
        "request_id": "k9Zt4",
        "created": "2026-10-25T14:31:07Z",
        "notes": [
            {"id": "a1b2c3d4-aaaa-4bbb-8ccc-ddddeeeeffff", "title": "beta"},
            {"id": "9c8b7a65-4321-4fed-9cba-0987654321ab", "title": "alpha"},
        ],
    },
)

options = {"ignore": ["$.request_id"], "sort": ["$.notes"]}
assert normalize(a, **options).result == normalize(b, **options).result
```

After normalizing, the result looks like this:

```python
{"created": "<TIMESTAMP>", "notes": [{"id": "<UUID>", "title": "alpha"},
                                      {"id": "<UUID>", "title": "beta"}]}
```

Path syntax is a small subset of JSONPath: `$`, `.key`, `[*]` (every list item) and `[0]` (one item). A path must end with a key.

### `lint`

`lint` checks a server's tool definitions against eight rules, without calling any tool. Errors fail the run (exit code 1), warnings only fail it with `--strict`, and a file that cannot be read exits with 2.

| Rule | Severity | Catches |
|---|---|---|
| `core.duplicate-name` | error | Two tools with the same name |
| `core.wrong-schema-key` | error | `input_schema` or `parameters` instead of `inputSchema` |
| `core.hint-not-boolean` | error | A hint such as `"readOnlyHint": "true"` |
| `core.empty-tool-list` | warning | A server that lists no tools |
| `core.missing-description` | warning | A tool the model has nothing to choose by |
| `core.missing-input-schema` | warning | A tool whose arguments are not described |
| `core.no-access-hint` | warning | A tool that does not say whether it is read-only |
| `core.hint-conflict` | warning | A tool marked both read-only and destructive |

```bash
ads-preflight lint --tools-file tools.json
ads-preflight lint --tools-file tools.json --format json --strict
```

```
tools.json: 3 tools, 1 error, 1 warning

error    core.wrong-schema-key     create_note  has 'input_schema', which MCP clients do not read
         fix: rename it to 'inputSchema'
warning  core.missing-description  delete_note  has no description for the model to read
         fix: describe what the tool does and when to use it
```

Text from the server is shown with control characters escaped, because a tools file may come from a hostile server.

### Command line

The `ads-preflight` command is installed with the package. `lint --tools-file` works. `inspect`, `run` and `lint` with a server command check their arguments, print "not implemented yet" and exit with code 1. The server command goes after a double dash, and everything after it is passed through untouched:

```bash
ads-preflight --version
ads-preflight lint --tools-file tools.json
ads-preflight inspect -- python server.py
ads-preflight run tests.yaml -- python server.py
```

## Planned

- A mock ads MCP server with synthetic data, and an MCP client with a basic `inspect`
- `lint` of a live server (works today on a saved tools file)
- `check`: decide whether one proposed tool call is allowed, needs approval, or is blocked, using a policy file. It reads the call as JSON, so it can run from a Claude Code hook or in CI
- `run`: execute YAML test files against a server (command exists, logic does not)
- Data checks on tool results: totals, freshness, currency
- A JUnit XML report for CI
- A demo agent that uses Claude through the gate, and a small evaluation of what the gate catches
- Later: an MCP proxy that applies the policy to every call, and an Amazon Ads adapter once its tool names are verified

## Development

You need Python 3.11 or newer (the MCP SDK it builds on requires it).

```bash
git clone https://github.com/his-adriness/ads-preflight.git
cd ads-preflight
python -m venv .venv
```

Activate the environment:

```bash
# Windows (Command Prompt)
.venv\Scripts\activate.bat
# Windows (Git Bash)
source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate
```

Then install and check:

```bash
pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

Run both checks before pushing. CI runs the same two commands.

## Project layout

```
ads-preflight/
├─ src/ads_preflight/
│  ├─ models.py       # CallRecord, ToolDef, ToolAnnotations
│  ├─ normalize.py    # scrub, remove_paths, sort_lists, normalize
│  ├─ tools_file.py   # load_tools, parse_tools: read a saved tools/list response
│  ├─ findings.py     # Finding: one problem a check reports
│  ├─ lint_rules.py   # the eight core lint rules
│  ├─ linter.py       # lint(tools), failed(findings)
│  ├─ cli.py          # lint works on a tools file; inspect and run are stubs
│  ├─ client.py       # MCP SDK client and transports (not built yet)
│  ├─ runner.py       # test execution (not built yet)
│  ├─ assertions.py   # expectation types (not built yet)
│  └─ reporters/      # terminal and json reports (junit not built yet)
├─ examples/
│  ├─ servers/        # a good server and a deliberately broken one (not built yet)
│  └─ tests/          # sample test files (not built yet)
├─ tests/             # tests for the tool itself
└─ .github/workflows/ci.yml
```

## Design decisions

- **Records are immutable.** A `CallRecord` cannot be changed after it is created. Cleaning a record builds a new one, validated with `model_validate` (`model_copy(update=...)` skips validation).
- **Normalization is opt-in by path.** Some lists are order-meaningful, such as MCP content blocks. Only the lists you name are sorted, and only the fields you name are removed.
- **Sorting runs last.** Lists are ordered by their scrubbed content. Sorting first would order them by values that change on every run.
- **Typos fail loudly.** A malformed path raises an error even when it would match nothing. A well-formed path that matches nothing is skipped, because a volatile field may be absent from some results.
- **`ToolDef` is lenient on purpose.** Only `name` is required, so a server with a missing description or schema can still be loaded and reported on. Unknown fields are kept, because vendors add their own and rules for them need to see them.
- **Hints are stored as sent, and unsent is not false.** A hint that was not sent stays `None`. A hint that is not a real boolean, such as the string `"true"`, still loads so a lint rule can report it, and it counts as `"unknown"` access. A safety check must not guess what a server meant.
- **Field names match the wire format** (`inputSchema`, `readOnlyHint`), like the official MCP SDK, so what a server sent is what you get back. This is why `ToolDef` uses camelCase while `CallRecord`, which we create ourselves, uses snake_case. A key spelled `input_schema` is kept as an unknown field so the linter can report it.
- **An empty tools list and duplicate names are not errors.** They are problems in the server, so a lint rule reports them. The loader only rejects files it cannot read.
- **Errors say where, and name common mistakes.** Each one names the file, the tool's position and name, and the field. Loading stops at the first invalid tool. A saved server error response, JSON with comments or trailing commas, and tool definitions written for OpenAI or Gemini each get their own message. Files saved as UTF-8 with a byte order mark load, and a UTF-16 file (a common PowerShell result) gets a message that says to save it as UTF-8.
- **No conversion between tool formats.** A file in another API's format is the wrong file, and an MCP server that sends `input_schema` has a bug. Silently accepting either would hide it.
- **Only `result` is normalized.** Latency is real measured data, so compare `.result`, not whole records.
- **One problem, one finding.** Rules that overlap stay quiet: a tool with `input_schema` gets the wrong-key error, not also a missing-schema warning.
- **The report is plain text, not a Rich table.** It is easy to read, to copy and to test, and color is added only when the output is a terminal.

## Known limitations

- Frozen records protect their fields, not the dicts and lists inside them.
- `ignore` and `sort` paths cannot look inside JSON that a server wrote as text.
- Sorting is stable and deterministic, not natural: the text `"10"` sorts before `"2"`.

## License

MIT. See [LICENSE](LICENSE).
