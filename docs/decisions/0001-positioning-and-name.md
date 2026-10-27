# 0001: Positioning, name and repository layout

Date: 2026-10-27. Status: accepted.

## Context

The project started as a generic "pytest for MCP servers". Other tools already cover that
space (`mcp-doctor`, `mcp-lint` and `mcp-probe` are taken). The goal is a portfolio project
that shows how to build safe AI-agent systems, and the author will have access to the Amazon
Ads MCP server (open beta, partners only), which other people cannot use.

## Decision

1. **Focus on ad-platform MCP servers.** The tool checks tool definitions (lint), decides
   whether a proposed call may run (gate), and checks the data an agent relied on (verify).
2. **Keep the core generic.** Policy and checks refer to tool access and argument patterns.
   Platform-specific rules go in a separate `ads` layer, and Amazon names in a thin adapter.
3. **Ship a mock ads server with synthetic data**, so reviewers can run everything without
   an ad account.
4. **Enforce in code, not in prompts.** The gate is a pure function,
   `evaluate(call, tools, policy)`, with no network access. Front ends (CLI, Claude Code hook,
   later an MCP proxy) call it.
5. **One repository.** Shared models make a split costly now. Instead, `ads` code may import
   `core` code and never the reverse, so the generic part can be split out later.
6. **Name: `ads-preflight`** (package `ads_preflight`). It is not affiliated with any ad platform
   and avoids platform trademarks. Availability on GitHub, PyPI and npm must be checked before
   publishing a package.

## Consequences

- The previous scope (`snapshot`, `bench`) is dropped from v0.1.
- The time-box and kill criterion are reset around the mock server: `inspect` must work over
  stdio against the mock server and one public MCP server by about 13 November 2026.
- Nothing derived from live account data is published without checking the partner agreement.
  Credentials and real account data are never committed.
