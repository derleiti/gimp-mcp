from __future__ import annotations

import json
import os
import socket
import stat
from pathlib import Path
from typing import Any

from .headless import HeadlessGimpPool


class LiveBridge:
    def __init__(self, socket_path: Path | None = None, timeout: float = 2.0, auto_headless: bool = True) -> None:
        self.socket_path = socket_path or Path(os.environ.get(
            'GIMP_MCP_LIVE_SOCKET', str(Path.home() / '.local/state/gimp-mcp/gimp-live.sock')
        ))
        self.timeout = timeout
        self.auto_headless = auto_headless
        self.headless = HeadlessGimpPool() if auto_headless and socket_path is None else None
        self._active_socket = self.socket_path

    def _select_socket(self, key: str | None = None) -> Path:
        if self.headless is None:
            self._active_socket = self.socket_path
            return self.socket_path
        if self.socket_path.exists():
            try:
                if self._request_at(self.socket_path, 'ping').get('ok'):
                    self._active_socket = self.socket_path
                    return self.socket_path
            except Exception:
                pass
        worker = self.headless.for_key(key)
        worker.ensure()
        self._active_socket = worker.socket_path
        return self._active_socket

    def _request_at(self, socket_path: Path, command: str, **payload: Any) -> dict[str, Any]:
        request = {'command': command, **payload}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(socket_path))
            sock.sendall((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
            data = b''
            while b'\n' not in data and len(data) < 1024 * 1024:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        if not data:
            raise RuntimeError('GIMP bridge returned no data')
        result = json.loads(data.split(b'\n', 1)[0].decode('utf-8'))
        result.setdefault('socket', str(socket_path))
        return result

    def request(self, command: str, **payload: Any) -> dict[str, Any]:
        return self._request_at(self._select_socket(str(payload.get('path') or '')), command, **payload)

    def available(self) -> bool:
        try:
            return bool(self.request('ping').get('ok'))
        except Exception:
            return False

    def _cleanup_stale_socket(self) -> bool:
        try:
            st = self.socket_path.lstat()
            if stat.S_ISSOCK(st.st_mode):
                self.socket_path.unlink(missing_ok=True)
                return True
        except OSError:
            pass
        return False

    def status(self) -> dict[str, Any]:
        try:
            result = self.request('ping')
            result['workspace_mode'] = 'headless' if result.get('headless') else 'visible'
            return result
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            cleaned = self._cleanup_stale_socket() if isinstance(exc, ConnectionRefusedError) else False
            return {'ok': False, 'mode': 'batch', 'error': str(exc), 'socket': str(self.socket_path), 'stale_socket_removed': cleaned}
        except Exception as exc:
            return {'ok': False, 'mode': 'batch', 'error': str(exc), 'socket': str(self.socket_path)}

    def show_document(self, path: Path) -> dict[str, Any]:
        return self.request('show_document', path=str(path))

    def layer_update(self, path: Path, layer_id: int, *, name: str | None = None, visible: bool | None = None, opacity: float | None = None) -> dict[str, Any]:
        return self.request("layer_update", path=str(path), layer_id=int(layer_id), name=name, visible=visible, opacity=opacity)

    def translate(self, path: Path, layer_id: int, dx: float, dy: float) -> dict[str, Any]:
        return self.request("translate", path=str(path), layer_id=int(layer_id), dx=float(dx), dy=float(dy))

    def undo(self, path: Path) -> dict[str, Any]:
        return self.request("undo", path=str(path))

    def vision_snapshot(self, output: Path, path: Path | None = None) -> dict[str, Any]:
        payload = {'output': str(output)}
        if path is not None: payload['path'] = str(path)
        return self.request('vision_snapshot', **payload)

    def mirror_if_available(self, path: Path) -> dict[str, Any] | None:
        if not self.available():
            return None
        try:
            return self.show_document(path)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
