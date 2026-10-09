# Tool contracts

Both handlers use only local files and the standard library. They are available
through `tools/list` and `tools/call`, and through direct Python imports without
a sandbox. `readOnlyHint` describes the handler; it does not enforce permissions.

## scan_asset_usage

Callable: `agent_mcp_gate.tools.scan_asset_usage.scan_asset_usage`.
Read-only: **true**. Reads a directory of asset JSON fixtures and returns
transitive usage from a level file. No writes, network access, or LLM calls.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "root": {"type": "string"},
    "level": {"type": "string"},
    "include_soft": {"type": "boolean", "default": false}
  },
  "required": ["root", "level"],
  "additionalProperties": false
}
```

`root` must name an existing directory. A relative `level` is resolved under
`root`; an absolute level path is also accepted. The level file is a JSON array,
for example `[{"asset":"map"}]`. Every entry needs a string `asset` field.

All other `*.json` files recursively under `root` must be asset objects:

```json
{"asset":"map","package":"/Game/Map","references":["mesh"],"hard":true}
```

The four fields above are mandatory and have the illustrated types. Additional
fixture fields are ignored. The selected level file is excluded from the asset
index. Other level files in the tree are not a supported fixture shape.

`hard` is an attribute of the target asset. With the default `include_soft=false`,
an asset whose definitions are all soft is neither reached nor traversed; its
unique definition is reported as unused. This applies to direct level roots too.
With `include_soft=true`, soft assets and their descendants are traversed.
Unknown referenced IDs are always eligible and reported as missing when reached.

Breadth-first traversal uses sorted roots and references, removes duplicate roots,
and terminates on cycles. Depth zero means a direct level root. Duplicate asset
IDs are ambiguous even if unreachable; all eligible definitions of a reachable
ambiguous ID contribute references. Ambiguous IDs have no selected package.

### Output schema

```json
{
  "type": "object",
  "required": ["assets", "counts"],
  "additionalProperties": false,
  "properties": {
    "assets": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "package", "verdict", "depth", "path"],
        "additionalProperties": false,
        "properties": {
          "id": {"type": "string"},
          "package": {"type": ["string", "null"]},
          "verdict": {"type": "string", "enum": ["used", "unused", "missing", "ambiguous"]},
          "depth": {"type": ["integer", "null"], "minimum": 0},
          "path": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "counts": {
      "type": "object",
      "required": ["used", "unused", "missing", "ambiguous"],
      "additionalProperties": false,
      "properties": {
        "used": {"type": "integer", "minimum": 0},
        "unused": {"type": "integer", "minimum": 0},
        "missing": {"type": "integer", "minimum": 0},
        "ambiguous": {"type": "integer", "minimum": 0}
      }
    }
  }
}
```

Rows are sorted by ID. `path` is the shortest chain of asset IDs from a level
root to the row's ID, including both ends. Unreachable rows have `path: []` and
`depth: null`. Missing and ambiguous rows have `package: null`. Counts count IDs,
including one ambiguous row per duplicated ID, rather than fixture files.

### Rights, prohibitions, and errors

Requires read access to the level and asset files and directory traversal rights.
There is no workspace restriction on reads. This handler does not provide a
security sandbox. It never modifies files, resolves a missing asset through the
network, or silently discards missing reachable IDs. File and directory contents
must remain stable during a scan to obtain repeatable results.

Invalid tool arguments cause `RpcError(-32602)` before reading files. Invalid
fixture JSON, wrong fixture structures, missing paths, and other read failures
raise Python exceptions for direct callers and become `-32603` over MCP. A valid
scan with missing or ambiguous assets is a successful result, not a protocol error.

## apply_scoped_patch

Callable: `agent_mcp_gate.tools.apply_scoped_patch.apply_scoped_patch`.
Read-only: **false**. Applies one unified diff to one existing UTF-8 regular file
under the directory configured by `AGENT_MCP_GATE_WORKSPACE`.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "patch": {"type": "string"}
  },
  "required": ["path", "patch"],
  "additionalProperties": false
}
```

`path` is relative to the configured workspace. Absolute paths (even within the
workspace), empty paths, and any `..` component are refused before filesystem
access. Symlinks resolving outside the workspace are refused. Symlinks resolving
inside it are allowed; the resolved file is replaced, preserving the link.

`patch` is a single-file line-oriented unified diff. Optional `---` / `+++`
headers describe the diff; they never select the write target. Standard `@@`
ranges, context, additions, deletions, multiple hunks, insert-only/delete-only
ranges, and `\ No newline at end of file` markers are supported. Git preambles,
binary patches, rename/create/delete operations, and multi-file diffs are not
supported. Deleting every line empties an existing file without removing it.

Every hunk must match the original file exactly at its declared old position.
New positions, line counts, range ordering, context, and line endings must also
match. There is no fuzzy search, offset recovery, or partial application. A patch
with no hunks, a nonmatching hunk, or no resulting change is refused.

### Output schema

```json
{
  "type": "object",
  "required": ["applied", "path", "hunks_applied"],
  "additionalProperties": false,
  "properties": {
    "applied": {"type": "boolean"},
    "path": {"type": "string"},
    "hunks_applied": {"type": "integer", "minimum": 0},
    "reason": {
      "type": "string",
      "enum": ["out_of_scope", "symlink_escape", "workspace_not_configured", "not_file",
               "not_found", "permission_denied", "invalid_encoding", "invalid_patch",
               "no_match", "io_error"]
    }
  }
}
```

`path` echoes the input. Success has `applied: true`, the number of applied hunks,
and no `reason`. Refusals have `applied: false`, `hunks_applied: 0`, and `reason`:

| Reason | Meaning |
| --- | --- |
| `out_of_scope` | Absolute/empty/parent path, invalid path, or unresolved path loop. |
| `symlink_escape` | Resolved path leaves workspace, or `O_NOFOLLOW` rejects a symlink race. |
| `workspace_not_configured` | Environment variable is absent or empty. |
| `not_file` | Target is the workspace itself or a nonregular file. |
| `not_found` | Workspace, target, or a parent component does not exist. |
| `permission_denied` | OS denies access, creation, or replacement. |
| `invalid_encoding` | Existing file is not UTF-8. |
| `invalid_patch` | Malformed or unsupported unified diff. |
| `no_match` | Empty/no-op patch, wrong positions, or nonmatching hunk. |
| `io_error` | Other OS failures. |

### Rights, side effects, and prohibitions

Requires read access to the target, traversal rights, and create/replace rights
in its parent directory. All checks and hunk matching precede mutation. A success
writes a temporary sibling, flushes it, preserves mode bits, and atomically
replaces the selected file. The temporary sibling is cleaned on failure. File
identity and other metadata are not preserved; concurrent edits are not merged.

Writes outside the workspace, following escaping symlinks, creating a target,
modifying nonregular files, partial patches, network calls, and LLM calls are
forbidden. Anchored directory descriptors and `O_NOFOLLOW` avoid following a new
symlink between path resolution and writing. This implementation uses POSIX
filesystem APIs, matching the project's Linux execution environment.

Invalid argument types or extra fields cause `-32602`. Expected filesystem and
patch refusals use the output above. Unexpected exceptions become `-32603` over
MCP. A refusal is an MCP tool result with `isError: true`, not a JSON-RPC error.

## MCP envelope and errors

`tools/list` returns each registered name, description, full `inputSchema`, and
`annotations.readOnlyHint`. Output schemas above document handler results; they
are not currently advertised as `outputSchema` or validated at runtime.

`tools/call` params are an object with required string `name`, optional object
`arguments` (default `{}`), and optional object `_meta` (accepted and ignored).
Other top-level fields are rejected. Tool arguments are validated against the
listed input schema; defaults are handled by the Python signature.

Successful calls return `content: [{"type":"text","text":"<JSON result>"}]`,
`structuredContent` containing the same result, and `isError`. Missing and
ambiguous scan verdicts leave `isError: false`.

| Code | Meaning |
| --- | --- |
| `-32700` | Invalid JSON, including nonstandard NaN/Infinity constants. |
| `-32600` | Invalid JSON-RPC envelope, response submitted as a request, or operation after shutdown. |
| `-32601` | Unknown method. |
| `-32602` | Unknown tool or argument-schema violation. |
| `-32603` | Unexpected handler/dispatch exception; exception details are not exposed. |

Validation `error.data` contains `path` and `reason`, also present in the message.
Nested paths use `levels[0].path` notation. Unknown tools have `data.path: "name"`
and a sorted `data.available` list. Notifications never receive replies during
dispatch, including unknown methods/tools and failing handlers. Malformed JSON
and invalid envelopes are rejected with error responses, ordinarily with a null
ID. Handler failures do not end the stdio loop.

`initialize` accepts optional `protocolVersion` (string), `capabilities` (object),
and `clientInfo` (object with required string `name`/`version` and optional string
`title`). Other initialization fields are rejected. It returns revision
`2025-06-18`, package server information, and `capabilities.tools.listChanged: false`.
`notifications/initialized`, `tools/list`, `shutdown`, and `exit` take no params
or an empty object. Tool calls/listing are also allowed without initialization.
The server does not implement pagination, JSON-RPC batch arrays, or other MCP
methods. Shutdown/exit are local extensions; EOF also terminates the process.
