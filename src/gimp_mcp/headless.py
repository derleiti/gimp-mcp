from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any


class HeadlessGimpManager:
    """Start/reuse a private persistent GIMP 3 worker for MCP automation."""
    def __init__(self, executable: str = "/usr/bin/gimp", state_dir: Path | None = None, startup_timeout: float = 15.0, worker_id: int = 0) -> None:
        self.executable = executable
        self.state_dir = state_dir or Path(os.environ.get("GIMP_MCP_STATE_DIR", str(Path.home() / ".local/state/gimp-mcp")))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if worker_id == 0 else f"-{worker_id}"
        self.worker_id = worker_id
        self.socket_path = self.state_dir / f"gimp-headless{suffix}.sock"
        self.pid_path = self.state_dir / f"gimp-headless{suffix}.pid"
        self.log_path = self.state_dir / f"gimp-headless{suffix}.log"
        self.lock_path = self.state_dir / f"gimp-headless{suffix}-start.lock"
        self.startup_timeout = startup_timeout

    def _ping(self) -> dict[str, Any] | None:
        if not self.socket_path.exists(): return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0); sock.connect(str(self.socket_path)); sock.sendall(b'{"command":"ping"}\n'); data=sock.recv(65536)
            result=json.loads(data.split(b"\n",1)[0].decode("utf-8")); return result if result.get("ok") else None
        except Exception: return None

    def ensure(self) -> dict[str, Any]:
        current=self._ping()
        if current: return {**current,"managed":True,"started":False,"worker_id":self.worker_id}
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX); current=self._ping()
            if current: return {**current,"managed":True,"started":False,"worker_id":self.worker_id}
            self.socket_path.unlink(missing_ok=True)
            env=os.environ.copy(); env.pop("VIRTUAL_ENV",None); env.pop("PYTHONHOME",None); env.pop("PYTHONPATH",None)
            env.update(GIMP_MCP_LIVE_SOCKET=str(self.socket_path),GIMP_MCP_HEADLESS="1",GIMP_MCP_BATCH="0")
            code=("from gi.repository import Gimp;" "p=Gimp.get_pdb().lookup_procedure('plug-in-gimp-mcp-live');" "assert p is not None, 'GIMP MCP persistent plug-in is not installed';" "c=p.create_config();p.run(c)")
            log=self.log_path.open("ab",buffering=0)
            proc=subprocess.Popen([self.executable,"-n","-i","-c","--batch-interpreter=python-fu-eval","-b",code],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env,close_fds=True)
            self.pid_path.write_text(str(proc.pid),encoding="ascii"); deadline=time.monotonic()+self.startup_timeout
            while time.monotonic()<deadline:
                if proc.poll() is not None: raise RuntimeError(f"headless GIMP worker {self.worker_id} exited ({proc.returncode}); see {self.log_path}")
                current=self._ping()
                if current: return {**current,"managed":True,"started":True,"pid":proc.pid,"worker_id":self.worker_id}
                time.sleep(.1)
            try: os.killpg(proc.pid,signal.SIGTERM)
            except OSError: pass
            raise RuntimeError(f"headless GIMP worker {self.worker_id} did not become ready; see {self.log_path}")

    def status(self) -> dict[str, Any]:
        current=self._ping(); return {"ok":bool(current),"mode":"headless" if current else "offline","socket":str(self.socket_path),"worker_id":self.worker_id,**(current or {})}


class HeadlessGimpPool:
    """Stable session-to-worker routing permits parallel work on different XCFs."""
    def __init__(self, workers: int | None = None, state_dir: Path | None = None) -> None:
        count=workers if workers is not None else int(os.environ.get("GIMP_MCP_HEADLESS_WORKERS","2"))
        self.workers=[HeadlessGimpManager(state_dir=state_dir,worker_id=i) for i in range(max(1,min(count,8)))]

    def for_key(self, key: str | None) -> HeadlessGimpManager:
        if not key: return self.workers[0]
        digest=hashlib.blake2b(key.encode("utf-8"),digest_size=4).digest()
        return self.workers[int.from_bytes(digest,"big") % len(self.workers)]

    def status(self) -> dict[str, Any]:
        rows=[w.status() for w in self.workers]
        return {"ok":any(r["ok"] for r in rows),"workers":rows,"configured_workers":len(rows)}
