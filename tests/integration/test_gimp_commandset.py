from pathlib import Path

from gimp_mcp.bridge import GimpBridge
from gimp_mcp.operations import GimpOperations
from gimp_mcp.sessions import SessionManager


def _ops(tmp_path: Path):
    sessions=SessionManager(tmp_path/"sessions")
    ops=GimpOperations(GimpBridge(timeout=45),sessions)
    ops.live.status=lambda: {"ok":False}
    ops.live.mirror_if_available=lambda _path: None
    return sessions,ops


def test_extended_semantic_commandset_real_gimp(tmp_path: Path):
    sessions,ops=_ops(tmp_path)
    s=sessions.create()
    base=ops.document_create(s,160,120,"Background","#ffffff")
    top=ops.layer_create(s,"Top",80,60,opacity=80,blend_mode="multiply")
    ops.layer_fill(s,top["layer_id"],"#ff0000")
    updated=ops.layer_set(s,top["layer_id"],x=12,y=18,opacity=70,blend_mode="screen",lock_alpha=True)
    assert (updated["x"],updated["y"],round(updated["opacity"])) == (12,18,70)
    dup=ops.layer_duplicate(s,top["layer_id"],"Copy")
    assert dup["layer_id"] != top["layer_id"] and dup["name"] == "Copy"
    resized=ops.layer_resize(s,dup["layer_id"],100,90,0,0)
    assert (resized["width"],resized["height"]) == (100,90)
    to_canvas=ops.layer_resize_to_image(s,dup["layer_id"])
    assert (to_canvas["width"],to_canvas["height"]) == (160,120)
    alpha=ops.layer_add_alpha(s,dup["layer_id"])
    assert alpha["has_alpha"] is True
    assert ops.selection(s,"ellipse",x=10,y=10,width=60,height=40,operation="replace")["ok"]
    assert ops.selection(s,"polygon",points=[10,10,70,10,40,60],operation="replace")["ok"]
    assert ops.selection(s,"feather",radius=3)["ok"]
    assert ops.selection(s,"grow",steps=2)["ok"]
    assert ops.selection(s,"translate",dx=1,dy=2)["ok"]
    guide=ops.guide_add(s,"horizontal",30)
    listed=ops.guide_list(s)
    assert any(x["guide_id"]==guide["guide_id"] for x in listed["guides"])
    ops.guide_delete(s,guide["guide_id"])
    assert not any(x["guide_id"]==guide["guide_id"] for x in ops.guide_list(s)["guides"])
    crop=ops.document_crop(s,140,100,5,5)
    assert (crop["width"],crop["height"]) == (140,100)
    scale=ops.document_scale(s,70,50)
    assert (scale["width"],scale["height"]) == (70,50)
    resize=ops.document_resize(s,90,70,4,5)
    assert (resize["width"],resize["height"]) == (90,70)


def test_merge_flatten_import_and_typed_pdb_real_gimp(tmp_path: Path):
    sessions,ops=_ops(tmp_path)
    s=sessions.create(); base=ops.document_create(s,96,64,"Base","#ffffff")
    one=ops.layer_create(s,"One",None,None); ops.layer_fill(s,one["layer_id"],"#00ff00")
    two=ops.layer_duplicate(s,one["layer_id"],"Two")
    merged=ops.layer_merge_down(s,two["layer_id"],"expand")
    assert merged["layer_id"]
    extra=ops.layer_create(s,"Extra",None,None); ops.layer_fill(s,extra["layer_id"],"#0000ff")
    visible=ops.layers_merge_visible(s,"clip-image")
    assert visible["layer_count"] >= 1
    flat=ops.document_flatten(s)
    assert flat["layer_count"] == 1

    source=tmp_path/"source.png"
    ops.export(s,source)
    imported=ops.import_layer(s,source,"Imported")
    assert imported["name"] == "Imported"

    # Proves generic enum coercion in the expert PDB fallback.
    result=ops.pdb_call(s,"gimp-image-select-rectangle",{
        "image":0,"operation":"replace","x":1.0,"y":1.0,"width":10.0,"height":10.0
    })
    assert result["procedure"] == "gimp-image-select-rectangle"


def test_text_update_and_autocrop_real_gimp(tmp_path: Path):
    sessions,ops=_ops(tmp_path)
    s=sessions.create()
    ops.document_create(s,300,200,"Background","#ffffff")
    text=ops.text_create(s,"Hello",20,20,32,"Sans","#336699")
    changed=ops.text_update(s,text["layer_id"],text="Hello GIMP",size=40,color="#ff0000")
    assert changed["text"] == "Hello GIMP"
    assert round(changed["size"]) == 40
    cropped=ops.document_autocrop(s,text["layer_id"])
    assert 0 < cropped["width"] < 300
    assert 0 < cropped["height"] < 200
