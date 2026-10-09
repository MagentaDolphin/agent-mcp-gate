# Architecture

The package has a standard-library MCP server and two local tool handlers.
The MCP package has no SDK, network, LLM, or Python runtime dependencies.
The sandbox layer requires the host bubblewrap binary. A separately callable
task loop runs sandboxed commands and accepts them through machine checks.

## Module map

| Module | Purpose and calls |
| --- | --- |
| `demo.py` | CLI fixture orchestration; calls `loop.run_task` twice, streams events/verdicts, retains reports and host event logs. |
| `loop.py` | Calls `sandbox.runner.run_in_sandbox`, supplements snapshots, calls `gate.evaluate`, and emits lifecycle events. |
| `sandbox/policy.py`, `sandbox/runner.py` | Validate policy; build and execute bubblewrap, limit output/time, hash workspace files. |
| `gate.py` | Evaluates G01–G07 against execution and filesystem evidence. |
| `events.py` | Appends timestamped JSON Lines to host-selected destinations. |
| `mcp/jsonrpc.py`, `mcp/server.py`, `mcp/tools.py` | Parse/dispatch stdio MCP, validate schemas, and call registered handlers directly. |
| `tools/scan_asset_usage.py` | Reads JSON fixtures and traverses asset references. |
| `tools/apply_scoped_patch.py` | Validates and atomically applies a patch to an existing workspace file. |

`demo.py` creates three asset JSON files and one level in a temporary read-only root.
It binds package source/runtime read-only, runs isolated Python with an empty environment
allowlist, and writes a uniquely named JSON report inside the workspace. A second worker
creates an in-scope artifact then attempts a write to read-only `/usr`; its failed exit must
be rejected by G01. Both workers run every time. Zero exit requires scan acceptance and
negative-run G01 refusal with G02 satisfied, so sandbox setup failure cannot pass the demo.
Events are retained outside the workspace as `*-events.json` JSON Lines files and streamed
to stdout; the standalone loop/MCP stdout invariants remain unchanged. Repeated demo calls
create fresh fixtures/reports without deleting existing workspace files.

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

## Sandbox boundary

- `sandbox/policy.py:SandboxPolicy` validates JSON/dict policies and records
  filesystem binds, environment names, timeout, output budget and tmpfs capacity.
- `sandbox/runner.py:build_bwrap_argv` constructs namespace and mount arguments;
  `run_in_sandbox` resolves bwrap, captures bounded output, kills timed-out process
  groups, and returns before/after SHA-256 maps of regular workspace files.
- Execution always uses bwrap. Missing or unstartable binaries, unreadable policies
  and unsupported network requests prevent launch. Isolation setup failures return
  a nonzero result; no branch runs the task without isolation.
- Only the workspace is bound writable; `/tmp` is a private size-limited tmpfs.
  Configured roots are read-only, other host paths are absent, host environment is
  cleared before explicit allowlisted values are restored, and networking is
  always isolated. Workspace hard links and overlapping binds are refused.
- Snapshot traversal does not follow symlinks. Captured stdout/stderr share one
  byte budget; excess output is drained. Timeouts report `rc=-9`, kill descendants
  in the PID namespace, and reap bwrap. Workspace writes are not rolled back.

See [sandbox.md](sandbox.md) for the policy schema, command layout, failure
semantics and named negative tests. This boundary is independently callable;
MCP tools do not yet invoke it; the task loop and acceptance gate consume its
`RunResult` without changing sandbox execution.

## Acceptance and events

- `gate.py:GateRule` defines G01–G07; `evaluate` runs every rule and returns a
  `GateVerdict` containing structured checks and refusal reasons. Check exceptions
  and malformed check returns fail closed with `internal_error`.
- Acceptance requires a successful, untimed-out run, a new nonempty regular file,
  changes inside the workspace, no new escaping symlinks, complete output,
  disabled network/no stderr indicators, and sanitary changed paths. A caller's
  `extra_paths` participates in boundary and sanitation checks, but cannot replace
  snapshot evidence for a new artifact.
- `loop.py:run_task` invokes the unchanged sandbox runner. Descriptor-based tree
  metadata supplements its file hashes with modes, directories and symlinks, so
  those changes are visible and preexisting links are distinguished from new ones.
  Optional artifact glob matching constrains G02. Setup/snapshot errors refuse
  acceptance; there is no unsandboxed fallback or cleanup of rejected workspaces.
- `events.py:EventEmitter` and `emit` append UTC-timestamped JSON Lines to the
  caller's file and/or stderr. Stdout remains reserved for MCP responses. The
  task loop records lifecycle, artifacts, all checks, verdict and final decision.
  Serialization/write errors propagate. Logs are initialized before snapshots
  and do not supply artifact evidence; host-side logs should be outside task scope.
- These APIs remain independently callable; the MCP handlers do not yet invoke
  the task loop. The gate judges observable execution evidence, not task meaning.

See [acceptance.md](acceptance.md) for rule details, named tests and limitations.

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

Protocol references: [MCP lifecycle]( 53 )
and [MCP tools]( 54 ).

## Navigation and verification

[CODEMAP.md](CODEMAP.md) indexes Python symbols with file and line addresses.
Regenerate it after structural changes with `python3 tools/gen_codemap.py`.
The generator scans only the explicit source, test, and tooling directories.

Validation: `.venv/bin/ruff check .`, `.venv/bin/pytest -q -rs`, and
`.venv/bin/python -m agent_mcp_gate.demo` (both sandboxed workers are mandatory).
The [threat model](threat-model.md) maps boundary claims to named tests.
