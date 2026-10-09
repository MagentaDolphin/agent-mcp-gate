import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from agent_mcp_gate import gate
from agent_mcp_gate.sandbox.policy import SandboxPolicy
from agent_mcp_gate.sandbox.runner import RunResult


@pytest.fixture
def successful_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "result.txt"
    artifact.write_text("actual result", encoding="utf-8")
    policy = SandboxPolicy(workspace=workspace)
    run = RunResult(
        rc=0,
        stdout='{"status":"ok"}',
        stderr="",
        duration_s=0.1,
        truncated=False,
        policy_snapshot=policy.snapshot(),
        workspace_before={},
        workspace_after={"result.txt": hashlib.sha256(artifact.read_bytes()).hexdigest()},
    )
    return policy, run


def assert_only_refusal(verdict, code):
    assert not verdict.accepted
    assert [reason["code"] for reason in verdict.reasons] == [code]
    assert len(verdict.checks) == 7
    assert all(check["ok"] for check in verdict.checks if check["code"] != code)


def test_all_rules_accept_factual_artifact(successful_run):
    policy, run = successful_run
    verdict = gate.evaluate(run, policy)
    assert verdict.accepted
    assert verdict.reasons == []
    assert [check["code"] for check in verdict.checks] == [rule.code for rule in gate.RULES]
    assert all(check["ok"] is True for check in verdict.checks)


@pytest.mark.parametrize("rc,timed_out", [(7, False), (0, True), (-9, True)])
def test_g01_rejects_failed_or_timed_out_run(successful_run, rc, timed_out):
    policy, run = successful_run
    assert_only_refusal(gate.evaluate(replace(run, rc=rc, timed_out=timed_out), policy), "G01")


@pytest.mark.parametrize("artifact_state", ["missing", "empty", "preexisting", "modified"])
def test_g02_self_report_is_not_new_nonempty_artifact(successful_run, artifact_state):
    policy, run = successful_run
    artifact = Path(policy.workspace) / "result.txt"
    if artifact_state == "missing":
        artifact.unlink()
        run = replace(run, workspace_after={})
    elif artifact_state == "empty":
        artifact.write_text("")
    else:
        before = run.workspace_after if artifact_state == "preexisting" else {"result.txt": "old"}
        run = replace(run, workspace_before=before)
    assert_only_refusal(gate.evaluate(run, policy), "G02")


@pytest.mark.parametrize("change", ["created", "modified", "deleted", "extra", "parent", "prefix"])
def test_g03_rejects_every_known_outside_change(successful_run, tmp_path, change):
    policy, run = successful_run
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    extra = ()
    if change == "extra":
        extra = (outside,)
    elif change == "parent":
        run = replace(run, workspace_after={**run.workspace_after, "../outside.txt": "new"})
    elif change == "prefix":
        sibling = tmp_path / "workspace-sibling"
        sibling.mkdir()
        extra = (sibling / "file.txt",)
    else:
        before, after = dict(run.workspace_before), dict(run.workspace_after)
        if change != "created":
            before[str(outside)] = "old"
        if change != "deleted":
            after[str(outside)] = "new"
        run = replace(run, workspace_before=before, workspace_after=after)
    assert_only_refusal(gate.evaluate(run, policy, extra_paths=iter(extra)), "G03")


@pytest.mark.parametrize("target", ["file", "directory", "dangling"])
def test_g04_detects_new_escaping_links_omitted_by_runner(successful_run, tmp_path, target):
    policy, run = successful_run
    outside = tmp_path / "outside"
    if target == "file":
        outside.write_text("outside")
    elif target == "directory":
        outside.mkdir()
    (Path(policy.workspace) / "escape").symlink_to(
        outside, target_is_directory=target == "directory"
    )
    assert_only_refusal(gate.evaluate(run, policy), "G04")


def test_g05_rejects_truncated_output(successful_run):
    policy, run = successful_run
    verdict = gate.evaluate(replace(run, truncated=True), policy)
    assert_only_refusal(verdict, "G05")
    assert verdict.reasons[0]["detail"] == "incomplete_result:output_truncated"


@pytest.mark.parametrize(
    "indicator",
    [
        "policy",
        "network",
        "Network is unreachable",
        "ConnectionRefusedError: blocked",
        "socket.create_connection failed",
        "curl: connection refused",
        "https://example.invalid/",
        "Temporary failure in name resolution",
    ],
)
def test_g06_rejects_network_policy_or_stderr_evidence(successful_run, indicator):
    policy, run = successful_run
    if indicator == "policy":
        run = replace(run, policy_snapshot={**run.policy_snapshot, "network": True})
    else:
        run = replace(run, stderr=indicator)
    assert_only_refusal(gate.evaluate(run, policy), "G06")


@pytest.mark.parametrize(
    "name",
    [
        "cache.pyc",
        "cache.PYO",
        "build.aux",
        "build.fdb_latexmk",
        "build.synctex.gz",
        ".venv/config.txt",
        "pkg/__pycache__/cache.txt",
        ".git/config",
    ],
)
def test_g07_rejects_binary_build_debris_and_service_paths(successful_run, name):
    policy, run = successful_run
    path = Path(policy.workspace) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("forbidden artifact")
    run = replace(run, workspace_after={**run.workspace_after, name: "new"})
    assert_only_refusal(gate.evaluate(run, policy), "G07")


@pytest.mark.parametrize("change", ["deleted", "extra"])
def test_g07_covers_deletions_and_announced_paths(successful_run, change):
    policy, run = successful_run
    if change == "deleted":
        run = replace(run, workspace_before={"cache.pyc": "old"})
        extra = ()
    else:
        extra = (".git/announced.txt",)
    assert_only_refusal(gate.evaluate(run, policy, extra), "G07")


def test_one_failed_rule_still_runs_all_seven(successful_run):
    policy, run = successful_run
    verdict = gate.evaluate(replace(run, rc=4), policy)
    assert [check["code"] for check in verdict.checks] == [f"G0{i}" for i in range(1, 8)]
    assert_only_refusal(verdict, "G01")


@pytest.mark.parametrize("index", range(7))
def test_internal_error_is_fail_closed_for_each_rule(successful_run, monkeypatch, index):
    policy, run = successful_run
    visited = []

    def broken(*args):
        visited.append(index)
        raise RuntimeError("check implementation failed")

    rules = list(gate.RULES)
    code = rules[index].code
    rules[index] = replace(rules[index], function=broken)
    monkeypatch.setattr(gate, "RULES", tuple(rules))
    verdict = gate.evaluate(run, policy)
    assert_only_refusal(verdict, code)
    assert verdict.reasons == [{"code": code, "detail": "internal_error"}]
    assert visited == [index]


def test_bad_extra_paths_cannot_bypass_checks(successful_run):
    policy, run = successful_run

    def broken_paths():
        yield "result.txt"
        raise ValueError("cannot collect paths")

    verdict = gate.evaluate(run, policy, broken_paths())
    assert not verdict.accepted
    assert len(verdict.checks) == len(verdict.reasons) == 7
    assert all(reason["detail"] == "internal_error" for reason in verdict.reasons)


def test_multiple_refusals_are_all_reported(successful_run):
    policy, run = successful_run
    run = replace(run, rc=7, truncated=True, stderr="Network is unreachable")
    verdict = gate.evaluate(run, policy)
    assert [reason["code"] for reason in verdict.reasons] == ["G01", "G05", "G06"]


def test_inside_symlinks_and_unmodified_outside_entries_are_allowed(successful_run, tmp_path):
    policy, run = successful_run
    workspace = Path(policy.workspace)
    (workspace / "alias").symlink_to("result.txt")
    outside = str(tmp_path / "outside.txt")
    run = replace(
        run,
        workspace_before={outside: "same"},
        workspace_after={**run.workspace_after, outside: "same"},
    )
    assert gate.evaluate(run, policy).accepted


def test_extra_path_cannot_fabricate_artifact(successful_run):
    policy, run = successful_run
    run = replace(run, workspace_after={})
    assert_only_refusal(gate.evaluate(run, policy, ("result.txt",)), "G02")


def test_g03_rejects_file_beneath_escaping_directory_link(successful_run, tmp_path):
    policy, run = successful_run
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside")
    (Path(policy.workspace) / "alias").symlink_to(outside, target_is_directory=True)
    # An existing link is not new; the announced write still escapes via its parent.
    run = replace(run, workspace_before={"alias": "existing-link"})
    assert_only_refusal(gate.evaluate(run, policy, ("alias/file.txt",)), "G03")
