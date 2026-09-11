from __future__ import annotations

import json, math, random, threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .bridge import GimpBridge
from .errors import GimpMcpError
from .live_bridge import LiveBridge
from .sessions import ArtworkSession, SessionManager


def _q(v: str | Path) -> str:
    return json.dumps(str(v))

def _load(path: Path) -> str:
    return f"path={_q(path)}\nimg=Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,Gio.File.new_for_path(path))\n"

def _save(path: Path) -> str:
    return f"Gimp.file_save(Gimp.RunMode.NONINTERACTIVE,img,Gio.File.new_for_path({_q(path)}),None)\n"

def _layer_lookup(layer_id: int) -> str:
    return f"layer_id={int(layer_id)}\nlayer=next((x for x in img.get_layers() if int(x.get_tattoo())==layer_id),None)\nif layer is None: raise RuntimeError('LAYER_NOT_FOUND')\n"

class GimpOperations:
    def __init__(self, bridge: GimpBridge, sessions: SessionManager) -> None:
        self.bridge, self.sessions = bridge, sessions
        self.live = LiveBridge()
        self._execution = threading.local()

    @contextmanager
    def execution_mode(self, mode: str):
        previous = getattr(self._execution, "mode", "auto")
        self._execution.mode = mode if mode in {"auto", "live", "batch"} else "auto"
        try:
            yield
        finally:
            self._execution.mode = previous

    def _mode(self) -> str:
        return getattr(self._execution, "mode", "auto")

    def _live_ready(self) -> bool:
        if self._mode() == "batch":
            return False
        return bool(self.live.status().get("ok"))

    def _mirror(self, s: ArtworkSession) -> None:
        if self._mode() != "batch":
            self.live.mirror_if_available(s.document)

    def sync_visible(self, s: ArtworkSession) -> None:
        self._mirror(s)

    def _mutate(self, s: ArtworkSession, body: str) -> Any:
        with s.lock:
            snap = self.sessions.snapshot(s)
            try:
                result = self.bridge.run_json(body)
                self._mirror(s)
                return result
            except Exception:
                self.sessions.rollback_failed(s, snap)
                raise

    def document_create(self, s: ArtworkSession, width: int, height: int, name: str) -> dict[str, Any]:
        if not (1 <= width <= 20000 and 1 <= height <= 20000):
            raise GimpMcpError('INVALID_ARGUMENT', 'width and height must be within 1..20000', False)
        tattoo = random.randint(100000, 2_000_000_000)
        result = self.bridge.run_json(
            f"w={width};h={height};name={_q(name)};tattoo={tattoo}\n"
            "img=Gimp.Image.new(w,h,Gimp.ImageBaseType.RGB)\n"
            "layer=Gimp.Layer.new(img,name,w,h,Gimp.ImageType.RGBA_IMAGE,100.0,Gimp.LayerMode.NORMAL)\n"
            "img.insert_layer(layer,None,0);layer.fill(Gimp.FillType.TRANSPARENT);layer.set_tattoo(tattoo)\n"
            + _save(s.document)
            + "result={'width':w,'height':h,'layer_id':tattoo,'layer_name':name}\nimg.delete()\n")
        self._mirror(s)
        return result

    def document_info(self, s: ArtworkSession) -> dict[str, Any]:
        return self.bridge.run_json(_load(s.document) + "layers=img.get_layers()\nresult={'width':img.get_width(),'height':img.get_height(),'layer_count':len(layers),'layers':[{'layer_id':int(x.get_tattoo()),'name':x.get_name(),'width':x.get_width(),'height':x.get_height(),'visible':x.get_visible(),'opacity':x.get_opacity()} for x in layers]}\nimg.delete()\n")

    def shape_create(self, s: ArtworkSession, name: str, shape: str, x: float, y: float, width: float, height: float, color: str) -> dict[str, Any]:
        shape = str(shape).strip().lower()
        if shape not in {"ellipse", "rectangle"}:
            raise GimpMcpError("INVALID_ARGUMENT", "shape must be ellipse or rectangle", False)
        if width <= 0 or height <= 0:
            raise GimpMcpError("INVALID_ARGUMENT", "shape width/height must be positive", False)
        color = str(color).strip()
        if not color or len(color) > 128 or any(ord(ch) < 32 for ch in color):
            raise GimpMcpError("INVALID_ARGUMENT", "color must be a non-empty CSS/Gegl color string", False)
        tattoo = random.randint(100000, 2_000_000_000)
        body = _load(s.document)
        body += f"name={_q(name)};shape={_q(shape)};x={float(x)};y={float(y)};w={float(width)};h={float(height)};color_text={_q(color)};tattoo={tattoo}\n"
        body += "layer=Gimp.Layer.new(img,name,img.get_width(),img.get_height(),Gimp.ImageType.RGBA_IMAGE,100.0,Gimp.LayerMode.NORMAL)\nimg.insert_layer(layer,None,0);layer.fill(Gimp.FillType.TRANSPARENT);layer.set_tattoo(tattoo)\n"
        body += "color=Gegl.Color.new(color_text)\nif color is None: raise RuntimeError('INVALID_COLOR')\nGimp.context_push()\ntry:\n Gimp.context_set_foreground(color)\n if shape=='ellipse': ok=img.select_ellipse(Gimp.ChannelOps.REPLACE,x,y,w,h)\n else: ok=img.select_rectangle(Gimp.ChannelOps.REPLACE,x,y,w,h)\n layer.edit_fill(Gimp.FillType.FOREGROUND)\n Gimp.Selection.none(img)\nfinally:\n Gimp.context_pop()\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'name':name,'shape':shape,'x':x,'y':y,'width':w,'height':h,'color':color_text}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_create(self, s: ArtworkSession, name: str, width: int | None, height: int | None, *, opacity: float = 100.0, visible: bool = True, blend_mode: str = "normal") -> dict[str, Any]:
        if not 0 <= float(opacity) <= 100:
            raise GimpMcpError('INVALID_ARGUMENT', 'opacity must be 0..100', False)
        aliases = {
            'normal': 'NORMAL', 'multiply': 'MULTIPLY', 'screen': 'SCREEN',
            'overlay': 'OVERLAY', 'addition': 'ADDITION', 'subtract': 'SUBTRACT',
            'darken': 'DARKEN_ONLY', 'lighten': 'LIGHTEN_ONLY',
        }
        key = str(blend_mode or 'normal').strip().lower().replace('-', '_').replace(' ', '_')
        enum_name = aliases.get(key, key.upper())
        tattoo = random.randint(100000, 2_000_000_000)
        body = _load(s.document) + f"name={_q(name)};w={int(width) if width else 0};h={int(height) if height else 0};tattoo={tattoo};opacity={float(opacity)};visible={bool(visible)};mode_name={_q(enum_name)}\n"
        body += "w=w or img.get_width();h=h or img.get_height();mode=getattr(Gimp.LayerMode,mode_name,None)\nif mode is None: raise RuntimeError('UNSUPPORTED_BLEND_MODE:'+mode_name)\nimg.undo_group_start()\nlayer=Gimp.Layer.new(img,name,w,h,Gimp.ImageType.RGBA_IMAGE,opacity,mode)\nimg.insert_layer(layer,None,0);layer.fill(Gimp.FillType.TRANSPARENT);layer.set_tattoo(tattoo);layer.set_visible(visible);img.undo_group_end()\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'name':name,'width':w,'height':h,'opacity':layer.get_opacity(),'visible':layer.get_visible(),'blend_mode':mode_name}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_delete(self, s: ArtworkSession, layer_id: int) -> dict[str, Any]:
        body = _load(s.document) + _layer_lookup(layer_id) + "name=layer.get_name();img.remove_layer(layer)\n" + _save(s.document) + "result={'deleted':True,'layer_id':layer_id,'name':name}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_set(self, s: ArtworkSession, layer_id: int, *, name: str | None = None, visible: bool | None = None, opacity: float | None = None) -> dict[str, Any]:
        if opacity is not None and not 0 <= opacity <= 100:
            raise GimpMcpError('INVALID_ARGUMENT', 'opacity must be 0..100', False)
        if self._live_ready():
            with s.lock:
                snap = self.sessions.snapshot(s)
                try:
                    result = self.live.layer_update(s.document, layer_id, name=name, visible=visible, opacity=opacity)
                    if result.get('ok'):
                        result['execution_mode'] = 'live-direct'
                        return result
                    raise RuntimeError(result.get('error') or 'live layer update failed')
                except Exception:
                    self.sessions.rollback_failed(s, snap)
                    self._mirror(s)
        body = _load(s.document) + _layer_lookup(layer_id)
        if name is not None: body += f"layer.set_name({_q(name)})\n"
        if visible is not None: body += f"layer.set_visible({bool(visible)})\n"
        if opacity is not None: body += f"layer.set_opacity({float(opacity)})\n"
        body += _save(s.document) + "result={'layer_id':layer_id,'name':layer.get_name(),'visible':layer.get_visible(),'opacity':layer.get_opacity()}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_fill(self, s: ArtworkSession, layer_id: int, color: str) -> dict[str, Any]:
        color = str(color).strip()
        if not color or len(color) > 128 or any(ord(ch) < 32 for ch in color):
            raise GimpMcpError("INVALID_ARGUMENT", "color must be a non-empty CSS/Gegl color string", False)
        body = _load(s.document) + _layer_lookup(layer_id)
        body += f"color_text={_q(color)}\ncolor=Gegl.Color.new(color_text)\nif color is None: raise RuntimeError('INVALID_COLOR')\n"
        body += "Gimp.context_push()\ntry:\n Gimp.context_set_foreground(color)\n layer.edit_fill(Gimp.FillType.FOREGROUND)\nfinally:\n Gimp.context_pop()\n"
        body += _save(s.document) + "result={'layer_id':layer_id,'color':color_text}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_reorder(self, s: ArtworkSession, layer_id: int, position: int) -> dict[str, Any]:
        body = _load(s.document) + _layer_lookup(layer_id)
        body += f"position={int(position)};count=len(img.get_layers());position=max(0,min(position,count-1));img.reorder_item(layer,None,position)\n"
        body += _save(s.document) + "result={'layer_id':layer_id,'position':position}\nimg.delete()\n"
        return self._mutate(s, body)

    def selection(self, s: ArtworkSession, action: str, **kwargs: Any) -> dict[str, Any]:
        body = _load(s.document)
        if action == 'none': body += "ok=Gimp.Selection.none(img)\n"
        elif action == 'all': body += "ok=Gimp.Selection.all(img)\n"
        elif action == 'invert': body += "ok=Gimp.Selection.invert(img)\n"
        elif action == 'rectangle': body += f"ok=img.select_rectangle(Gimp.ChannelOps.REPLACE,{float(kwargs['x'])},{float(kwargs['y'])},{float(kwargs['width'])},{float(kwargs['height'])})\n"
        else: raise GimpMcpError('UNSUPPORTED_OPERATION', f'Unknown selection action: {action}', False)
        body += _save(s.document) + f"result={{'action':{_q(action)},'ok':bool(ok)}}\nimg.delete()\n"
        return self._mutate(s, body)

    def text_create(self, s: ArtworkSession, text: str, x: float, y: float, size: float, font_name: str = 'Sans') -> dict[str, Any]:
        if not 1 <= size <= 2000: raise GimpMcpError('INVALID_ARGUMENT', 'font size must be 1..2000 px', False)
        tattoo = random.randint(100000, 2_000_000_000)
        body = _load(s.document) + f"text={_q(text)};font_name={_q(font_name)};size={float(size)};x={float(x)};y={float(y)};tattoo={tattoo}\n"
        body += "font=Gimp.Font.get_by_name(font_name)\nif font is None:\n font=Gimp.context_get_font()\nif font is None: raise RuntimeError('FONT_NOT_FOUND')\n"
        body += "img.undo_group_start()\nlayer=Gimp.TextLayer.new(img,text,font,size,Gimp.Unit.pixel());layer.set_tattoo(tattoo);img.insert_layer(layer,None,0);layer.set_offsets(int(x),int(y));img.undo_group_end()\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'text':text,'font':font_name,'size':size,'x':x,'y':y}\nimg.delete()\n"
        return self._mutate(s, body)

    @staticmethod
    def _normalize_transform_values(action: str, values: Any) -> list[float]:
        action = str(action).strip().lower()
        if isinstance(values, dict):
            if action == 'translate':
                x = values.get('x', values.get('dx'))
                y = values.get('y', values.get('dy'))
                if x is not None and y is not None:
                    return [float(x), float(y)]
            elif action == 'rotate':
                angle = values.get('degrees', values.get('angle'))
                if angle is not None:
                    cx = values.get('cx', values.get('center_x'))
                    cy = values.get('cy', values.get('center_y'))
                    return [float(angle)] if cx is None or cy is None else [float(angle), float(cx), float(cy)]
            elif action == 'scale':
                keys = ('x0', 'y0', 'x1', 'y1')
                if all(k in values for k in keys):
                    return [float(values[k]) for k in keys]
                if all(k in values for k in ('x', 'y', 'width', 'height')):
                    x=float(values['x']); y=float(values['y'])
                    return [x, y, x+float(values['width']), y+float(values['height'])]
        if isinstance(values, (list, tuple)):
            try:
                return [float(v) for v in values]
            except (TypeError, ValueError) as exc:
                raise GimpMcpError('INVALID_ARGUMENT', 'transform values must be numeric', False) from exc
        raise GimpMcpError('INVALID_ARGUMENT', f'unsupported transform values for {action}: expected list or object', False)

    def transform(self, s: ArtworkSession, layer_id: int, action: str, values: Any) -> dict[str, Any]:
        action = str(action).strip().lower()
        values = self._normalize_transform_values(action, values)
        if action == 'translate' and len(values) == 2 and self._live_ready():
            with s.lock:
                snap = self.sessions.snapshot(s)
                try:
                    result = self.live.translate(s.document, layer_id, float(values[0]), float(values[1]))
                    if result.get('ok'):
                        result['execution_mode'] = 'live-direct'
                        return result
                    raise RuntimeError(result.get('error') or 'live translate failed')
                except Exception:
                    self.sessions.rollback_failed(s, snap)
                    self._mirror(s)
        body = _load(s.document) + _layer_lookup(layer_id)
        if action == 'translate' and len(values) == 2: body += f"layer.transform_translate({float(values[0])},{float(values[1])})\n"
        elif action == 'rotate' and len(values) in {1,3}:
            angle = math.radians(values[0])
            body += f"layer.transform_rotate({angle},{str(len(values)==1)}," + ("0.0,0.0" if len(values)==1 else f"{float(values[1])},{float(values[2])}") + ")\n"
        elif action == 'scale' and len(values) == 4: body += f"layer.transform_scale({float(values[0])},{float(values[1])},{float(values[2])},{float(values[3])})\n"
        else: raise GimpMcpError('INVALID_ARGUMENT', 'translate=[dx,dy], rotate=[degrees] or [degrees,cx,cy], scale=[x0,y0,x1,y1]', False)
        body += _save(s.document) + f"result={{'layer_id':layer_id,'action':{_q(action)}}}\nimg.delete()\n"
        return self._mutate(s, body)

    def filter_apply(self, s: ArtworkSession, layer_id: int, operation: str, parameters: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        body = _load(s.document) + _layer_lookup(layer_id)
        body += f"operation={_q(operation)};params=json.loads({_q(json.dumps(parameters, ensure_ascii=False))});filter_name={_q(name or operation)}\n"
        body += "flt=Gimp.DrawableFilter.new(layer,operation,filter_name)\nif flt is None: raise RuntimeError('FILTER_NOT_FOUND')\nconfig=flt.get_config()\n"
        body += "for key,value in params.items(): config.set_property(key,value)\nflt.update();layer.append_filter(flt)\n"
        body += _save(s.document) + "result={'layer_id':layer_id,'operation':operation,'name':filter_name,'filter_count':len(layer.get_filters())}\nimg.delete()\n"
        return self._mutate(s, body)

    def filter_list(self, query: str, limit: int) -> dict[str, Any]:
        # GIMP 3.2.2 python-fu-eval crashes in Gegl.list_operations(); keep discovery crash-safe.
        supported = [
            "gegl:gaussian-blur", "gegl:brightness-contrast", "gegl:unsharp-mask",
            "gegl:color-temperature", "gegl:shadows-highlights",
        ]
        q = query.lower().strip()
        items = [x for x in supported if q in x.lower()]
        return {"count": len(items), "operations": items[:max(1, min(limit, 500))], "discovery": "validated-safe-registry"}

    def filter_describe(self, operation: str) -> dict[str, Any]:
        return self.bridge.run_json(
            f"op={_q(operation)}\nimg=Gimp.Image.new(8,8,Gimp.ImageBaseType.RGB)\nlayer=Gimp.Layer.new(img,'probe',8,8,Gimp.ImageType.RGBA_IMAGE,100.0,Gimp.LayerMode.NORMAL)\nimg.insert_layer(layer,None,0)\nflt=Gimp.DrawableFilter.new(layer,op,op)\n"
            "if flt is None: result={'operation':op,'found':False,'properties':[]}\n"
            "else:\n props=[]\n config=flt.get_config()\n for spec in config.list_properties():\n  default=getattr(spec,'default_value',None)\n  if not isinstance(default,(str,int,float,bool,type(None))): default=str(default)\n  props.append({'name':spec.name,'type':spec.value_type.name,'default':default})\n result={'operation':op,'found':True,'properties':props}\nimg.delete()\n"
        )

    def export(self, s: ArtworkSession, output: Path, max_width: int | None = None, max_height: int | None = None) -> dict[str, Any]:
        body = _load(s.document)
        if max_width or max_height:
            body += f"mw={int(max_width or 0)};mh={int(max_height or 0)}\nw=img.get_width();h=img.get_height();scale=min((mw/w if mw else 999999),(mh/h if mh else 999999),1.0)\nif scale<1.0: img.scale(max(1,int(w*scale)),max(1,int(h*scale)))\n"
        body += f"out={_q(output)}\nGimp.file_save(Gimp.RunMode.NONINTERACTIVE,img,Gio.File.new_for_path(out),None)\nresult={{'output':out,'width':img.get_width(),'height':img.get_height()}}\nimg.delete()\n"
        return self.bridge.run_json(body)

    def pdb_search(self, query: str, limit: int) -> dict[str, Any]:
        return self.bridge.run_json(f"q={_q(query.lower())};limit={max(1,min(limit,500))}\npdb=Gimp.get_pdb();names=list(pdb.query_procedures('.*','.*','.*','.*','.*','.*','.*','.*'));items=[n for n in names if q in n.lower()];result={{'count':len(items),'procedures':items[:limit]}}\n")

    def pdb_describe(self, name: str) -> dict[str, Any]:
        return self.bridge.run_json(f"name={_q(name)}\npdb=Gimp.get_pdb();proc=pdb.lookup_procedure(name)\nif proc is None: result={{'found':False,'name':name}}\nelse:\n args=[{{'name':s.name,'type':s.value_type.name,'blurb':getattr(s,'blurb','')}} for s in proc.get_arguments()]\n rets=[{{'name':s.name,'type':s.value_type.name,'blurb':getattr(s,'blurb','')}} for s in proc.get_return_values()]\n result={{'found':True,'name':name,'arguments':args,'returns':rets}}\n")
