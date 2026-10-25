# mcp-doctor

> A checkup for MCP servers: inspect, test, lint and benchmark.

**Status:** early scaffold. Nothing is implemented yet.

## Why

MCP server authors usually test by chatting with an AI client and hoping it works. That misses broken schemas, vague tool descriptions, crashes on bad input, and regressions after changes. `mcp-doctor` makes those checks repeatable and CI-friendly.

## Planned features

- `inspect`: connect to a server and list its tools, resources and prompts
- `run`: execute YAML test files against a server
- `lint`: audit a server automatically (schemas, descriptions, naming, error handling, timeouts, safety hints)
- `snapshot`: snapshot outputs and tool schemas, and classify drift
- `bench`: latency and throughput per tool
- Reports: terminal, JSON and JUnit XML, with non-zero exit codes for CI

## Install (development)

```bash
git clone https://github.com/his-adriness/mcp-doctor.git
cd mcp-doctor
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e ".[dev]"
```

## Usage

```bash
mcp-doctor inspect --command "python server.py"
mcp-doctor run tests/example.yaml
mcp-doctor lint --command "python server.py"
```

*(Planned. Not implemented yet.)*

## Project layout

```
mcp-doctor/
├─ src/mcpdoctor/
│  ├─ cli.py          # commands
│  ├─ client.py       # MCP SDK client + transports
│  ├─ runner.py       # test execution
│  ├─ assertions.py   # expectation types
│  ├─ lint_rules.py   # lint rules
│  └─ reporters/      # terminal, json, junit
├─ examples/
│  ├─ servers/        # a good server and a deliberately broken one
│  └─ tests/          # sample test files
├─ tests/             # tests for the tool itself
└─ .github/workflows/ci.yml
```

## Design decisions

*(To be written.)*

## Limitations and next steps

*(To be written.)*

## License

MIT. See [LICENSE](LICENSE).
