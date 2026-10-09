import json
import os
import subprocess
import sys

import pytest

from agent_mcp_gate.mcp.jsonrpc import notification, request
from agent_mcp_gate.mcp.server import Server
from agent_mcp_gate.mcp.tools import ToolSpec, build_registry


def _run(messages, workspace, module="agent_mcp_gate.mcp.server"):
    payload = (
        "\n".join(item if isinstance(item, str) else json.dumps(item) for item in messages) + "\n"
    )
    completed = subprocess.run(
        [sys.executable, "-m", module],
        input=payload,
        text=True,
        capture_output=True,
        env={**os.environ, "AGENT_MCP_GATE_WORKSPACE": str(workspace)},
        timeout=10,
        check=True,
    )
    assert completed.stderr == ""
    return [json.loads(line) for line in completed.stdout.splitlines()]


def test_live_process_protocol_tools_errors_and_recovery(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "level.json").write_text('[{"asset":"a"}]')
    (fixtures / "a.json").write_text(
        json.dumps(
            {
                "asset": "a",
                "package": "/Game/a",
                "references": ["missing"],
                "hard": True,
            }
        )
    )
    (tmp_path / "target.txt").write_text("before\n")
    messages = [
        request(
            "initialize",
            1,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        ),
        notification("notifications/initialized"),
        request("tools/list", 2),
        request(
            "tools/call",
            3,
            {
                "name": "scan_asset_usage",
                "arguments": {
                    "root": str(fixtures),
                    "level": "level.json",
                },
            },
        ),
        request(
            "tools/call",
            4,
            {
                "name": "apply_scoped_patch",
                "arguments": {
                    "path": "target.txt",
                    "patch": "@@ -1 +1 @@\n-before\n+after\n",
                },
            },
        ),
        request("unknown", 5),
        request("tools/call", 6, {"name": "unknown", "arguments": {}}),
        request(
            "tools/call",
            7,
            {
                "name": "scan_asset_usage",
                "arguments": {
                    "root": 42,
                    "level": "level.json",
                },
            },
        ),
        "{broken json",
        notification("tools/call", {"name": "unknown"}),
        request("tools/list", 8),
        request(
            "tools/call",
            9,
            {
                "name": "apply_scoped_patch",
                "arguments": {
                    "path": "../outside.txt",
                    "patch": "",
                },
            },
        ),
        request(
            "tools/call",
            10,
            {
                "name": "scan_asset_usage",
                "arguments": {
                    "root": str(fixtures),
                    "level": "level.json",
                    "typo": True,
                },
            },
        ),
        request(
            "tools/call",
            11,
            {
                "name": "scan_asset_usage",
                "arguments": {
                    "root": str(fixtures),
                    "level": "level.json",
                    "include_soft": "false",
                },
            },
        ),
        request("tools/call", 12, {"name": "scan_asset_usage", "arguments": []}),
        request("tools/list", 13),
    ]
    answers = _run(messages, tmp_path)
    assert len(answers) == 14  # No replies for either notification.
    by_id = {answer["id"]: answer for answer in answers}
    initialized = by_id[1]["result"]
    assert initialized["protocolVersion"] == "2025-06-18"
    assert initialized["serverInfo"]["name"] == "agent-mcp-gate"
    assert initialized["capabilities"]["tools"] == {"listChanged": False}
    listed = {spec["name"]: spec for spec in by_id[2]["result"]["tools"]}
    for name, spec in build_registry().items():
        assert listed[name]["inputSchema"] == spec.input_schema
        assert listed[name]["annotations"]["readOnlyHint"] is spec.read_only
    scanned = by_id[3]["result"]
    assert scanned["structuredContent"]["counts"]["missing"] == 1
    assert json.loads(scanned["content"][0]["text"]) == scanned["structuredContent"]
    assert scanned["isError"] is False
    assert by_id[4]["result"]["structuredContent"] == {
        "applied": True,
        "path": "target.txt",
        "hunks_applied": 1,
    }
    assert (tmp_path / "target.txt").read_text() == "after\n"
    assert by_id[5]["error"]["code"] == -32601
    assert by_id[6]["error"]["code"] == -32602
    assert by_id[6]["error"]["data"]["available"] == sorted(build_registry())
    assert by_id[7]["error"]["code"] == -32602
    assert by_id[7]["error"]["data"]["path"] == "root"
    assert by_id[None]["error"]["code"] == -32700
    assert by_id[8]["result"] == by_id[2]["result"]
    assert by_id[9]["result"]["isError"] is True
    assert by_id[9]["result"]["structuredContent"]["reason"] == "out_of_scope"
    for request_id, path in [(10, "typo"), (11, "include_soft"), (12, "arguments")]:
        assert by_id[request_id]["error"]["code"] == -32602
        assert by_id[request_id]["error"]["data"]["path"] == path
    assert by_id[13]["result"] == by_id[2]["result"]


def test_live_process_internal_and_invalid_request_errors_do_not_end_loop(tmp_path):
    (tmp_path / "level.json").write_text('[{"asset":"bad"}]')
    (tmp_path / "bad.json").write_text("{malformed")
    answers = _run(
        [
            request(
                "tools/call",
                1,
                {
                    "name": "scan_asset_usage",
                    "arguments": {
                        "root": str(tmp_path),
                        "level": "level.json",
                    },
                },
            ),
            {"jsonrpc": "2.0", "id": 2, "method": 1},
            request("tools/list", 3),
        ],
        tmp_path,
    )
    assert answers[0]["error"] == {"code": -32603, "message": "Internal error"}
    assert answers[1]["error"]["code"] == -32600
    assert answers[1]["id"] == 2
    assert len(answers[2]["result"]["tools"]) == 2


@pytest.mark.parametrize(
    "module",
    [
        "agent_mcp_gate",
        "agent_mcp_gate.mcp",
        "agent_mcp_gate.mcp.server",
    ],
)
def test_entrypoints_shutdown_exit_and_no_response_to_notifications(tmp_path, module):
    answers = _run(
        [
            notification("unknown"),
            request("tools/list", 0),
            request("shutdown", 1),
            request("tools/list", 2),
            notification("exit"),
            request("tools/list", 3),
        ],
        tmp_path,
        module,
    )
    assert [answer["id"] for answer in answers] == [0, 1, 2]
    assert answers[1]["result"] is None
    assert answers[2]["error"]["code"] == -32600


def test_unexpected_handler_exception_is_internal_error_and_loop_survives():
    def broken():
        raise RuntimeError("private failure details")

    server = Server({"broken": ToolSpec("broken", "test", {"type": "object"}, broken, True)})
    result = server.handle_line(json.dumps(request("tools/call", 1, {"name": "broken"})))
    assert result["error"] == {"code": -32603, "message": "Internal error"}
    assert server.handle_line(json.dumps(request("tools/list", 2)))["id"] == 2
