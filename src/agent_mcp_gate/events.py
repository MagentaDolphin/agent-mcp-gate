"""Write structured lifecycle events as JSON Lines, never to stdout."""

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EVENTS = frozenset(
    {
        "run_started",
        "run_finished",
        "gate_started",
        "gate_check",
        "gate_result",
        "accepted",
        "rejected",
        "artifact_written",
    }
)


@dataclass(frozen=True)
class EventEmitter:
    events_path: str | Path | None = None
    stderr: bool = True

    def emit(self, event: str, **fields) -> dict:
        if event not in EVENTS or "ts" in fields:
            raise ValueError("invalid event or reserved timestamp field")
        record = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        if self.events_path is not None:
            path = Path(self.events_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
        if self.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
        return record


def emit(
    event: str, *, events_path: str | Path | None = None, stderr: bool = True, **fields
) -> dict:
    """Use a caller-selected file, stderr, or both; JSON serialization errors propagate."""
    return EventEmitter(events_path, stderr).emit(event, **fields)
