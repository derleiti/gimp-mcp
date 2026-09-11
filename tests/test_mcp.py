import pytest
from mcp import Client
from gimp_mcp.server import mcp

@pytest.mark.anyio
async def test_mcp_tools_and_real_status():
    async with Client(mcp, raise_exceptions=True) as client:
        tools=(await client.list_tools()).tools; names={t.name for t in tools}
        assert {'gimp_status','session_create','layer_create','preview_render','filter_apply','pdb_search'} <= names
        result=await client.call_tool('gimp_status',{})
        assert result.structured_content['ok'] is True
