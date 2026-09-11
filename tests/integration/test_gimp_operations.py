from pathlib import Path
from gimp_mcp.bridge import GimpBridge
from gimp_mcp.operations import GimpOperations
from gimp_mcp.sessions import SessionManager

def test_real_gimp_session(tmp_path: Path):
    bridge=GimpBridge(timeout=30); sessions=SessionManager(tmp_path/'sessions'); ops=GimpOperations(bridge,sessions)
    assert bridge.probe()['pdb_available'] is True
    s=sessions.create(); created=ops.document_create(s,320,200,'AI Canvas')
    layer=ops.layer_create(s,'Overlay',None,None)
    changed=ops.layer_set(s,layer['layer_id'],opacity=42.0,visible=True)
    info=ops.document_info(s)
    assert info['width']==320 and info['height']==200 and info['layer_count']==2
    assert changed['opacity']==42.0
    out=tmp_path/'result.png'; exported=ops.export(s,out)
    assert out.exists() and exported['width']==320
