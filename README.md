# agent-mcp-gate

## Русский

Портфолио-проект: локальные MCP-инструменты, песочница bubblewrap и машинный гейт приёмки.
Демо сканирует JSON-ассеты, сохраняет отчёт и проверяет отказ при записи вне workspace.
Нужны Linux, Python 3.11+ и bubblewrap; команды установки приведены в разделе Run it.
После установки: `python -m agent_mcp_gate.demo`.

## Status

early development (v0.1). This is a portfolio prototype, not a production service.
The package metadata version is currently `0.0.1`; v0.1 describes the development stage.
The demo connects an existing local tool to a sandbox and a factual acceptance gate.
Standalone MCP calls execute handlers directly; callers must arrange isolation themselves.

## What it is

Two standard-library tools expose deterministic asset reachability and scoped text patches
through a line-oriented stdio MCP server. A separate task loop runs commands in bubblewrap,
records workspace changes, and evaluates seven acceptance rules. The demo uses that loop to
run `scan_asset_usage`, write its JSON report, and reject an outside-write attempt.

Repository: [MagentaDolphin/agent-mcp-gate](https://github.com/MagentaDolphin/agent-mcp-gate).

## Architecture

```text
task → sandbox → tool → workspace tree change → gate (G01–G07) → verdict
                       report                   JSON Lines events
```

`demo.py` calls `loop.run_task`; the loop calls the sandbox runner and then `gate.evaluate`.
The runner supplies exit status, bounded output, and before/after file hashes. The loop adds
entry metadata and emits events. The scan handler reads the fixture; its sandboxed wrapper
writes the report. Logs live outside the task workspace and cannot count as artifacts.

The stdio MCP server is a separate entry point: JSON-RPC → schema validation → handler.
See the [module map](docs/ARCHITECTURE.md) and [symbol index](docs/CODEMAP.md).

## Tools

| Name | Mode | Required rights | Forbidden behavior |
| --- | --- | --- | --- |
| `scan_asset_usage` | Read-only | Read/traverse the asset root and level; reads have no intrinsic workspace restriction | Writes, network lookups, silently discarding missing reachable IDs |
| `apply_scoped_patch` | Writing | Read an existing UTF-8 file and create/replace in its parent under `AGENT_MCP_GATE_WORKSPACE` | Outside/absolute/parent paths, escaping symlinks, new targets, partial or binary patches |

Full input/output schemas and errors are in [tool-contracts.md](docs/tool-contracts.md).
Read-only hints describe tools; permissions come from the sandbox or the caller.

## Sandbox

Linux and the host `bwrap` executable are required. The workspace is the only writable host
bind. Runtime and explicitly supplied fixture/source roots are read-only; other host files
are absent. `/tmp` is a private, size-limited tmpfs, so writes there do not modify host files.
Network namespaces isolate networking. The host environment is cleared, then only configured
allowlisted variables are restored; the demo uses an empty allowlist and isolated Python.
Workspace hard links and overlapping workspace/read-only binds are refused.

Missing bubblewrap, invalid policies, unsupported network requests, or isolation setup errors
fail closed: there is no host execution fallback. Output has a shared byte limit; timeouts
kill the process group. Rejected workspace changes remain for inspection and are not rolled back.
See [sandbox.md](docs/sandbox.md) and the [threat model](docs/threat-model.md).

## Acceptance rules

— G01: Exit successfully without a timeout.
— G02: Create a new, nonempty regular file; the task loop can require an artifact glob.
— G03: Keep all known changes inside the declared workspace.
— G04: Keep new symlink targets inside the workspace.
— G05: Capture complete output; truncation is a refusal.
— G06: Disable networking and reject recognized network indicators in stderr.
— G07: Avoid binary/build debris and service directories in changed paths.

Every rule runs; exceptions or malformed check results reject the task. An agent’s success
message is not artifact evidence. A nonempty report’s semantic correctness is outside the gate;
end-to-end demo tests check the actual scan result. Details: [acceptance.md](docs/acceptance.md).

## Run it

From the repository root, on a Linux host that permits bubblewrap namespaces:

```bash
sudo apt-get update && sudo apt-get install -y bubblewrap
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m agent_mcp_gate.demo
python -m agent_mcp_gate.demo --workspace /tmp/agent-mcp-gate-demo
ruff check .
pytest -q -rs
```

The default workspace is a retained temporary directory. Every launch builds a fresh
read-only fixture containing three asset JSON files and one level file, and preserves
reports with unique names so the same workspace can be reused. Stdout is JSON Lines: the
workspace/log locations, gate events, and one verdict summary per run. `*-events.json` files
contain the same event lines and are retained in the printed `events_dir`.

Exit status is zero only when the scan is accepted and the outside-write worker actually
creates its in-scope artifact and is rejected. Both workers run on every invocation, including CI.
The negative worker attempts a write under read-only `/usr`; G01 rejects its failed exit.
CI installs bubblewrap, runs lint and tests with skip reasons, requires the demo, and uploads
its event logs. Live demo tests fail if isolation is unavailable.

Abbreviated output from an actual demo run (selected complete lines; temporary paths vary):

<!-- demo-output:start -->
```json
{"workspace": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy", "events_dir": "/mnt/data/tmp/agent-mcp-gate-events-9cgwvcyl"}
{"ts": "2026-10-09T20:04:44.113676+00:00", "event": "run_started", "policy": {"workspace": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy", "read_only_roots": ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives", "/home/pi/kodlo_jobsearch/portfolio/agent-mcp-gate/src", "/mnt/data/tmp/agent-mcp-gate-fixture-s8u8s6_u"], "env_allowlist": [], "network": false, "timeout_s": 60, "max_output_bytes": 1048576, "tmpfs_size": 67108864}}
{"ts": "2026-10-09T20:04:44.149713+00:00", "event": "run_finished", "rc": 0, "timed_out": false, "truncated": false, "duration_s": 0.03461114800302312}
{"ts": "2026-10-09T20:04:44.149875+00:00", "event": "gate_started"}
{"ts": "2026-10-09T20:04:44.151462+00:00", "event": "artifact_written", "path": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy/asset-usage-5607c285fbed4de8b2db703377c458ba.json"}
{"ts": "2026-10-09T20:04:44.152010+00:00", "event": "accepted", "reasons": []}
{"run": "scan_asset_usage", "verdict": "accepted", "accepted": true, "reasons": [], "report": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy/asset-usage-5607c285fbed4de8b2db703377c458ba.json"}
{"ts": "2026-10-09T20:04:44.152126+00:00", "event": "run_started", "policy": {"workspace": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy", "read_only_roots": ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives", "/home/pi/kodlo_jobsearch/portfolio/agent-mcp-gate/src", "/mnt/data/tmp/agent-mcp-gate-fixture-s8u8s6_u"], "env_allowlist": [], "network": false, "timeout_s": 60, "max_output_bytes": 1048576, "tmpfs_size": 67108864}}
{"ts": "2026-10-09T20:04:44.196030+00:00", "event": "run_finished", "rc": 1, "timed_out": false, "truncated": false, "duration_s": 0.041999202978331596}
{"ts": "2026-10-09T20:04:44.196262+00:00", "event": "gate_started"}
{"ts": "2026-10-09T20:04:44.197353+00:00", "event": "artifact_written", "path": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy/outside-attempt-5607c285fbed4de8b2db703377c458ba.json"}
{"ts": "2026-10-09T20:04:44.197489+00:00", "event": "gate_check", "code": "G01", "ok": false, "detail": "execution_failed:rc=1,timed_out=False"}
{"ts": "2026-10-09T20:04:44.198051+00:00", "event": "rejected", "reasons": [{"code": "G01", "detail": "execution_failed:rc=1,timed_out=False"}]}
{"run": "outside_write", "verdict": "rejected", "accepted": false, "reasons": [{"code": "G01", "detail": "execution_failed:rc=1,timed_out=False"}], "report": "/mnt/data/tmp/agent-mcp-gate-workspace-ij0uo5hy/outside-attempt-5607c285fbed4de8b2db703377c458ba.json"}
```
<!-- demo-output:end -->

To run the independent MCP server over stdio:

```bash
python -m agent_mcp_gate.mcp.server
```

## What this is not

There are no network integrations, LLM calls, cloud components, or performance promises.
This is not a general-purpose agent, a production security service, or a proof that a task
has been solved correctly. The gate checks execution evidence and filesystem boundaries.