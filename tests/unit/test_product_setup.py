from pathlib import Path

import pytest

from gimp_mcp.control_settings import ControlSettings
from gimp_mcp.server import _StaticTokenVerifier
from gimp_mcp.setup_manager import SetupManager


def test_product_defaults_are_local_and_secure(tmp_path: Path):
    data=ControlSettings(tmp_path/"control.json").load()
    assert data["first_run_complete"] is False
    assert data["mcp_network_enabled"] is False
    assert data["mcp_bind_host"] == "127.0.0.1"
    assert data["mcp_require_auth"] is True
    assert data["check_updates_on_start"] is True


def test_latest_gimp_release_builds_official_appimage(monkeypatch, tmp_path: Path):
    manager=SetupManager(tmp_path)
    monkeypatch.setattr("gimp_mcp.setup_manager.platform.machine",lambda:"x86_64")
    monkeypatch.setattr("gimp_mcp.setup_manager._fetch_json",lambda *a,**k:[{"name":"GIMP 3.2.6"}])
    release=manager.latest_gimp_release()
    assert release["version"] == "3.2.6"
    assert release["url"].startswith("https://download.gimp.org/")
    assert release["filename"] == "GIMP-3.2.6-x86_64.AppImage"
    assert release["checksums_url"].endswith("/SHA256SUMS")


@pytest.mark.anyio
async def test_static_network_token_verifier():
    verifier=_StaticTokenVerifier("secret")
    assert await verifier.verify_token("wrong") is None
    token=await verifier.verify_token("secret")
    assert token is not None
    assert "gimp:control" in token.scopes
