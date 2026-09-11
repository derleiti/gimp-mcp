from pathlib import Path
import shutil
import subprocess
import pytest

from gimp_mcp.vision import VisionRenderer


@pytest.mark.skipif(not (shutil.which("magick") or shutil.which("convert")), reason="ImageMagick not installed")
def test_vision_overlay_and_rolling_gif(tmp_path: Path):
    magick=shutil.which("magick") or shutil.which("convert")
    preview=tmp_path/"preview.png"
    subprocess.run([magick,"-size","320x240","xc:white",str(preview)],check=True)
    info={"width":640,"height":480,"layers":[{"layer_id":7,"name":"Head","x":160,"y":96,"width":200,"height":180,"visible":True,"opacity":100.0}]}
    vr=VisionRenderer(default_grid=64,max_frames=3,frame_delay_ms=300)
    overlay=tmp_path/"vision-overlay.png"
    data=vr.render_overlay(preview,overlay,info,grid_px=64)
    assert overlay.exists() and overlay.stat().st_size > 100
    assert data["coordinate_system"] == {"origin":"top-left","x_axis":"right","y_axis":"down","unit":"canvas-pixel"}
    assert data["layers"][0]["center_x"] == 260.0
    for _ in range(4): vr.append_timeline_frame(tmp_path,overlay)
    frames=sorted((tmp_path/"vision-frames").glob("frame-*.png"))
    assert len(frames) == 3
    gif=vr.render_timeline(tmp_path,frames)
    assert Path(gif["output"]).exists() and gif["frame_count"] == 3
