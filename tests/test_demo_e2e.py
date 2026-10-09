import json
import subprocess
import sys
from pathlib import Path

from agent_mcp_gate import demo


def _run_demo(workspace=None):
    argv = [sys.executable, "-m", "agent_mcp_gate.demo"]
    if workspace is not None:
        argv.extend(["--workspace", str(workspace)])
    process = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    assert process.stderr == ""
    records = [json.loads(line) for line in process.stdout.splitlines()]
    assert records and "workspace" in records[0]
    events = [record for record in records if "event" in record]
    summaries = [record for record in records if "verdict" in record]
    assert [record["run"] for record in summaries] == ["scan_asset_usage", "outside_write"]
    return records[0], events, summaries


def test_demo_e2e_accepts_scan_and_rejects_outside_write(tmp_path):
    workspace = tmp_path / "workspace"
    info, events, summaries = _run_demo(workspace)
    assert info["workspace"] == str(workspace)
    assert [summary["accepted"] for summary in summaries] == [True, False]
    assert [summary["verdict"] for summary in summaries] == ["accepted", "rejected"]
    assert summaries[0]["reasons"] == []
    assert [reason["code"] for reason in summaries[1]["reasons"]] == ["G01"]
    report = json.loads(Path(summaries[0]["report"]).read_text())
    assert report == {
        "assets": [
            {"id": "map", "package": "/Game/Map", "verdict": "used", "depth": 0, "path": ["map"]},
            {
                "id": "mesh",
                "package": "/Game/Mesh",
                "verdict": "used",
                "depth": 1,
                "path": ["map", "mesh"],
            },
            {
                "id": "unused",
                "package": "/Game/Unused",
                "verdict": "unused",
                "depth": None,
                "path": [],
            },
        ],
        "counts": {"used": 2, "unused": 1, "missing": 0, "ambiguous": 0},
    }
    logs = []
    for name, decision in (("scan-events.json", "accepted"), ("outside-events.json", "rejected")):
        records = [
            json.loads(line)
            for line in (Path(info["events_dir"]) / name).read_text(encoding="utf-8").splitlines()
        ]
        assert [record["event"] for record in records] == [
            "run_started",
            "run_finished",
            "gate_started",
            "artifact_written",
            *["gate_check"] * 7,
            "gate_result",
            decision,
        ]
        assert all("ts" in record for record in records)
        assert [record["code"] for record in records if record["event"] == "gate_check"] == [
            f"G0{index}" for index in range(1, 8)
        ]
        logs.extend(records)
    assert logs == events
    outside = Path(events[13]["argv"][-1])
    assert outside.parent == Path("/usr")
    assert not outside.exists()
    assert events[14]["rc"] != 0
    assert Path(summaries[1]["report"]).is_file()


def test_demo_e2e_repeated_workspace_keeps_fresh_reports(tmp_path):
    workspace = tmp_path / "repeat"
    _, _, first = _run_demo(workspace)
    saved = {summary["report"]: Path(summary["report"]).read_bytes() for summary in first}
    _, _, second = _run_demo(workspace)
    assert [summary["accepted"] for summary in second] == [True, False]
    assert not set(saved).intersection(summary["report"] for summary in second)
    assert all(Path(path).read_bytes() == content for path, content in saved.items())


def test_demo_e2e_default_workspace():
    info, _, summaries = _run_demo()
    assert Path(info["workspace"]).is_dir()
    assert Path(summaries[0]["report"]).parent == Path(info["workspace"])


def test_demo_missing_sandbox_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    assert demo.main(["--workspace", str(tmp_path / "workspace")]) == 1
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summaries = [record for record in records if "verdict" in record]
    assert [summary["accepted"] for summary in summaries] == [False, False]
    assert all(not Path(summary["report"]).exists() for summary in summaries)
