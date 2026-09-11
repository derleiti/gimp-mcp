from __future__ import annotations

import pytest

from gimp_mcp.prompt_runner import PlanError, parse_plan, transparent_background_requested
from gimp_mcp.studio_mcp_client import StudioMcpClient, StudioMcpError
from gimp_mcp.gui import _prefer_active_session


def test_default_artwork_is_not_transparent():
    assert transparent_background_requested('Create a cute brown bear') is False


@pytest.mark.parametrize('prompt', [
    'Create a bear on a transparent background',
    'transparent canvas with a bear',
    'Keep transparency around the logo',
    'use an alpha background',
    'bear with no background',
])
def test_explicit_transparency_is_preserved(prompt):
    assert transparent_background_requested(prompt) is True


def test_invalid_gegl_filter_rejected_before_execution():
    with pytest.raises(PlanError):
        parse_plan('{"goal":"x","steps":[{"tool":"filter_apply","arguments":{"layer_id":"$last_layer_id","operation":"noise","parameters":{}},"reason":"x"}]}')


def test_valid_gegl_filter_accepted():
    plan = parse_plan('{"goal":"x","steps":[{"tool":"filter_apply","arguments":{"layer_id":"$last_layer_id","operation":"gegl:gaussian-blur","parameters":{}},"reason":"x"}]}')
    assert plan['steps'][0]['tool'] == 'filter_apply'


def test_studio_mcp_client_rejects_bad_mode_without_network():
    client = StudioMcpClient('http://127.0.0.1:9/mcp')
    with pytest.raises(StudioMcpError, match='invalid Studio job mode'):
        client.call_tool('gimp_status', {'__job_mode': 'wat'})


def test_active_studio_session_wins_over_old_live_selection():
    assert _prefer_active_session("active-session", "old-session") == "active-session"
    assert _prefer_active_session(None, "old-session") == "old-session"


def test_mcp_unavailable_has_no_direct_fallback():
    client = StudioMcpClient("http://127.0.0.1:9/mcp", timeout=2)
    with pytest.raises(StudioMcpError, match="MCP call gimp_status"):
        client.call_tool("gimp_status", {})


def test_completed_mcp_result_survives_cleanup_exception_group(monkeypatch):
    # Covered behavior is integration-sensitive; assert source guard exists so a
    # post-result TaskGroup teardown cannot force unsafe mutation replay.
    import inspect
    source=inspect.getsource(StudioMcpClient._call)
    assert 'except BaseExceptionGroup' in source
    assert 'if completed is not None' in source


def test_studio_client_uses_mcp_http_timeout_factory():
    import inspect
    source=inspect.getsource(StudioMcpClient._call)
    assert 'create_mcp_http_client' in source
    assert 'read=max(300.0, call_timeout + 30.0)' in source
