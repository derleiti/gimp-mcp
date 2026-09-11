import json
import socket
import threading
from pathlib import Path

from gimp_mcp.live_bridge import LiveBridge


def test_live_bridge_offline(tmp_path: Path):
    status = LiveBridge(tmp_path / 'missing.sock', timeout=0.05).status()
    assert status['ok'] is False
    assert status['mode'] == 'batch'


def test_live_bridge_ping(tmp_path: Path):
    path = tmp_path / 'live.sock'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path)); server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall((json.dumps({'ok': True, 'mode': 'persistent', 'gimp_version': '3.test'})+'\n').encode())
        server.close()

    thread = threading.Thread(target=serve, daemon=True); thread.start()
    status = LiveBridge(path, timeout=1).status()
    thread.join(timeout=1)
    assert status['ok'] is True
    assert status['mode'] == 'persistent'
