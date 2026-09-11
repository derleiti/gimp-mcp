from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULTS = {
    "ai_provider": "triforce",
    "ai_model": "",
    "auto_preview": True,
    "preview_interval_ms": 750,
    "preview_max": 1200,
    "open_gimp_after_create": False,
}

class ControlSettings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        data = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict): data.update(raw)
        except Exception: pass
        return data

    def save(self, data: dict[str, Any]) -> None:
        merged = dict(DEFAULTS); merged.update(data)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
