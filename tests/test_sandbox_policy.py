import json
from pathlib import Path

import pytest

from agent_mcp_gate.sandbox import (
    SandboxPolicy,
    SandboxPolicyError,
    SandboxUnavailable,
    build_bwrap_argv,
    run_in_sandbox,
)
from agent_mcp_gate.sandbox.policy import DEFAULT_ENV_ALLOWLIST, DEFAULT_READ_ONLY_ROOTS


def test_sandbox_policy_defaults_and_json(tmp_path):
    policy = SandboxPolicy.from_dict({"workspace": str(tmp_path)})
    assert policy.read_only_roots == DEFAULT_READ_ONLY_ROOTS
    assert policy.env_allowlist == DEFAULT_ENV_ALLOWLIST
    assert policy.network is False
    assert policy.timeout_s == 60
    assert policy.max_output_bytes == 1024 * 1024
    assert policy.tmpfs_size == 64 * 1024 * 1024
    file = tmp_path / "policy.json"
    file.write_text(json.dumps(policy.snapshot()))
    assert SandboxPolicy.from_json(file) == policy


@pytest.mark.parametrize(
    "data", [{}, {"workspace": "relative"}, {"workspace": "/nonexistent-mc003"}]
)
def test_sandbox_policy_missing_or_invalid_workspace(data):
    with pytest.raises(SandboxPolicyError, match="workspace"):
        SandboxPolicy.from_dict(data)


@pytest.mark.parametrize("content", ["{broken", "[]", '{"workspace": null}', "\xff"])
def test_sandbox_policy_broken_json(tmp_path, content):
    file = tmp_path / "broken.json"
    file.write_bytes(content.encode("latin-1"))
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy.from_json(file)


def test_sandbox_policy_unreadable_json(tmp_path):
    with pytest.raises(SandboxPolicyError, match="cannot read"):
        SandboxPolicy.from_json(tmp_path / "absent.json")
    with pytest.raises(SandboxPolicyError, match="cannot read"):
        SandboxPolicy.from_json(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"network": "false"},
        {"timeout_s": True},
        {"timeout_s": 0},
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
        {"max_output_bytes": -1},
        {"max_output_bytes": True},
        {"tmpfs_size": 0},
        {"read_only_roots": "/usr"},
        {"read_only_roots": ["/"]},
        {"read_only_roots": ["relative"]},
        {"env_allowlist": ["A=B"]},
        {"env_allowlist": ["PATH", "PATH"]},
        {"unexpected": True},
    ],
)
def test_sandbox_policy_invalid_fields(tmp_path, overrides):
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy.from_dict({"workspace": str(tmp_path), **overrides})


def test_sandbox_policy_overlapping_bind_is_rejected(tmp_path):
    with pytest.raises(SandboxPolicyError, match="overlaps"):
        SandboxPolicy(workspace=tmp_path, read_only_roots=[str(tmp_path)])
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy(workspace="/")
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy(workspace="/tmp")


def test_sandbox_policy_network_true_is_recorded_but_execution_rejected(tmp_path, monkeypatch):
    policy = SandboxPolicy.from_dict({"workspace": str(tmp_path), "network": True})
    assert policy.snapshot()["network"] is True
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("task was launched"))
    with pytest.raises(SandboxPolicyError, match="network execution is not supported"):
        build_bwrap_argv(policy, ["/bin/true"])
    with pytest.raises(SandboxPolicyError, match="network execution is not supported"):
        run_in_sandbox(policy, ["/bin/true"])


def test_sandbox_policy_missing_bwrap_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("task was launched"))
    with pytest.raises(SandboxUnavailable, match="unsandboxed execution is forbidden"):
        run_in_sandbox(SandboxPolicy(workspace=tmp_path), ["/bin/true"])


def test_sandbox_policy_deleted_workspace_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = SandboxPolicy(workspace=workspace)
    workspace.rmdir()
    with pytest.raises(SandboxPolicyError, match="workspace"):
        build_bwrap_argv(policy, ["/bin/true"])


def test_sandbox_policy_argv_and_bind_order(tmp_path, monkeypatch):
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("MC003_PRIVATE", "secret")
    policy = SandboxPolicy(workspace=tmp_path, env_allowlist=["LANG"])
    argv = ["/bin/echo", "--share-net"]
    command = build_bwrap_argv(policy, argv)
    assert command[:5] == [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
    ]
    assert command[command.index("--") + 1 :] == argv
    assert command.index("--tmpfs") < command.index("--bind")
    assert command[command.index("--size") + 1] == str(policy.tmpfs_size)
    assert command[command.index("--setenv") + 1 : command.index("--setenv") + 3] == ["LANG", "C"]
    assert "MC003_PRIVATE" not in command
    assert "--clearenv" in command
    for root in policy.read_only_roots:
        if Path(root).exists():
            index = command.index(root)
            assert command[index - 1 : index + 2] == ["--ro-bind", root, root]
    for bad in [[], [""], ["/bin/true", "\0"], "/bin/true"]:
        with pytest.raises(SandboxPolicyError, match="argv"):
            build_bwrap_argv(policy, bad)
