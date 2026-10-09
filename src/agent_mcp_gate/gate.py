"""Accept sandbox results using independent, fail-closed machine checks."""

import fnmatch
import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .sandbox.policy import SandboxPolicy
from .sandbox.runner import RunResult

SANITARY_BLOCKED_DIRECTORIES = frozenset({".venv", "__pycache__", ".git"})
SANITARY_BLOCKED_PATTERNS = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.o",
    "*.obj",
    "*.a",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.class",
    "*.aux",
    "*.log",
    "*.out",
    "*.toc",
    "*.lof",
    "*.lot",
    "*.fls",
    "*.fdb_latexmk",
    "*.synctex.gz",
    "*.dvi",
    "*.xdv",
)
NETWORK_INDICATORS = re.compile(
    r"https?://|\b(?:network|socket|curl|wget|ENETUNREACH|EHOSTUNREACH|"
    r"ConnectionRefusedError|ConnectionResetError|gaierror)\b|\bconnect\s*\("
    r"|connection (?:refused|timed out)|name or service not known"
    r"|temporary failure in name resolution",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateRule:
    code: str
    description: str
    function: Callable[[RunResult, SandboxPolicy, tuple], tuple[bool, str]]


@dataclass(frozen=True)
class GateVerdict:
    accepted: bool
    checks: list[dict]
    reasons: list[dict]


def _path(workspace: Path, value) -> Path:
    path = Path(value)
    return Path(os.path.abspath(path if path.is_absolute() else workspace / path))


def _changes(run: RunResult, extra_paths: tuple) -> set:
    before, after = run.workspace_before, run.workspace_after
    return {
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    } | set(extra_paths)


def new_artifacts(run: RunResult, policy: SandboxPolicy) -> list[Path]:
    """Only new, nonempty regular files recorded in the run are artifact evidence."""
    workspace = Path(policy.workspace)
    artifacts = []
    for name in sorted(run.workspace_after.keys() - run.workspace_before.keys()):
        path = _path(workspace, name)
        if not path.is_relative_to(workspace) or not path.parent.resolve().is_relative_to(
            workspace
        ):
            continue
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_size > 0:
            artifacts.append(path)
    return artifacts


def _check_g01(run, policy, extra_paths):
    ok = run.rc == 0 and run.timed_out is False
    return ok, "completed" if ok else f"execution_failed:rc={run.rc},timed_out={run.timed_out}"


def _check_g02(run, policy, extra_paths):
    artifacts = new_artifacts(run, policy)
    return bool(artifacts), "new_nonempty_file" if artifacts else "no_new_nonempty_file"


def _check_g03(run, policy, extra_paths):
    workspace = Path(policy.workspace)
    if Path(run.policy_snapshot["workspace"]).resolve() != workspace:
        return False, "workspace_mismatch"
    outside = []
    for name in _changes(run, extra_paths):
        path = _path(workspace, name)
        # The final symlink itself is an entry inside the workspace; G04 checks its target.
        if not path.is_relative_to(workspace) or not path.parent.resolve().is_relative_to(
            workspace
        ):
            outside.append(str(name))
    return not outside, "inside_workspace" if not outside else "outside_workspace:" + ",".join(
        sorted(outside)
    )


def _raise_walk_error(error):
    raise error


def _check_g04(run, policy, extra_paths):
    workspace = Path(policy.workspace)
    before = {_path(workspace, name) for name in run.workspace_before}
    candidates = {_path(workspace, name) for name in run.workspace_after} | {
        _path(workspace, name) for name in extra_paths
    }
    # MC-003 snapshots omit links. Inspect links directly, including directory/dangling links.
    for directory, dirs, names in os.walk(workspace, followlinks=False, onerror=_raise_walk_error):
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                candidates.add(path)
    escaped = []
    for path in candidates - before:
        if path.is_symlink() and not path.resolve().is_relative_to(workspace):
            escaped.append(str(path))
    return not escaped, "links_inside_workspace" if not escaped else "escaping_symlink:" + ",".join(
        sorted(escaped)
    )


def _check_g05(run, policy, extra_paths):
    ok = run.truncated is False
    return ok, "complete_output" if ok else "incomplete_result:output_truncated"


def _check_g06(run, policy, extra_paths):
    if run.policy_snapshot["network"] or policy.network:
        return False, "network_policy_unsupported"
    match = NETWORK_INDICATORS.search(run.stderr)
    return (
        (False, "network_indicator:" + match.group()) if match else (True, "no_network_indicator")
    )


def _check_g07(run, policy, extra_paths):
    workspace = Path(policy.workspace)
    blocked = []
    for name in _changes(run, extra_paths):
        path = _path(workspace, name)
        parts = path.relative_to(workspace).parts if path.is_relative_to(workspace) else path.parts
        if SANITARY_BLOCKED_DIRECTORIES.intersection(parts) or any(
            fnmatch.fnmatchcase(path.name.lower(), pattern) for pattern in SANITARY_BLOCKED_PATTERNS
        ):
            blocked.append(str(name))
    return not blocked, "sanitary_paths" if not blocked else "unsanitary_paths:" + ",".join(
        sorted(blocked)
    )


RULES = (
    GateRule("G01", "Successful exit without timeout", _check_g01),
    GateRule("G02", "A new nonempty regular file", _check_g02),
    GateRule("G03", "All known changes stay inside the workspace", _check_g03),
    GateRule("G04", "New symlinks stay inside the workspace", _check_g04),
    GateRule("G05", "Captured output is complete", _check_g05),
    GateRule("G06", "Network access is unsupported", _check_g06),
    GateRule("G07", "Changes avoid binary/build debris and service directories", _check_g07),
)


def evaluate(
    run_result: RunResult, policy: SandboxPolicy, extra_paths: Iterable = ()
) -> GateVerdict:
    """Evaluate every rule; exceptions become machine-readable refusal reasons."""
    checks = []
    try:
        paths = tuple(extra_paths)
        paths_error = False
    except Exception:
        paths = ()
        paths_error = True
    for rule in RULES:
        try:
            if paths_error:
                raise ValueError("invalid extra_paths")
            ok, detail = rule.function(run_result, policy, paths)
            if type(ok) is not bool or not isinstance(detail, str):
                raise TypeError("invalid check result")
        except Exception:
            ok, detail = False, "internal_error"
        checks.append({"code": rule.code, "ok": ok, "detail": detail})
    reasons = [
        {"code": check["code"], "detail": check["detail"]} for check in checks if not check["ok"]
    ]
    return GateVerdict(accepted=not reasons, checks=checks, reasons=reasons)
