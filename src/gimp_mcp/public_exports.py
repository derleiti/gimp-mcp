from __future__ import annotations
import hashlib, hmac, os, secrets, time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .errors import GimpMcpError
from .export_service import ExportService
from .sessions import ArtworkSession

FORMATS=("xcf","jpg","png")

class PublicExportManager:
    """Materialize final artwork formats and create opaque, expiring download capabilities."""
    def __init__(self, root: Path, exports: ExportService, *, base_url: str, ttl: int = 86400) -> None:
        self.root=root.resolve(); self.exports=exports; self.base_url=base_url.rstrip('/'); self.ttl=max(60,int(ttl))
        self.root.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def publish(self, session: ArtworkSession, *, ttl: int | None = None) -> dict[str,Any]:
        token=secrets.token_urlsafe(32); token_hash=self._token_hash(token); directory=self.root/token_hash
        directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        expires=int(time.time())+max(60,int(ttl or self.ttl)); links={}
        try:
            for fmt in FORMATS:
                filename=f"artwork.{fmt}"; result=self.exports.export(session,directory/filename,fmt,overwrite=True)
                links[fmt]={"url":f"{self.base_url}/{quote(token)}/{filename}","format":fmt,"filename":filename,"size":Path(result['output']).stat().st_size}
            (directory/'expires').write_text(str(expires)); (directory/'expires').chmod(0o600)
        except Exception:
            import shutil; shutil.rmtree(directory,ignore_errors=True); raise
        return {"session_id":session.session_id,"expires_at":expires,"ttl_seconds":expires-int(time.time()),"links":links}

    def resolve(self, token: str, filename: str) -> Path:
        if filename not in {f'artwork.{x}' for x in FORMATS}: raise FileNotFoundError(filename)
        directory=self.root/self._token_hash(token)
        try: expires=int((directory/'expires').read_text())
        except Exception: raise FileNotFoundError(filename)
        if expires < int(time.time()): raise FileNotFoundError(filename)
        path=directory/filename
        if not path.is_file(): raise FileNotFoundError(filename)
        return path

    def cleanup(self) -> int:
        import shutil
        now=int(time.time()); count=0
        for d in self.root.iterdir():
            if not d.is_dir(): continue
            try: expired=int((d/'expires').read_text()) < now
            except Exception: expired=True
            if expired: shutil.rmtree(d,ignore_errors=True); count+=1
        return count
