# Architecture

The package has a standard-library MCP server and two local tool handlers.
There are no SDK, network, LLM, or runtime dependency requirements. Sandbox,
agent-loop, and gate layers are outside this implementation.

## Execution and boundaries

`python -m agent_mcp_gate.mcp.server`, `python -m agent_mcp_gate.mcp`, and
`python -m agent_mcp_gate` start the same line-oriented stdio loop.

1. `mcp/jsonrpc.py` parses JSON-RPC 2.0 envelopes and constructs results/errors.
2. `mcp/server.py:Server` dispatches methods and isolates failures per input line.
3. `mcp/tools.py:ToolSpec` validates arguments before calling a handler.
4. `tools/scan_asset_usage.py` reads local JSON fixtures and traverses dependencies.
5. `tools/apply_scoped_patch.py` checks scope and applies an atomic text patch.

Handlers are independently callable; they do not require a sandbox or MCP client.
The MCP boundary performs argument-schema validation. Direct Python callers must
supply the documented argument types.

## Invariants

- Stdout contains only one JSON response line per request; notifications have no
  responses. Stderr is not used for ordinary failures.
- All five standard error codes are represented by `RpcError`. Tool exceptions
  become `-32603`; subsequent input lines are processed.
- Input schemas reject extra tool arguments and enforce types without coercion.
  Boolean values do not satisfy the integer type. Validation errors include a
  field path and reason. Defaults are implemented by handler signatures.
- Tool names and read-only hints are advertised from the registry, alongside the
  complete input schemas. Read-only hints are descriptive metadata, not a sandbox.
- Scan output has one row per ID, sorted by ID. Breadth-first traversal visits
  each eligible ID once; paths are shortest and ties are deterministic. Missing
  reachable IDs and duplicate definitions are explicit verdicts.
- Patch targets are existing UTF-8 regular files under the environment-specified
  workspace. Absolute paths and `..` are refused. Escaping symlinks are refused;
  directory-descriptor operations with `O_NOFOLLOW` anchor the actual write.
- Patching validates all hunks and coordinates before mutation, performs an
  atomic replacement, and reports zero applied hunks on any refusal. File modes
  are preserved; temporary files are removed. Concurrent edits are not merged.

## Protocol scope

The advertised MCP revision is `2025-06-18`. Initialize returns that revision
when the client omits it or requests another revision. Tool results have JSON
`structuredContent` and an equivalent JSON text content block. Patch refusals
set `isError: true`; invalid arguments are JSON-RPC errors.

One input line carries one JSON object; JSON-RPC batch arrays and pagination are
not implemented. Tool discovery and calls are permitted before initialization,
including the standalone `tools/list` smoke command. `shutdown` and `exit` are
project lifecycle extensions: shutdown replies with null and refuses further
operations; exit or stdin EOF ends the loop. See
[tool-contracts.md](tool-contracts.md) for supported params and detailed semantics.

Protocol references: [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)
and [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

## Navigation and verification

[CODEMAP.md](CODEMAP.md) indexes Python symbols with file and line addresses.
Regenerate it after structural changes with `python3 tools/gen_codemap.py`.
The generator scans only the explicit source, test, and tooling directories.

Validation: `.venv/bin/ruff check .`, `.venv/bin/pytest -q`, and a live
`tools/list` request to `.venv/bin/python -m agent_mcp_gate.mcp.server`.
