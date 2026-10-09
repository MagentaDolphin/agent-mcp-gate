# Sandbox execution

`agent_mcp_gate.sandbox` is a separate execution boundary. It uses the Python
standard library and the host `bwrap` binary. MCP handlers remain independently
callable; connecting sandbox execution to the gate is the MC-004 integration.

## Loading a policy

Load a file with `SandboxPolicy.from_json(path)` or a mapping with
`SandboxPolicy.from_dict(data)`. Direct dataclass construction also validates
fields. Paths are host paths. The workspace must be an absolute existing
directory; its resolved path becomes the writable bind and working directory.
Unknown fields, invalid types, missing files, malformed JSON and invalid binds
raise `SandboxPolicyError` before a process starts.

```python
from agent_mcp_gate.sandbox import SandboxPolicy, run_in_sandbox

policy = SandboxPolicy.from_json("/absolute/path/policy.json")
result = run_in_sandbox(policy, ["/usr/bin/python3", "-c", "print('inside')"])
```

Only `workspace` is required. For example:

```json
{
  "workspace": "/absolute/path/task",
  "read_only_roots": ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives"],
  "env_allowlist": ["PATH", "LANG", "LC_ALL", "HOME", "PYTHONPATH"],
  "network": false,
  "timeout_s": 60,
  "max_output_bytes": 1048576,
  "tmpfs_size": 67108864
}
```

The defaults match this example. Missing standard roots, such as `/lib64` on
some ARM distributions, are omitted from the command. Missing custom roots are
errors. Read-only roots may be files or directories, must be absolute, and
cannot overlap the workspace or replace the private `/tmp` mount. The root directory and
`/tmp` itself cannot be the workspace. Hard-linked regular workspace files are
refused because their writable inode might also belong to a host path outside
the workspace. Symlinks are not followed when hashing workspace files.

Timeouts must be finite positive numbers, output limits nonnegative integers,
and tmpfs sizes positive integers in bytes. Environment names must be distinct
valid variable names. Allowlisted values are taken from the host environment;
absent variables remain absent. Environment values are excluded from the policy
snapshot. Command arguments are passed as a list without a shell.

## Bind and namespace rules

`build_bwrap_argv(policy, argv)` builds this command without launching a process:

```text
bwrap --unshare-all --die-with-parent --new-session --unshare-net
  --size TMPFS_BYTES --tmpfs /tmp
  --ro-bind ROOT ROOT ...
  --bind WORKSPACE WORKSPACE --chdir WORKSPACE
  --clearenv --setenv ALLOWED_NAME HOST_VALUE ...
  -- COMMAND ARGUMENT ...
```

The private tmpfs is mounted before the binds so configured roots and workspaces below `/tmp`
remain visible. Writes elsewhere in private `/tmp` never modify host `/tmp`.
Read-only binds expose only the configured roots. A host file outside those
roots and the workspace is inaccessible, including through a workspace symlink.
No host root, home directory, `/proc`, device tree or resolver configuration is
implicitly mounted. Interpreters and their libraries must belong to read-only
roots; a virtual environment outside the binds is inaccessible.

Network execution is unsupported. A policy can store `network: true` and its
snapshot records that request, but command construction and execution reject it
with `SandboxPolicyError`. Neither case produces `--share-net`. The explicit
network namespace has no external route. No proxy or socket from the host is
forwarded into it.

## Results and failures

`run_in_sandbox` resolves `bwrap` through `shutil.which`; a missing or unstartable
binary raises `SandboxUnavailable`. A removed workspace is rejected again before
launch. There is no execution path that substitutes an unsandboxed subprocess.
If bwrap cannot establish isolation or execute the command, its nonzero return
code and stderr are returned. The command never receives a fallback launch.
A proxy failure inside a task is likewise an ordinary task failure; this layer
provides no retry or alternate execution path outside bwrap.

`RunResult` contains `rc`, decoded UTF-8 `stdout` and `stderr`, `duration_s`,
`truncated`, `timed_out`, `policy_snapshot`, `workspace_before` and
`workspace_after`. Snapshots map relative regular-file paths to SHA-256 digests;
symlinks and directories are excluded. Added, removed and modified files can be
compared by the gate. A snapshot I/O error raises `SandboxPolicyError` rather than
returning an incomplete map. Snapshots are observations, not a rollback mechanism.

Stdout and stderr share one raw-byte budget. Both pipes keep draining after the
budget is exhausted, so output floods cannot allocate unbounded capture memory
or block on a full pipe. Invalid UTF-8 is replaced during decoding. A timeout
kills and reaps the bwrap process group; its PID namespace terminates task
descendants. The result reports `rc=-9` and `timed_out=True`, retaining bounded
output already received. Workspaces can retain writes performed before failure.

## Verification

The live tests use `/usr/bin/python3` and real bubblewrap. Each negative test
checks that relevant host files remain unchanged:

- `test_sandbox_negative_tmp_write_is_private`: writes to private `/tmp` do not
  create the corresponding host file.
- `test_sandbox_negative_absolute_write_outside_workspace`: an absolute sibling
  path cannot be written.
- `test_sandbox_negative_symlink_escape`: a symlink cannot modify its host target.
- `test_sandbox_negative_network_connect`: an external socket connection reports
  an unreachable network in the task output.
- `test_sandbox_negative_read_unbound_host_file`: a secret outside the binds
  cannot be read.
- `test_sandbox_negative_host_environment_leak`: a non-allowlisted host variable
  is absent, while an allowlisted variable is available.
- `test_sandbox_negative_read_only_root_write`: a configured root cannot be written.
- `test_sandbox_negative_hard_link_is_rejected`: a linked host inode blocks launch.

`test_sandbox_runner_timeout_kills_process_and_descendant` checks timeout flags,
reaping and absence of a surviving child in host `/proc`. Other live tests check
workspace reads/writes and hashes, combined output truncation, tmpfs capacity,
and exit-code preservation. Policy tests check malformed/unreadable JSON,
missing/relative/deleted workspaces, invalid fields and binds, command construction,
network refusal, and missing-binary refusal without launching any subprocess.

Live tests skip only when the bwrap binary is absent, with an explicit reason.
In CI absence is a failure, and installation from apt must succeed. Namespace
or mount restrictions are failures, never skip conditions. Host seccomp filters
also apply to bwrap: if they deny its NETLINK_ROUTE socket, namespace setup fails
before the task executes. That failure does not permit an unsandboxed retry.

```sh
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/pytest -q -k sandbox -rs
```

Bubblewrap option semantics: [upstream manual](https://github.com/containers/bubblewrap/blob/main/bwrap.xml).
