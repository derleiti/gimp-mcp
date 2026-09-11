from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Event:
    event_id: str
    timestamp: float
    type: str
    session_id: str | None = None
    operation: str | None = None
    status: str | None = None
    duration_ms: float | None = None
    details: dict[str, Any] | None = None


class EventBus:
    """Thread-safe in-memory event ring with an append-only JSONL audit stream."""

    def __init__(self, path: Path, max_events: int = 1000) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: deque[Event] = deque(maxlen=max_events)
        self._lock = threading.RLock()
        self._load_tail(max_events)

    def _load_tail(self, limit: int) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
            for line in lines:
                try:
                    self._events.append(Event(**json.loads(line)))
                except Exception:
                    continue
        except OSError:
            pass

    def emit(self, event_type: str, *, session_id: str | None = None,
             operation: str | None = None, status: str | None = None,
             duration_ms: float | None = None, details: dict[str, Any] | None = None) -> Event:
        event = Event(uuid.uuid4().hex, time.time(), event_type, session_id,
                      operation, status, duration_ms, details)
        row = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._events.append(event)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(row + "\n")
        return event

    def recent(self, limit: int = 100, *, session_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = list(self._events)
        if session_id:
            rows = [x for x in rows if x.session_id == session_id]
        return [asdict(x) for x in rows[-limit:]]
