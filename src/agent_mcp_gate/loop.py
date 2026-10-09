"""Run one sandboxed task and record its machine acceptance decision."""

import fnmatch
import os
import stat
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .events import EventEmitter
from .gate import GateVerdict, evaluate, new_artifacts
from .sandbox.policy import SandboxPolicy
from .sandbox.runner import RunResult, run_in_sandbox


@dataclass(frozen=True)
class TaskOutcome:
    accepted: bool
    verdict: GateVerdict
    run: RunResult
    events_path: str | None


def _tree_metadata(workspace: str) -> dict[str, str]:
    """Supplement runner hashes with modes and entries it omits; never follow links."""
    result = {}

    def fail(error):
        raise error

    for directory, dirs, names, dir_fd in os.fwalk(workspace, onerror=fail):
        for name in sorted(dirs + names):
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            relative = (Path(directory) / name).relative_to(workspace).as_posix()
            value = f"mode:{info.st_mode}"
            if stat.S_ISLNK(info.st_mode):
                value += ":link:" + os.readlink(name, dir_fd=dir_fd)
            elif not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                value += f":special:{info.st_rdev}"
            result[relative] = value
    return result


def _merge_snapshot(hashes: dict, metadata: dict) -> dict[str, str]:
    return {name: hashes.get(name, "") + ":" + metadata.get(name, "") for name in hashes | metadata}


def _refuse_artifact(verdict: GateVerdict, detail: str) -> GateVerdict:
    checks = [
        {**check, "ok": False, "detail": detail}
        if check["code"] == "G02" and check["ok"]
        else check
        for check in verdict.checks
    ]
    reasons = [
        {"code": check["code"], "detail": check["detail"]} for check in checks if not check["ok"]
    ]
    return GateVerdict(False, checks, reasons)


def run_task(policy: SandboxPolicy, argv, artifact_glob=None, events_path=None) -> TaskOutcome:
    """Preserve the workspace on rejection; event writes are outside run snapshots."""
    emitter = EventEmitter(events_path)
    emitter.emit("run_started", argv=list(argv), policy=policy.snapshot())
    started = time.monotonic()
    before = {}
    run = None
    try:
        before = _tree_metadata(policy.workspace)
        run = run_in_sandbox(policy, argv)
        after = _tree_metadata(policy.workspace)
        run = replace(
            run,
            workspace_before=_merge_snapshot(run.workspace_before, before),
            workspace_after=_merge_snapshot(run.workspace_after, after),
        )
    except Exception as exc:
        # Setup/snapshot errors must still produce a recorded rejection, never a host fallback.
        error = f"sandbox_error:{type(exc).__name__}:{exc}"
        try:
            after = _tree_metadata(policy.workspace)
        except Exception:
            after = {}
        if run is None:
            run = RunResult(
                rc=-1,
                stdout="",
                stderr=error,
                duration_s=time.monotonic() - started,
                truncated=False,
                policy_snapshot=policy.snapshot(),
                workspace_before=before,
                workspace_after=after,
            )
        else:
            run = replace(
                run,
                rc=-1,
                stderr=run.stderr + "\n" + error,
                workspace_before=_merge_snapshot(run.workspace_before, before),
                workspace_after=_merge_snapshot(run.workspace_after, after),
            )
    emitter.emit(
        "run_finished",
        rc=run.rc,
        timed_out=run.timed_out,
        truncated=run.truncated,
        duration_s=run.duration_s,
    )
    emitter.emit("gate_started")
    verdict = evaluate(run, policy)
    try:
        artifacts = new_artifacts(run, policy)
        if artifact_glob is not None:
            artifacts = [
                path
                for path in artifacts
                if fnmatch.fnmatchcase(path.relative_to(policy.workspace).as_posix(), artifact_glob)
            ]
            if not artifacts:
                verdict = _refuse_artifact(verdict, f"no_matching_artifact:{artifact_glob}")
    except Exception:
        artifacts = []
        verdict = _refuse_artifact(verdict, "internal_error")
    for path in artifacts:
        emitter.emit("artifact_written", path=str(path))
    for check in verdict.checks:
        emitter.emit("gate_check", **check)
    emitter.emit(
        "gate_result",
        accepted=verdict.accepted,
        checks=verdict.checks,
        reasons=verdict.reasons,
    )
    emitter.emit("accepted" if verdict.accepted else "rejected", reasons=verdict.reasons)
    return TaskOutcome(
        accepted=verdict.accepted,
        verdict=verdict,
        run=run,
        events_path=str(events_path) if events_path is not None else None,
    )
