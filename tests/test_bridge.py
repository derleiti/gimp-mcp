from gimp_mcp.bridge import GimpBridge


def test_probe():
    data = GimpBridge(timeout=30).probe()
    assert data['pdb_available'] is True
    assert data['gimp_version']


def test_batch_bridge_marks_environment_to_disable_live_plugin():
    from gimp_mcp.bridge import GimpBridge
    env=GimpBridge._clean_env()
    assert env['GIMP_MCP_BATCH'] == '1'
