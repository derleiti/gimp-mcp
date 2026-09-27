import os

from gimp_mcp import server


def test_prompt_runner_uses_configured_endpoint_when_env_missing(monkeypatch):
    monkeypatch.delenv("GIMP_MCP_ENDPOINT", raising=False)
    monkeypatch.setattr(server.ControlSettings, "load", lambda self: {
        "mcp_bind_host":"10.10.0.2", "mcp_port":8000,
        "gimp_operation_timeout":90, "creative_budget":"normal",
        "max_steps_per_batch":20, "provider_planning_timeout":600,
        "provider_review_timeout":300, "preview_render_timeout":120,
        "export_timeout":180, "vision_timeout":120, "idle_watchdog_timeout":300,
        "preview_cadence_mutations":4, "vision_review_every_batches":2,
    })
    seen={}
    class DummyClient:
        def __init__(self, endpoint, timeout, bearer_token): seen["endpoint"]=endpoint
        def call_tool(self,*a,**k): return {}
    monkeypatch.setattr(server, "StudioMcpClient", DummyClient)
    server._mcp_prompt_runner()
    assert seen["endpoint"] == "http://10.10.0.2:8000/mcp"
