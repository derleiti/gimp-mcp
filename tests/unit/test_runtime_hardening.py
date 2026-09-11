from pathlib import Path

from gimp_mcp.live_bridge import LiveBridge
from gimp_mcp.sessions import SessionManager


def test_stale_live_socket_is_not_available(tmp_path: Path):
    path = tmp_path / "dead.sock"
    path.touch()
    assert LiveBridge(path, timeout=0.02).available() is False


def test_new_snapshot_removes_stale_redo_files(tmp_path: Path):
    sessions = SessionManager(tmp_path / "sessions")
    s = sessions.create()
    s.document.write_bytes(b"v1")
    sessions.snapshot(s)
    s.document.write_bytes(b"v2")
    assert sessions.undo(s) is True
    assert s.redo_stack
    redo_path = s.redo_stack[-1]
    assert redo_path.exists()
    sessions.snapshot(s)
    assert s.redo_stack == []
    assert not redo_path.exists()


def test_session_manager_lazy_recovers_cross_process_session(tmp_path):
    from gimp_mcp.sessions import SessionManager
    first=SessionManager(tmp_path)
    created=first.create()
    created.document.write_bytes(b"xcf")
    second=SessionManager(tmp_path)
    second._sessions.clear()
    recovered=second.get(created.session_id)
    assert recovered.session_id == created.session_id
    assert recovered.document.read_bytes() == b"xcf"
