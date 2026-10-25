# mcp-doctor

> A checkup for MCP servers: inspect, test and lint.

**Status:** early development. The data model, the normalization toolkit and the command-line skeleton are built and tested. The commands exist but only print "not implemented yet", because the MCP client is not built, so there is nothing useful to run yet.

## Why

MCP server authors usually test by chatting with an AI client and hoping it works. That misses broken schemas, vague tool descriptions, crashes on bad input, and regressions after changes. `mcp-doctor` aims to make those checks repeatable and CI-friendly.

## What works today

### `CallRecord`

An immutable record of one tool call: the tool name, the arguments, the result, an error flag and the latency in milliseconds. Every feature reads from this one shape. It serializes to JSON (one record per line in JSONL), and `.text` returns the text content of a result.

### `normalize`

Two runs of the same tool call rarely return identical output: IDs and timestamps change, and unordered collections come back in a different order. The `normalize` module removes those differences so results can be compared fairly.

| Function | What it does |
|---|---|
| `scrub(value, scrubbers)` | Replaces volatile strings (UUIDs, timestamps by default) with placeholders, at any depth |
| `remove_paths(value, paths)` | Removes fields by path, such as `$.request_id` or `$.items[*].updated_at` |
| `sort_lists(value, paths)` | Puts the lists at the given paths in a stable order |
| `normalize(record, ...)` | Applies all three to a `CallRecord`'s result, in that order, and returns a new record |

```python
from mcpdoctor.models import CallRecord
from mcpdoctor.normalize import normalize

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

### Command line

The `mcp-doctor` command is installed with the package. It has three commands, which are stubs for now: they check their arguments, print "not implemented yet" and exit with code 1. The server command goes after a double dash, and everything after it is passed through untouched:

```bash
mcp-doctor --version
mcp-doctor inspect -- python server.py
mcp-doctor lint -- python server.py
mcp-doctor run tests.yaml -- python server.py
```

## Planned

- `inspect`: connect to a server and list its tools, resources and prompts (command exists, logic does not)
- `run`: execute YAML test files against a server (command exists, logic does not)
- `lint`: audit a server automatically: schemas, descriptions, naming, error handling, timeouts, safety hints (command exists, logic does not)
- `snapshot`: save outputs and tool schemas, and classify drift
- `bench`: latency and throughput per tool
- Reports: terminal, JSON and JUnit XML, with non-zero exit codes for CI
- A reusable GitHub Action

## Development

You need Python 3.10 or newer.

```bash
git clone https://github.com/his-adriness/mcp-doctor.git
cd mcp-doctor
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
mcp-doctor/
├─ src/mcpdoctor/
│  ├─ models.py       # CallRecord
│  ├─ normalize.py    # scrub, remove_paths, sort_lists, normalize
│  ├─ cli.py          # inspect, run and lint commands (stubs for now)
│  ├─ client.py       # MCP SDK client and transports (not built yet)
│  ├─ runner.py       # test execution (not built yet)
│  ├─ assertions.py   # expectation types (not built yet)
│  ├─ lint_rules.py   # lint rules (not built yet)
│  └─ reporters/      # terminal, json, junit (not built yet)
├─ examples/
│  ├─ servers/        # a good server and a deliberately broken one
│  └─ tests/          # sample test files
├─ tests/             # tests for the tool itself
└─ .github/workflows/ci.yml
```

## Design decisions

- **Records are immutable.** A `CallRecord` cannot be changed after it is created. Cleaning a record builds a new one, validated with `model_validate` (`model_copy(update=...)` skips validation).
- **Normalization is opt-in by path.** Some lists are order-meaningful, such as MCP content blocks. Only the lists you name are sorted, and only the fields you name are removed.
- **Sorting runs last.** Lists are ordered by their scrubbed content. Sorting first would order them by values that change on every run.
- **Typos fail loudly.** A malformed path raises an error even when it would match nothing. A well-formed path that matches nothing is skipped, because a volatile field may be absent from some results.
- **Only `result` is normalized.** Latency is real measured data, so compare `.result`, not whole records.

## Known limitations

- Frozen records protect their fields, not the dicts and lists inside them.
- `ignore` and `sort` paths cannot look inside JSON that a server wrote as text.
- Sorting is stable and deterministic, not natural: the text `"10"` sorts before `"2"`.

## License

MIT. See [LICENSE](LICENSE).