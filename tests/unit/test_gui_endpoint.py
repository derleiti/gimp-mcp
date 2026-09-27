from gimp_mcp.gui import _studio_mcp_endpoint


def test_studio_endpoint_uses_configured_bind_host():
    assert _studio_mcp_endpoint({"mcp_bind_host":"10.10.0.2","mcp_port":8000}) == "http://10.10.0.2:8000/mcp"


def test_studio_endpoint_collapses_bind_all_to_loopback():
    assert _studio_mcp_endpoint({"mcp_bind_host":"0.0.0.0","mcp_port":8123}) == "http://127.0.0.1:8123/mcp"


def test_studio_endpoint_formats_ipv6():
    assert _studio_mcp_endpoint({"mcp_bind_host":"::1","mcp_port":8000}) == "http://[::1]:8000/mcp"
