from pathlib import Path

from gimp_mcp.operations import GimpOperations
from gimp_mcp.sessions import ArtworkSession


class CaptureBridge:
    def __init__(self):
        self.body = ""
    def run_json(self, body, **kwargs):
        self.body = body
        return {"ok": True}


class DummySessions:
    def snapshot(self, _s): return None
    def rollback_failed(self, _s, _snap): pass


def test_filter_parameters_are_embedded_as_json_string_not_python_literals(tmp_path: Path):
    bridge = CaptureBridge()
    ops = GimpOperations(bridge, DummySessions())
    ops.live.status = lambda: {"ok": False}
    ops.live.mirror_if_available = lambda _path: None
    s = ArtworkSession("s", tmp_path, tmp_path / "document.xcf")
    ops.filter_apply(s, 1, "gegl:test", {"enabled": True, "optional": None}, None)
    assert "params=json.loads(" in bridge.body
    assert "true" in bridge.body and "null" in bridge.body
