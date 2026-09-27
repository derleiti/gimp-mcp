import pytest
from mcp import Client
from gimp_mcp.server import mcp

@pytest.mark.anyio
async def test_mcp_tools_and_real_status():
    async with Client(mcp, raise_exceptions=True) as client:
        tools=(await client.list_tools()).tools; names={t.name for t in tools}
        assert {'gimp_status','session_create','document_resize','document_scale','document_crop','document_autocrop','document_flatten','layer_create','layer_duplicate','layer_resize','layer_merge_down','layers_merge_visible','selection_set','guide_add','guide_list','import_layer','text_create','text_update','preview_render','vision_capture','filter_apply','pdb_search','pdb_describe','pdb_call'} <= names
        result=await client.call_tool('gimp_status',{})
        assert result.structured_content['ok'] is True
