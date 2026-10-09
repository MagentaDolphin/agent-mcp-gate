import errno
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from agent_mcp_gate.sandbox import SandboxPolicy, SandboxPolicyError, run_in_sandbox

PYTHON = "/usr/bin/python3"


@pytest.fixture
def sandbox(tmp_path):
    if shutil.which("bwrap") is None:
        reason = "live sandbox tests require bwrap; binary is not installed"
        if os.environ.get("CI"):
            pytest.fail(reason + "; CI must install bubblewrap")
        pytest.skip(reason)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "host"
    outside.mkdir()
    (outside / "secret.txt").write_text("host secret")
    return SandboxPolicy(workspace=workspace), workspace, outside


def _host_snapshot(outside):
    return {file.name: file.read_bytes() for file in outside.iterdir()}


def _run(policy, source):
    return run_in_sandbox(policy, [PYTHON, "-c", source])


def test_sandbox_runner_reads_and_writes_workspace(sandbox):
    policy, workspace, outside = sandbox
    (workspace / "input.txt").write_text("input")
    before = _host_snapshot(outside)
    result = _run(
        policy,
        "from pathlib import Path; "
        "text = Path('input.txt').read_text(); Path('result.txt').write_text(text + ' ok'); "
        "print(text)",
    )
    assert result.rc == 0, result.stderr
    assert result.stdout == "input\n"
    assert result.stderr == ""
    assert (workspace / "result.txt").read_text() == "input ok"
    assert result.workspace_before == {"input.txt": hashlib.sha256(b"input").hexdigest()}
    assert result.workspace_after["result.txt"] == hashlib.sha256(b"input ok").hexdigest()
    assert result.policy_snapshot == policy.snapshot()
    assert result.duration_s >= 0
    assert not result.timed_out and not result.truncated
    assert _host_snapshot(outside) == before


def test_sandbox_negative_tmp_write_is_private(sandbox):
    policy, _, outside = sandbox
    host_file = Path("/tmp") / f"mc003-{uuid.uuid4().hex}"
    before = _host_snapshot(outside)
    assert not host_file.exists()
    result = _run(
        policy, f"from pathlib import Path; Path({str(host_file)!r}).write_text('private')"
    )
    assert result.rc == 0, result.stderr
    assert not host_file.exists()
    assert _host_snapshot(outside) == before


def test_sandbox_negative_absolute_write_outside_workspace(sandbox):
    policy, _, outside = sandbox
    before = _host_snapshot(outside)
    target = outside / "escaped.txt"
    result = _run(policy, f"from pathlib import Path; Path({str(target)!r}).write_text('escape')")
    assert result.rc != 0
    assert "FileNotFoundError" in result.stderr
    assert not target.exists()
    assert _host_snapshot(outside) == before


def test_sandbox_negative_symlink_escape(sandbox):
    policy, workspace, outside = sandbox
    target = outside / "secret.txt"
    (workspace / "escape").symlink_to(target)
    before = _host_snapshot(outside)
    result = _run(policy, "from pathlib import Path; Path('escape').write_text('changed')")
    assert result.rc != 0
    assert "FileNotFoundError" in result.stderr
    assert "escape" not in result.workspace_before
    assert "escape" not in result.workspace_after
    assert _host_snapshot(outside) == before


def test_sandbox_negative_network_connect(sandbox):
    policy, _, outside = sandbox
    before = _host_snapshot(outside)
    result = _run(
        policy,
        """
import socket
import sys
try:
    socket.create_connection(('192.0.2.1', 443), timeout=0.5)
except OSError as exc:
    print(f'network-blocked:{exc.errno}', flush=True)
    sys.exit(7)
sys.exit(99)
""",
    )
    assert result.rc == 7, result.stderr
    assert f"network-blocked:{errno.ENETUNREACH}" in result.stdout
    assert _host_snapshot(outside) == before


def test_sandbox_negative_read_unbound_host_file(sandbox):
    policy, _, outside = sandbox
    before = _host_snapshot(outside)
    target = outside / "secret.txt"
    result = _run(policy, f"from pathlib import Path; print(Path({str(target)!r}).read_text())")
    assert result.rc != 0
    assert "FileNotFoundError" in result.stderr
    assert "host secret" not in result.stdout
    assert _host_snapshot(outside) == before


def test_sandbox_negative_host_environment_leak(sandbox, monkeypatch):
    policy, _, outside = sandbox
    before = _host_snapshot(outside)
    monkeypatch.setenv("MC003_PRIVATE", "host secret")
    monkeypatch.setenv("LANG", "C")
    result = _run(
        policy,
        "import os; assert 'MC003_PRIVATE' not in os.environ; "
        "assert os.environ['LANG'] == 'C'; print('environment-filtered')",
    )
    assert result.rc == 0, result.stderr
    assert result.stdout == "environment-filtered\n"
    assert _host_snapshot(outside) == before


def test_sandbox_negative_read_only_root_write(sandbox):
    policy, _, outside = sandbox
    before = _host_snapshot(outside)
    target = Path("/usr") / f"mc003-{uuid.uuid4().hex}"
    assert not target.exists()
    result = _run(policy, f"from pathlib import Path; Path({str(target)!r}).write_text('escape')")
    assert result.rc != 0
    assert "Read-only file system" in result.stderr
    assert not target.exists()
    assert _host_snapshot(outside) == before


def test_sandbox_negative_hard_link_is_rejected(sandbox):
    policy, workspace, outside = sandbox
    before = _host_snapshot(outside)
    (workspace / "escape").hardlink_to(outside / "secret.txt")
    with pytest.raises(SandboxPolicyError, match="hard links"):
        _run(policy, "from pathlib import Path; Path('escape').write_text('changed')")
    assert _host_snapshot(outside) == before


def test_sandbox_runner_timeout_kills_process_and_descendant(sandbox, monkeypatch):
    original_policy, _, outside = sandbox
    policy = SandboxPolicy.from_dict({**original_policy.snapshot(), "timeout_s": 0.5})
    marker = f"mc003-child-{uuid.uuid4().hex}"
    before = _host_snapshot(outside)
    popen = subprocess.Popen
    processes = []

    def record_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)
    result = _run(
        policy,
        f"""
import subprocess
import sys
import time
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', {marker!r}])
print('child-started', flush=True)
time.sleep(30)
""",
    )
    assert result.timed_out
    assert result.rc == -9
    assert "child-started" in result.stdout, result.stderr
    assert result.duration_s < 5
    assert len(processes) == 1 and processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)
    for process_dir in Path("/proc").iterdir():
        if process_dir.name.isdecimal():
            try:
                command = (process_dir / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            assert marker.encode() not in command, f"descendant still alive: {process_dir.name}"
    assert _host_snapshot(outside) == before


@pytest.mark.parametrize("limit", [0, 1000])
def test_sandbox_runner_output_is_bounded_and_drained(sandbox, limit):
    original_policy, _, _ = sandbox
    policy = SandboxPolicy.from_dict({**original_policy.snapshot(), "max_output_bytes": limit})
    result = _run(policy, "import os; os.write(1, b'o' * 200000); os.write(2, b'e' * 200000)")
    assert result.rc == 0
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == limit
    assert result.truncated and not result.timed_out


def test_sandbox_runner_private_tmpfs_has_size_limit(sandbox):
    original_policy, _, outside = sandbox
    policy = SandboxPolicy.from_dict({**original_policy.snapshot(), "tmpfs_size": 1024 * 1024})
    before = _host_snapshot(outside)
    result = _run(
        policy, "from pathlib import Path; Path('/tmp/large').write_bytes(b'x' * 2097152)"
    )
    assert result.rc != 0
    assert "No space left on device" in result.stderr
    assert _host_snapshot(outside) == before


def test_sandbox_runner_nonzero_exit_is_preserved(sandbox):
    policy, _, _ = sandbox
    result = _run(policy, "import sys; print('error', file=sys.stderr); sys.exit(23)")
    assert result.rc == 23 and result.stderr == "error\n"
    assert not result.timed_out and not result.truncated


def test_sandbox_runner_custom_read_only_root_below_tmp(sandbox):
    original_policy, _, outside = sandbox
    policy = SandboxPolicy.from_dict(
        {
            **original_policy.snapshot(),
            "read_only_roots": [*original_policy.read_only_roots, str(outside)],
        }
    )
    before = _host_snapshot(outside)
    target = outside / "secret.txt"
    source = f"""
from pathlib import Path
import errno
path = Path({str(target)!r})
assert path.read_text() == 'host secret'
try:
    path.write_text('changed')
except OSError as exc:
    assert exc.errno == errno.EROFS
    print('read-only')
else:
    raise AssertionError('read-only root was writable')
"""
    result = _run(policy, source)
    assert result.rc == 0, result.stderr
    assert result.stdout == "read-only\n"
    assert _host_snapshot(outside) == before
