import json
import os
import shutil
from pathlib import Path

import pytest

from agent_mcp_gate import loop
from agent_mcp_gate.sandbox.policy import SandboxPolicy, SandboxUnavailable
from agent_mcp_gate.sandbox.runner import RunResult


@pytest.fixture
def policy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SandboxPolicy(workspace=workspace)


@pytest.fixture
def live_policy(policy):
    if shutil.which("bwrap") is None:
        reason = "live task tests require bubblewrap"
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)
    return policy


def read_events(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    records = [json.loads(line) for line in lines]
    assert all("ts" in record and "event" in record for record in records)
    return records


def test_live_task_events_file_and_accepted_artifact(live_policy, tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    outcome = loop.run_task(
        live_policy,
        ["/usr/bin/python3", "-c", "from pathlib import Path; Path('result.txt').write_text('ok')"],
        artifact_glob="*.txt",
        events_path=path,
    )
    assert outcome.accepted, outcome.verdict.reasons
    assert outcome.run.rc == 0
    assert outcome.events_path == str(path)
    records = read_events(path)
    assert [record["event"] for record in records] == [
        "run_started",
        "run_finished",
        "gate_started",
        "artifact_written",
        *["gate_check"] * 7,
        "gate_result",
        "accepted",
    ]
    result = next(record for record in records if record["event"] == "gate_result")
    assert result["accepted"] is True and result["checks"] == outcome.verdict.checks
    assert next(record for record in records if record["event"] == "artifact_written")[
        "path"
    ] == str(Path(live_policy.workspace) / "result.txt")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert [json.loads(line) for line in captured.err.splitlines()] == records


def test_live_rejection_preserves_workspace_and_artifact(live_policy, tmp_path):
    path = tmp_path / "events.jsonl"
    outcome = loop.run_task(
        live_policy,
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('debug'); raise SystemExit(7)",
        ],
        events_path=path,
    )
    assert not outcome.accepted
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G01"]
    assert (Path(live_policy.workspace) / "result.txt").read_text() == "debug"
    records = read_events(path)
    assert records[-1]["event"] == "rejected"
    assert (
        next(record for record in records if record["event"] == "gate_result")["accepted"] is False
    )


def test_live_new_directory_link_is_part_of_tree_and_refused(live_policy, tmp_path):
    outcome = loop.run_task(
        live_policy,
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('ok'); "
            "Path('escape').symlink_to('/usr', target_is_directory=True)",
        ],
        events_path=tmp_path / "events.jsonl",
    )
    assert not outcome.accepted
    assert "escape" not in outcome.run.workspace_before
    assert "escape" in outcome.run.workspace_after
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G04"]
    assert (Path(live_policy.workspace) / "escape").is_symlink()


def test_live_new_service_directory_is_refused(live_policy):
    outcome = loop.run_task(
        live_policy,
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('ok'); Path('.git').mkdir()",
        ],
    )
    assert not outcome.accepted
    assert ".git" in outcome.run.workspace_after
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G07"]


def test_live_existing_link_is_not_mistaken_for_new_link(live_policy):
    (Path(live_policy.workspace) / "existing").symlink_to("/usr", target_is_directory=True)
    outcome = loop.run_task(
        live_policy,
        ["/usr/bin/python3", "-c", "from pathlib import Path; Path('result.txt').write_text('ok')"],
    )
    assert outcome.accepted, outcome.verdict.reasons
    assert outcome.run.workspace_before["existing"] == outcome.run.workspace_after["existing"]


def test_live_events_inside_workspace_do_not_satisfy_artifact_rule(live_policy):
    path = Path(live_policy.workspace) / "events.jsonl"
    outcome = loop.run_task(live_policy, ["/usr/bin/python3", "-c", "pass"], events_path=path)
    assert not outcome.accepted
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G02"]
    assert read_events(path)[-1]["event"] == "rejected"


def test_live_artifact_glob_requires_a_matching_new_artifact(live_policy):
    outcome = loop.run_task(
        live_policy,
        ["/usr/bin/python3", "-c", "from pathlib import Path; Path('other.txt').write_text('ok')"],
        artifact_glob="report-*.md",
    )
    assert not outcome.accepted
    assert outcome.verdict.reasons == [
        {"code": "G02", "detail": "no_matching_artifact:report-*.md"}
    ]
    assert (Path(live_policy.workspace) / "other.txt").exists()


def test_live_modified_service_file_is_refused(live_policy):
    service = Path(live_policy.workspace) / ".git"
    service.mkdir()
    (service / "config").write_text("before")
    outcome = loop.run_task(
        live_policy,
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('ok'); "
            "Path('.git/config').write_text('after')",
        ],
    )
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G07"]
    assert not outcome.accepted


def test_unavailable_sandbox_records_rejection_without_fallback(policy, tmp_path, monkeypatch):
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise SandboxUnavailable("bwrap not available")

    monkeypatch.setattr(loop, "run_in_sandbox", unavailable)
    path = tmp_path / "events.jsonl"
    outcome = loop.run_task(policy, ["unused"], events_path=path)
    assert len(calls) == 1
    assert not outcome.accepted
    assert outcome.run.rc == -1
    assert "sandbox_error:SandboxUnavailable" in outcome.run.stderr
    records = read_events(path)
    assert records[-1]["event"] == "rejected"
    assert len([record for record in records if record["event"] == "gate_check"]) == 7
    assert Path(policy.workspace).is_dir()


def test_loop_metadata_detects_mode_changes(policy, monkeypatch):
    workspace = Path(policy.workspace)
    existing = workspace / "input.txt"
    existing.write_text("unchanged content")

    def fake_runner(policy, argv):
        existing.chmod(0o600)
        (workspace / "result.txt").write_text("ok")
        return RunResult(
            rc=0,
            stdout="",
            stderr="",
            duration_s=0.1,
            truncated=False,
            policy_snapshot=policy.snapshot(),
            workspace_before={"input.txt": "same"},
            workspace_after={"input.txt": "same", "result.txt": "new"},
        )

    existing.chmod(0o644)
    monkeypatch.setattr(loop, "run_in_sandbox", fake_runner)
    outcome = loop.run_task(policy, ["unused"])
    assert outcome.accepted
    assert outcome.run.workspace_before["input.txt"] != outcome.run.workspace_after["input.txt"]


def test_live_deleted_service_directory_is_refused(live_policy):
    (Path(live_policy.workspace) / ".git").mkdir()
    outcome = loop.run_task(
        live_policy,
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('result.txt').write_text('ok'); Path('.git').rmdir()",
        ],
    )
    assert not outcome.accepted
    assert ".git" in outcome.run.workspace_before and ".git" not in outcome.run.workspace_after
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G07"]


def test_snapshot_error_after_execution_cannot_accept(policy, tmp_path, monkeypatch):
    metadata = loop._tree_metadata
    calls = []

    def unreliable(workspace):
        calls.append(workspace)
        if len(calls) == 2:
            raise OSError("metadata unavailable")
        return metadata(workspace)

    def fake_runner(policy, argv):
        (Path(policy.workspace) / "result.txt").write_text("inspect this")
        return RunResult(
            rc=0,
            stdout="",
            stderr="",
            duration_s=0.1,
            truncated=False,
            policy_snapshot=policy.snapshot(),
            workspace_before={},
            workspace_after={"result.txt": "new"},
        )

    monkeypatch.setattr(loop, "_tree_metadata", unreliable)
    monkeypatch.setattr(loop, "run_in_sandbox", fake_runner)
    path = tmp_path / "events.jsonl"
    outcome = loop.run_task(policy, ["unused"], events_path=path)
    assert not outcome.accepted
    assert [reason["code"] for reason in outcome.verdict.reasons] == ["G01"]
    assert "sandbox_error:OSError" in outcome.run.stderr
    assert "result.txt" in outcome.run.workspace_after
    assert (Path(policy.workspace) / "result.txt").read_text() == "inspect this"
    assert read_events(path)[-1]["event"] == "rejected"
