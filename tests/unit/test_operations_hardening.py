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


def test_filter_parameter_normalization_is_model_friendly():
    from gimp_mcp.operations import GimpOperations
    p=GimpOperations._normalize_filter_parameters("gegl:gaussian-blur", {"radius": 4})
    assert p == {"std-dev-x": 4, "std-dev-y": 4}
    p=GimpOperations._normalize_filter_parameters("gegl:unsharp-mask", {"radius": 2, "amount": .7})
    assert p["std-dev"] == 2 and p["scale"] == .7


def test_brightness_contrast_percent_compatibility():
    from gimp_mcp.operations import GimpOperations
    p=GimpOperations._normalize_filter_parameters("gegl:brightness-contrast", {"brightness":5,"contrast":5})
    assert p["brightness"] == 0.05
    assert p["contrast"] == 1.05
    p=GimpOperations._normalize_filter_parameters("gegl:brightness-contrast", {"brightness_percent":-10,"contrast_percent":12})
    assert p["brightness"] == -0.1
    assert p["contrast"] == 1.12
