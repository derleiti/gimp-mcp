from pathlib import Path

from gimp_mcp.setup_manager import SetupManager


def test_setup_manager_self_test_shape(tmp_path: Path, monkeypatch):
    manager = SetupManager(tmp_path)
    monkeypatch.setattr("gimp_mcp.setup_manager.shutil.which", lambda name: None if name == "uv" else f"/usr/bin/{name}")
    assert manager.self_test() == {"ok": False, "error": "uv fehlt"}


def test_update_check_shape(monkeypatch, tmp_path: Path):
    manager = SetupManager(tmp_path)
    monkeypatch.setattr(manager, "local_checks", lambda: [])
    def fake_fetch(url, timeout=8):
        if "pypi.org/pypi/uv" in url:
            return {"info": {"version": "9.9.9"}}
        if "pypi.org/pypi/mcp" in url:
            return {"info": {"version": "8.8.8"}}
        if "PyQt6" in url:
            return {"info": {"version": "7.7.7"}}
        if "gitlab.gnome.org" in url:
            return [{"name": "GIMP 3.2.6"}]
        return None
    monkeypatch.setattr("gimp_mcp.setup_manager._fetch_json", fake_fetch)
    monkeypatch.setattr("gimp_mcp.setup_manager._run", lambda *a, **k: type("R", (), {"stdout": "0 0\n", "stderr": "", "returncode": 0})())
    data = manager.update_check()
    assert data["uv_upstream"] == "9.9.9"
    assert data["mcp_upstream"] == "8.8.8"
    assert data["pyqt6_upstream"] == "7.7.7"
    assert data["gimp_upstream"] == "3.2.6"
