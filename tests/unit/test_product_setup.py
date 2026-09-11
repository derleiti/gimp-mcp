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


def test_creative_orchestration_settings_persist(tmp_path):
    from gimp_mcp.control_settings import ControlSettings
    settings=ControlSettings(tmp_path/'control.json')
    data=settings.load()
    assert data['creative_budget']=='effectively_unlimited'
    assert data['max_steps_per_batch']==20
    data.update(creative_budget='extended',max_steps_per_batch=37,provider_planning_timeout=900,idle_watchdog_timeout=420,preview_cadence_mutations=7,vision_review_every_batches=3)
    settings.save(data); loaded=settings.load()
    assert loaded['creative_budget']=='extended' and loaded['max_steps_per_batch']==37
    assert loaded['provider_planning_timeout']==900 and loaded['idle_watchdog_timeout']==420
    assert loaded['preview_cadence_mutations']==7 and loaded['vision_review_every_batches']==3


def test_creative_settings_are_normalized(tmp_path):
    from gimp_mcp.control_settings import ControlSettings
    settings=ControlSettings(tmp_path/'control.json')
    settings.save({'creative_budget':'nonsense','max_steps_per_batch':999,'provider_planning_timeout':1,'vision_review_every_batches':-4})
    data=settings.load()
    assert data['creative_budget']=='effectively_unlimited'
    assert data['max_steps_per_batch']==100
    assert data['provider_planning_timeout']==10
    assert data['vision_review_every_batches']==0
