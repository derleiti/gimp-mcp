from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _roots() -> list[Path]:
    home = Path.home()
    raw = os.getenv("GIMP_MCP_ALLOWED_ROOTS")
    if raw:
        return [Path(x).expanduser().resolve() for x in raw.split(os.pathsep) if x]
    return [home / "Pictures", home / "Downloads", home / "gimp-mcp" / "workspace", Path("/tmp/gimp-mcp")]


@dataclass(slots=True)
class Settings:
    gimp_executable: str = os.getenv("GIMP_MCP_GIMP", "/usr/bin/gimp")
    timeout: int = int(os.getenv("GIMP_MCP_TIMEOUT", "45"))
    preview_max: int = int(os.getenv("GIMP_MCP_PREVIEW_MAX", "1600"))
    expert_mode: bool = os.getenv("GIMP_MCP_EXPERT", "0").lower() in {"1", "true", "yes"}
    allowed_roots: list[Path] = field(default_factory=_roots)
    session_root: Path = Path(os.getenv("GIMP_MCP_SESSION_ROOT", "/tmp/gimp-mcp/sessions"))
    state_dir: Path = Path(os.getenv("GIMP_MCP_STATE_DIR", str(Path.home() / ".local/state/gimp-mcp")))
