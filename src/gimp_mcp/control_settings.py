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
    "open_gimp_after_create": True,
    "first_run_complete": False,
    "check_updates_on_start": True,
    "gimp_runtime": "system",
    "managed_gimp_path": "",
    "mcp_bind_host": "127.0.0.1",
    "mcp_port": 8000,
    "mcp_network_enabled": False,
    "mcp_network_host": "0.0.0.0",
    "mcp_require_auth": True,
    "mcp_auth_token": "",
    "creative_budget": "effectively_unlimited",
    "max_steps_per_batch": 20,
    "provider_planning_timeout": 600,
    "provider_review_timeout": 300,
    "gimp_operation_timeout": 90,
    "preview_render_timeout": 120,
    "export_timeout": 180,
    "vision_timeout": 120,
    "idle_watchdog_timeout": 300,
    "preview_cadence_mutations": 4,
    "vision_review_every_batches": 2,
}

def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try: return max(low, min(int(value), high))
    except (TypeError, ValueError): return default

def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    out=dict(DEFAULTS); out.update(data if isinstance(data,dict) else {})
    if out.get("creative_budget") not in {"conservative","normal","extended","effectively_unlimited"}: out["creative_budget"]="effectively_unlimited"
    bounds={
        "max_steps_per_batch":(20,1,100), "provider_planning_timeout":(600,10,3600), "provider_review_timeout":(300,10,3600),
        "gimp_operation_timeout":(90,5,1800), "preview_render_timeout":(120,5,1800), "export_timeout":(180,5,3600),
        "vision_timeout":(120,5,1800), "idle_watchdog_timeout":(300,10,86400),
        "preview_cadence_mutations":(4,1,1000), "vision_review_every_batches":(2,0,1000),
    }
    for key,(default,low,high) in bounds.items(): out[key]=_clamp_int(out.get(key),default,low,high)
    return out

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
        return _normalize(data)

    def save(self, data: dict[str, Any]) -> None:
        merged = _normalize(data)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
