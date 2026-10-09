"""Construct bubblewrap commands and capture bounded results without a fallback."""

import errno
import hashlib
import os
import selectors
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import SandboxPolicy, SandboxPolicyError, SandboxUnavailable


@dataclass(frozen=True)
class RunResult:
    rc: int
    stdout: str
    stderr: str
    duration_s: float
    truncated: bool
    policy_snapshot: dict
    workspace_before: dict[str, str]
    workspace_after: dict[str, str]
    timed_out: bool = False


def build_bwrap_argv(policy: SandboxPolicy, argv):
    """Build arguments without launching anything or changing the host environment."""
    if not isinstance(policy, SandboxPolicy):
        raise SandboxPolicyError("a validated SandboxPolicy is required")
    # Revalidate filesystem state as it may have changed since policy loading.
    policy = SandboxPolicy.from_dict(policy.snapshot())
    if policy.network:
        raise SandboxPolicyError("network=True: network execution is not supported")
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
        or not argv[0]
    ):
        raise SandboxPolicyError("argv must be a nonempty list of strings without NUL")
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--unshare-net"]
    # /tmp must be mounted first: read-only roots and workspaces may live below it.
    command.extend(["--size", str(policy.tmpfs_size), "--tmpfs", "/tmp"])
    for root in policy.read_only_roots:
        if Path(root).exists():
            command.extend(["--ro-bind", root, root])
    command.extend(["--bind", policy.workspace, policy.workspace, "--chdir", policy.workspace])
    command.append("--clearenv")
    for name in policy.env_allowlist:
        if name in os.environ:
            command.extend(["--setenv", name, os.environ[name]])
    return [*command, "--", *argv]


def _workspace_snapshot(workspace):
    """Hash regular files through directory descriptors; never follow task symlinks."""
    snapshot = {}

    def fail(error):
        raise error

    try:
        for directory, dirs, names, dir_fd in os.fwalk(workspace, onerror=fail):
            dirs.sort()
            for name in sorted(names):
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOENT):
                        continue
                    raise
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    # A writable hard link could mutate a host file outside the bind.
                    if info.st_nlink != 1:
                        raise SandboxPolicyError(f"workspace hard links are unsupported: {name}")
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                relative = (Path(directory) / name).relative_to(workspace).as_posix()
                snapshot[relative] = digest
    except OSError as exc:
        raise SandboxPolicyError(f"cannot snapshot workspace: {exc}") from exc
    return snapshot


def _kill_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _capture(process, policy):
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    remaining = policy.max_output_bytes
    truncated = False
    timed_out = False
    deadline = time.monotonic() + policy.timeout_s
    with selectors.DefaultSelector() as selector:
        for name, pipe in [("stdout", process.stdout), ("stderr", process.stderr)]:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            delay = deadline - time.monotonic()
            if delay <= 0 and not timed_out:
                timed_out = True
                _kill_process_group(process)
            for key, _ in selector.select(timeout=0.1 if timed_out else min(0.1, max(0, delay))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                kept = min(remaining, len(chunk))
                buffers[key.data].extend(chunk[:kept])
                remaining -= kept
                truncated |= kept < len(chunk)
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()) if not timed_out else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            process.wait()
    return buffers, truncated, timed_out


def run_in_sandbox(policy: SandboxPolicy, argv):
    command = build_bwrap_argv(policy, argv)
    binary = shutil.which("bwrap")
    if binary is None:
        raise SandboxUnavailable("bwrap is required; unsandboxed execution is forbidden")
    command[0] = binary
    before = _workspace_snapshot(policy.workspace)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise SandboxUnavailable(f"cannot start bwrap: {exc}") from exc
    with process:
        try:
            buffers, truncated, timed_out = _capture(process, policy)
        except BaseException:
            _kill_process_group(process)
            process.wait()
            raise
    duration = time.monotonic() - started
    return RunResult(
        rc=-9 if timed_out else process.returncode,
        stdout=buffers["stdout"].decode("utf-8", errors="replace"),
        stderr=buffers["stderr"].decode("utf-8", errors="replace"),
        duration_s=duration,
        truncated=truncated,
        policy_snapshot=policy.snapshot(),
        workspace_before=before,
        workspace_after=_workspace_snapshot(policy.workspace),
        timed_out=timed_out,
    )
