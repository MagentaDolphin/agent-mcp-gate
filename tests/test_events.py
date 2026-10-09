import json
from datetime import datetime

import pytest

from agent_mcp_gate.events import EventEmitter, emit


@pytest.mark.parametrize(
    "file_enabled,stderr_enabled", [(True, False), (False, True), (True, True)]
)
def test_emit_selected_destinations_are_json_lines(tmp_path, capsys, file_enabled, stderr_enabled):
    path = tmp_path / "nested" / "events.jsonl" if file_enabled else None
    record = emit(
        "gate_result",
        events_path=path,
        stderr=stderr_enabled,
        accepted=False,
        reasons=[{"code": "G01", "detail": "execution_failed"}],
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert datetime.fromisoformat(record["ts"]).utcoffset().total_seconds() == 0
    if path is not None:
        assert json.loads(path.read_text()) == record
    assert captured.err == (json.dumps(record, ensure_ascii=False) + "\n" if stderr_enabled else "")


def test_emitter_appends_and_escapes_field_newlines(tmp_path):
    path = tmp_path / "events.jsonl"
    emitter = EventEmitter(path, stderr=False)
    emitter.emit("run_started", argv=["first\nsecond"])
    emitter.emit("run_finished", rc=0)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["event"] for record in records] == ["run_started", "run_finished"]
    assert records[0]["argv"] == ["first\nsecond"]


@pytest.mark.parametrize("fields", [{"ts": "forged"}, {"value": float("nan")}, {"value": object()}])
def test_invalid_event_fields_never_write_partial_json(tmp_path, capsys, fields):
    path = tmp_path / "events.jsonl"
    with pytest.raises((ValueError, TypeError)):
        emit("gate_result", events_path=path, **fields)
    assert not path.exists()
    assert capsys.readouterr().err == ""
