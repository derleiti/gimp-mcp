from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .errors import GimpMcpError


@dataclass(slots=True)
class ArtworkSession:
    session_id: str
    workdir: Path
    document: Path
    source: Path | None = None
    revision: int = 0
    undo_stack: list[Path] = field(default_factory=list)
    redo_stack: list[Path] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)


class SessionManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, ArtworkSession] = {}
        self._lock = threading.RLock()
        self._recover_existing()

    def _meta_path(self, s: ArtworkSession) -> Path:
        return s.workdir / "session.json"

    def _persist(self, s: ArtworkSession) -> None:
        data = {"session_id": s.session_id, "source": str(s.source) if s.source else None, "revision": s.revision}
        path = self._meta_path(s)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _recover_existing(self) -> None:
        for workdir in self.root.iterdir():
            if not workdir.is_dir():
                continue
            document = workdir / "document.xcf"
            if not document.exists():
                continue
            source = None; revision = 0
            meta = workdir / "session.json"
            if meta.exists():
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    source = Path(data["source"]) if data.get("source") else None
                    revision = int(data.get("revision") or 0)
                except Exception:
                    pass
            undo = sorted(workdir.glob("undo-*.xcf"))
            redo = sorted(workdir.glob("redo-*.xcf"))
            s = ArtworkSession(workdir.name, workdir, document, source, revision, undo, redo)
            self._sessions[s.session_id] = s

    def create(self, source: Path | None = None) -> ArtworkSession:
        sid = uuid.uuid4().hex
        workdir = self.root / sid
        workdir.mkdir(parents=True, exist_ok=False)
        document = workdir / "document.xcf"
        s = ArtworkSession(sid, workdir, document, source)
        with self._lock:
            self._sessions[sid] = s
        self._persist(s)
        return s

    def _recover_one(self, session_id: str) -> ArtworkSession | None:
        workdir = self.root / str(session_id)
        document = workdir / "document.xcf"
        if not workdir.is_dir() or not document.is_file():
            return None
        source = None
        revision = 0
        meta = workdir / "session.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                source = Path(data["source"]) if data.get("source") else None
                revision = int(data.get("revision") or 0)
            except Exception:
                pass
        recovered = ArtworkSession(
            str(session_id), workdir, document, source, revision,
            sorted(workdir.glob("undo-*.xcf")), sorted(workdir.glob("redo-*.xcf")),
        )
        with self._lock:
            return self._sessions.setdefault(str(session_id), recovered)

    def get(self, session_id: str) -> ArtworkSession:
        session_id = str(session_id).strip()
        with self._lock:
            s = self._sessions.get(session_id)
        if s is None:
            s = self._recover_one(session_id)
        if not s:
            raise GimpMcpError("SESSION_NOT_FOUND", f"Unknown session: {session_id}")
        if not s.document.is_file():
            with self._lock:
                self._sessions.pop(session_id, None)
            raise GimpMcpError("SESSION_NOT_FOUND", f"Session document is no longer available: {session_id}")
        return s

    def snapshot(self, s: ArtworkSession) -> Path | None:
        if not s.document.exists():
            return None
        s.revision += 1
        p = s.workdir / f"undo-{s.revision:06d}.xcf"
        shutil.copy2(s.document, p)
        s.undo_stack.append(p)
        for old in s.redo_stack:
            old.unlink(missing_ok=True)
        s.redo_stack.clear()
        self._persist(s)
        return p

    def rollback_failed(self, s: ArtworkSession, snap: Path | None) -> None:
        if snap and snap.exists():
            shutil.copy2(snap, s.document)
            if s.undo_stack and s.undo_stack[-1] == snap:
                s.undo_stack.pop()
            snap.unlink(missing_ok=True)
            self._persist(s)

    def undo(self, s: ArtworkSession) -> bool:
        if not s.undo_stack or not s.document.exists():
            return False
        s.revision += 1
        redo = s.workdir / f"redo-{s.revision:06d}.xcf"
        shutil.copy2(s.document, redo)
        previous = s.undo_stack.pop()
        shutil.copy2(previous, s.document)
        previous.unlink(missing_ok=True)
        s.redo_stack.append(redo)
        self._persist(s)
        return True

    def redo(self, s: ArtworkSession) -> bool:
        if not s.redo_stack or not s.document.exists():
            return False
        s.revision += 1
        undo = s.workdir / f"undo-{s.revision:06d}.xcf"
        shutil.copy2(s.document, undo)
        nxt = s.redo_stack.pop()
        shutil.copy2(nxt, s.document)
        nxt.unlink(missing_ok=True)
        s.undo_stack.append(undo)
        self._persist(s)
        return True


    def list(self) -> list[dict[str, object]]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [{
            "session_id": s.session_id,
            "document": str(s.document),
            "source": str(s.source) if s.source else None,
            "revision": s.revision,
            "undo_depth": len(s.undo_stack),
            "redo_depth": len(s.redo_stack),
            "preview": str(s.workdir / "preview.png") if (s.workdir / "preview.png").exists() else None,
        } for s in sessions]

    def close(self, session_id: str, *, discard: bool = False) -> None:
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if not s:
            raise GimpMcpError("SESSION_NOT_FOUND", f"Unknown session: {session_id}")
        if discard:
            shutil.rmtree(s.workdir, ignore_errors=True)
