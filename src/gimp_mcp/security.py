from __future__ import annotations

from pathlib import Path

from .errors import GimpMcpError


class PathPolicy:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [p.expanduser().resolve() for p in roots]
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)

    def resolve(self, value: str, *, must_exist: bool = False) -> Path:
        raw = Path(value).expanduser()
        candidate = raw.resolve(strict=False)
        if not any(candidate == root or root in candidate.parents for root in self.roots):
            raise GimpMcpError("INVALID_PATH", f"Path is outside allowed roots: {candidate}", False)
        if must_exist and not candidate.exists():
            raise GimpMcpError("INVALID_PATH", f"Path does not exist: {candidate}")
        # Existing symlinks are resolved above; parent symlink traversal therefore cannot escape roots.
        return candidate

    def ensure_parent(self, value: str) -> Path:
        path = self.resolve(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
