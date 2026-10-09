"""Exercise a real sandboxed scan and an obligatory outside-write rejection."""

import argparse
import json
import sys
import tempfile
import uuid
from contextlib import redirect_stderr
from pathlib import Path

from .loop import TaskOutcome, run_task
from .sandbox.policy import DEFAULT_READ_ONLY_ROOTS, SandboxPolicy

_SCAN_SCRIPT = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_mcp_gate.tools.scan_asset_usage import scan_asset_usage
result = scan_asset_usage(sys.argv[2], "level.json")
Path(sys.argv[3]).write_text(json.dumps(result, indent=2) + "\\n", encoding="utf-8")
"""

_OUTSIDE_SCRIPT = """
import sys
from pathlib import Path
Path(sys.argv[1]).write_text('{"attempt":"outside_write"}\\n', encoding="utf-8")
Path(sys.argv[2]).write_text("escape", encoding="utf-8")
"""


def _fixture(directory: Path) -> None:
    assets = (
        ("map", "/Game/Map", ["mesh"]),
        ("mesh", "/Game/Mesh", []),
        ("unused", "/Game/Unused", []),
    )
    for asset, package, references in assets:
        (directory / f"{asset}.json").write_text(
            json.dumps(
                {"asset": asset, "package": package, "references": references, "hard": True}
            ),
            encoding="utf-8",
        )
    (directory / "level.json").write_text('[{"asset":"map"}]', encoding="utf-8")


def _summary(name: str, outcome: TaskOutcome, report: Path) -> None:
    print(
        json.dumps(
            {
                "run": name,
                "verdict": "accepted" if outcome.accepted else "rejected",
                "accepted": outcome.accepted,
                "reasons": outcome.verdict.reasons,
                "report": str(report),
            }
        ),
        flush=True,
    )


def run_demo(workspace: Path) -> tuple[TaskOutcome, TaskOutcome]:
    """Keep reports and host-side JSON Lines logs; create a fresh fixture per call."""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    events_dir = Path(tempfile.mkdtemp(prefix="agent-mcp-gate-events-"))
    print(json.dumps({"workspace": str(workspace), "events_dir": str(events_dir)}), flush=True)
    package_root = Path(__file__).resolve().parent.parent
    python = str(Path(sys.executable).resolve())
    roots = list(DEFAULT_READ_ONLY_ROOTS)
    for root in (package_root, Path(sys.base_prefix).resolve()):
        if not any(root.is_relative_to(Path(bound)) for bound in roots):
            roots.append(str(root))
    token = uuid.uuid4().hex
    report = workspace / f"asset-usage-{token}.json"
    rejected_report = workspace / f"outside-attempt-{token}.json"
    outside = Path("/usr") / f"agent-mcp-gate-outside-{token}.txt"
    with tempfile.TemporaryDirectory(prefix="agent-mcp-gate-fixture-") as directory:
        fixture = Path(directory)
        _fixture(fixture)
        policy = SandboxPolicy(
            workspace=workspace,
            read_only_roots=(*roots, str(fixture)),
            env_allowlist=(),
        )
        # The loop emits identical JSON Lines to its host log and this CLI's stdout.
        with redirect_stderr(sys.stdout):
            accepted = run_task(
                policy,
                [python, "-I", "-B", "-c", _SCAN_SCRIPT, str(package_root), directory, str(report)],
                artifact_glob=report.name,
                events_path=events_dir / "scan-events.json",
            )
        _summary("scan_asset_usage", accepted, report)
        with redirect_stderr(sys.stdout):
            rejected = run_task(
                policy,
                [python, "-I", "-B", "-c", _OUTSIDE_SCRIPT, str(rejected_report), str(outside)],
                artifact_glob=rejected_report.name,
                events_path=events_dir / "outside-events.json",
            )
        _summary("outside_write", rejected, rejected_report)
    return accepted, rejected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="retain reports in this directory")
    args = parser.parse_args(argv)
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="agent-mcp-gate-workspace-"))
    accepted, rejected = run_demo(workspace)
    checks = {check["code"]: check["ok"] for check in rejected.verdict.checks}
    # G02 proves the negative worker ran; setup failure alone cannot pass the demo.
    return (
        0
        if accepted.accepted and not rejected.accepted and not checks["G01"] and checks["G02"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
