from __future__ import annotations

import html
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class VisionRenderer:
    """Render non-destructive GIMP-coordinate overlays and a rolling GIF timeline."""

    def __init__(self, *, default_grid: int = 64, max_frames: int = 10, frame_delay_ms: int = 450) -> None:
        self.default_grid = max(16, int(default_grid))
        self.max_frames = max(2, min(int(max_frames), 30))
        self.frame_delay_ms = max(100, min(int(frame_delay_ms), 5000))

    @staticmethod
    def _magick() -> str:
        exe = shutil.which("magick") or shutil.which("convert")
        if not exe:
            raise RuntimeError("Vision rendering requires ImageMagick (magick or convert)")
        return exe

    @classmethod
    def _image_size(cls, path: Path) -> tuple[int, int]:
        exe = cls._magick()
        proc = subprocess.run([exe, "identify", "-format", "%w %h", str(path)], capture_output=True, text=True, timeout=15, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or f"Unable to inspect {path}").strip()[-800:])
        parts = proc.stdout.strip().split()
        if len(parts) != 2:
            raise RuntimeError(f"Unexpected ImageMagick identify output: {proc.stdout!r}")
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    def render_overlay(
        self,
        preview_path: Path,
        output_path: Path,
        document_info: dict[str, Any],
        *,
        grid_px: int | None = None,
        show_layer_bounds: bool = True,
        show_labels: bool = True,
        show_centers: bool = True,
    ) -> dict[str, Any]:
        if not preview_path.is_file():
            raise FileNotFoundError(preview_path)
        image_w, image_h = self._image_size(preview_path)
        canvas_w = int(document_info.get("width") or image_w)
        canvas_h = int(document_info.get("height") or image_h)
        grid = max(16, int(grid_px or self.default_grid))
        sx = image_w / float(max(1, canvas_w)); sy = image_h / float(max(1, canvas_h))

        svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_w}" height="{image_h}" viewBox="0 0 {image_w} {image_h}">',
            '<g font-family="DejaVu Sans, sans-serif" font-size="11">',
        ]
        # GIMP/XCF global canvas coordinates: origin top-left, +X right, +Y down.
        for x in range(0, canvas_w + 1, grid):
            px = round(x * sx, 2)
            svg.append(f'<line x1="{px}" y1="0" x2="{px}" y2="{image_h}" stroke="#ffff00" stroke-opacity="0.38" stroke-width="1" stroke-dasharray="4 4"/>')
            if x < canvas_w:
                svg.append(f'<rect x="{min(px+2,max(0,image_w-48))}" y="2" width="46" height="17" fill="#000" fill-opacity="0.65" rx="2"/>')
                svg.append(f'<text x="{min(px+5,max(3,image_w-45))}" y="14" fill="#fff">x={x}</text>')
        for y in range(0, canvas_h + 1, grid):
            py = round(y * sy, 2)
            svg.append(f'<line x1="0" y1="{py}" x2="{image_w}" y2="{py}" stroke="#ffff00" stroke-opacity="0.38" stroke-width="1" stroke-dasharray="4 4"/>')
            if y < canvas_h:
                svg.append(f'<rect x="2" y="{min(py+2,max(0,image_h-19))}" width="46" height="17" fill="#000" fill-opacity="0.65" rx="2"/>')
                svg.append(f'<text x="5" y="{min(py+14,max(12,image_h-5))}" fill="#fff">y={y}</text>')

        layers_meta: list[dict[str, Any]] = []
        for layer in document_info.get("layers") or []:
            try:
                raw_cx, raw_cy = layer.get("content_x"), layer.get("content_y")
                if raw_cx is not None and raw_cy is not None and int(layer.get("content_width", 0)) > 0 and int(layer.get("content_height", 0)) > 0:
                    lx, ly = int(raw_cx), int(raw_cy)
                    lw, lh = int(layer.get("content_width", 0)), int(layer.get("content_height", 0))
                    bounds_kind = "visible-content"
                else:
                    lx, ly = int(layer.get("x", 0)), int(layer.get("y", 0))
                    lw, lh = int(layer.get("width", 0)), int(layer.get("height", 0))
                    bounds_kind = "layer"
            except Exception:
                continue
            px, py, pw, ph = lx*sx, ly*sy, max(1,lw*sx), max(1,lh*sy)
            if show_layer_bounds:
                svg.append(f'<rect x="{px:.2f}" y="{py:.2f}" width="{pw:.2f}" height="{ph:.2f}" fill="none" stroke="#ff4646" stroke-opacity="0.9" stroke-width="2"/>')
            cx, cy = lx + lw/2.0, ly + lh/2.0
            if show_centers:
                pcx, pcy = cx*sx, cy*sy
                svg.append(f'<line x1="{pcx-6:.2f}" y1="{pcy:.2f}" x2="{pcx+6:.2f}" y2="{pcy:.2f}" stroke="#00ffff" stroke-width="2"/>')
                svg.append(f'<line x1="{pcx:.2f}" y1="{pcy-6:.2f}" x2="{pcx:.2f}" y2="{pcy+6:.2f}" stroke="#00ffff" stroke-width="2"/>')
            if show_labels:
                label = self._esc(f"{layer.get('name','Layer')} [{lx},{ly} {lw}x{lh}]")
                tx, ty = max(2, min(px+3, image_w-260)), max(13, min(py+14, image_h-4))
                svg.append(f'<rect x="{tx-3:.2f}" y="{ty-12:.2f}" width="255" height="17" fill="#000" fill-opacity="0.68" rx="2"/>')
                svg.append(f'<text x="{tx:.2f}" y="{ty:.2f}" fill="#fff">{label}</text>')
            layers_meta.append({
                "layer_id": layer.get("layer_id"), "name": layer.get("name"),
                "x": lx, "y": ly, "width": lw, "height": lh,
                "center_x": round(cx,2), "center_y": round(cy,2),
                "visible": layer.get("visible"), "opacity": layer.get("opacity"), "bounds_kind": bounds_kind,
            })

        title = self._esc(f"GIMP canvas {canvas_w}x{canvas_h} | origin (0,0) top-left | +x right | +y down | grid {grid}px")
        svg.append(f'<rect x="4" y="{max(0,image_h-24)}" width="{min(image_w-8,610)}" height="20" fill="#000" fill-opacity="0.72" rx="3"/>')
        svg.append(f'<text x="8" y="{max(14,image_h-9)}" fill="#fff">{title}</text>')
        svg.append('</g></svg>')

        overlay_svg = output_path.with_suffix('.overlay.svg')
        overlay_svg.write_text(''.join(svg), encoding='utf-8')
        tmp = output_path.with_suffix('.tmp.png')
        exe = self._magick()
        proc = subprocess.run([exe, str(preview_path), str(overlay_svg), '-compose', 'over', '-composite', str(tmp)], capture_output=True, text=True, timeout=30, check=False)
        overlay_svg.unlink(missing_ok=True)
        if proc.returncode != 0 or not tmp.exists():
            raise RuntimeError((proc.stderr or proc.stdout or 'ImageMagick overlay creation failed').strip()[-1200:])
        tmp.replace(output_path)
        return {
            "output": str(output_path), "width": image_w, "height": image_h,
            "canvas_width": canvas_w, "canvas_height": canvas_h, "grid_px": grid,
            "coordinate_system": {"origin":"top-left","x_axis":"right","y_axis":"down","unit":"canvas-pixel"},
            "layers": layers_meta,
        }

    def append_timeline_frame(self, session_dir: Path, overlay_path: Path) -> list[Path]:
        frames = session_dir / 'vision-frames'; frames.mkdir(parents=True, exist_ok=True)
        target = frames / f'frame-{time.time_ns():020d}.png'; shutil.copy2(overlay_path, target)
        paths = sorted(frames.glob('frame-*.png'))
        for old in paths[:-self.max_frames]: old.unlink(missing_ok=True)
        return sorted(frames.glob('frame-*.png'))

    def render_timeline(self, session_dir: Path, frames: list[Path] | None = None) -> dict[str, Any]:
        frames = frames or sorted((session_dir/'vision-frames').glob('frame-*.png'))[-self.max_frames:]
        if not frames: raise FileNotFoundError('No vision frames captured yet')
        output=session_dir/'vision-timeline.gif'; tmp=session_dir/'vision-timeline.tmp.gif'
        exe=self._magick(); delay_cs=max(2,int(round(self.frame_delay_ms/10.0)))
        proc=subprocess.run([exe,'-delay',str(delay_cs),'-loop','0',*[str(p) for p in frames],str(tmp)],capture_output=True,text=True,timeout=30,check=False)
        if proc.returncode != 0 or not tmp.exists(): raise RuntimeError((proc.stderr or proc.stdout or 'ImageMagick GIF creation failed').strip()[-1200:])
        tmp.replace(output)
        return {"output":str(output),"frame_count":len(frames),"frame_delay_ms":self.frame_delay_ms,"loop":True}

    @staticmethod
    def write_metadata(session_dir: Path, payload: dict[str, Any]) -> Path:
        path=session_dir/'vision.json'; tmp=session_dir/'vision.json.tmp'
        tmp.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(path); return path
