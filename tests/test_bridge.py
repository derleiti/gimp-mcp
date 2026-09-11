from gimp_mcp.bridge import GimpBridge


def test_probe():
    data = GimpBridge(timeout=30).probe()
    assert data['pdb_available'] is True
    assert data['gimp_version']
