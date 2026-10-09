# Machine acceptance

`gate.evaluate(run_result, policy, extra_paths=())` returns a `GateVerdict` with
`accepted`, seven ordered `checks` (`code`, `ok`, `detail`), and `reasons`
(`code`, `detail`) for every failed check. All rules execute even after a refusal.
An exception or invalid return value in a check produces `ok=false` and
`detail="internal_error"`; an unreadable extra-path iterable refuses all rules.
No stdout status message participates in acceptance.

| Rule | Requirement and what it catches | Negative test |
| --- | --- | --- |
| G01 | Exit code must be zero and `timed_out` must be false. Catches command failures and timeouts, even when a file was written. | `test_g01_rejects_failed_or_timed_out_run` |
| G02 | At least one path absent from the before snapshot must now be a nonempty regular file. Catches empty/no-op runs, empty files and edits to existing files. Symlinks, directories and merely announced artifacts do not qualify. | `test_g02_self_report_is_not_new_nonempty_artifact`, `test_extra_path_cannot_fabricate_artifact` |
| G03 | Every changed, created, deleted or additionally announced path must be inside the policy workspace, including its resolved parent. The run's recorded workspace must match the policy. Catches absolute/parent escapes, sibling-prefix tricks, deleted outside paths and writes through directory aliases. | `test_g03_rejects_every_known_outside_change`, `test_g03_rejects_file_beneath_escaping_directory_link` |
| G04 | New symlinks must resolve inside the workspace. Catches file, directory and dangling links to outside targets. Traversal never follows directory links. Direct evaluation of MC-003's file-only snapshots conservatively treats unrecorded links as new; the task loop records their prior existence. | `test_g04_detects_new_escaping_links_omitted_by_runner`, `test_live_new_directory_link_is_part_of_tree_and_refused` |
| G05 | `truncated` must be exactly false. Catches an incomplete output capture; refusal detail is `incomplete_result:output_truncated`. | `test_g05_rejects_truncated_output` |
| G06 | Both the recorded network policy and the supplied policy must disable network. Stderr must contain no network indicator. Catches network-enabled policy and messages naming network/socket operations, URLs, curl/wget, connection or DNS failures. | `test_g06_rejects_network_policy_or_stderr_evidence` |
| G07 | All known changes and extra paths must avoid `.venv`, `__pycache__`, `.git` and the filename patterns in `SANITARY_BLOCKED_PATTERNS`. Catches compiled binaries/bytecode and LaTeX/pandoc intermediate files, including deletions and empty service directories. | `test_g07_rejects_binary_build_debris_and_service_paths`, `test_g07_covers_deletions_and_announced_paths`, `test_live_new_service_directory_is_refused` |

The positive control is `test_all_rules_accept_factual_artifact`.
`test_one_failed_rule_still_runs_all_seven` checks continuation;
`test_internal_error_is_fail_closed_for_each_rule` injects an exception into each
rule separately and checks its machine refusal.

## Task loop and event contract

`loop.run_task(policy, argv, artifact_glob=None, events_path=None)` invokes the
existing bubblewrap runner, supplements its SHA-256 snapshots with file modes,
directories, symlinks and special entries, and evaluates the facts. If supplied,
`artifact_glob` must match at least one new nonempty regular file, using
`fnmatchcase` on its workspace-relative POSIX path; mismatch refuses G02.
Sandbox setup/snapshot failures also produce a recorded rejection. Rejected
workspaces and artifacts remain available for inspection; nothing is rolled back.

`events.emit(event, events_path=..., stderr=True, **fields)` and
`EventEmitter(events_path, stderr).emit(event, **fields)` append one UTF-8 JSON
object per line, with a UTC ISO timestamp `ts` and an `event` name. Destinations
are a caller-selected file, stderr, or both; stdout is untouched. Fields must be
JSON-serializable and cannot override `ts`. Serialization and destination errors
propagate instead of reporting successful event persistence.

The loop emits `run_started`, `run_finished`, `gate_started`, an
`artifact_written` record per observed matching artifact, seven `gate_check`
records, `gate_result`, then `accepted` or `rejected`. Artifact events report
observations even on rejected runs. `gate_result` includes the complete checks
and reasons. The returned `TaskOutcome` includes the same verdict, the augmented
`RunResult`, and the event-file path. Log initialization precedes task snapshots,
so an event file inside the workspace cannot itself satisfy G02. Use a log
outside the workspace when the coordinator needs task-inaccessible evidence.
`test_live_task_events_file_and_accepted_artifact` parses every event line from a
real sandboxed run and verifies the verdict; rejection, link tracking and
workspace retention also have live integration tests in `tests/test_loop.py`.

## Limits

The gate does not check task meaning, text quality or algorithm correctness.
It verifies exit/output and filesystem evidence, not whether an artifact solves
the requested problem. G07 is a filename/service-directory blacklist, not a
content or MIME classifier. G06's stderr indicators can match innocent messages
and cannot prove absence of all network attempts; namespace isolation remains
the enforcement mechanism. Snapshots describe final tree changes rather than
transient writes reverted before capture, and do not audit unreported host
paths; `extra_paths` supplies additional caller-known paths. Filesystem checks
assume the task is finished and the coordinator controls the workspace during
acceptance; event logs inside that workspace are not tamper-proof.
