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

    def document_create(self, s: ArtworkSession, width: int, height: int, name: str, background_color: str | None = None) -> dict[str, Any]:
        if not (1 <= width <= 20000 and 1 <= height <= 20000):
            raise GimpMcpError('INVALID_ARGUMENT', 'width and height must be within 1..20000', False)
        tattoo = random.randint(100000, 2_000_000_000)
        result = self.bridge.run_json(
            f"w={width};h={height};name={_q(name)};tattoo={tattoo}\n"
            "img=Gimp.Image.new(w,h,Gimp.ImageBaseType.RGB)\n"
            "layer=Gimp.Layer.new(img,name,w,h,Gimp.ImageType.RGBA_IMAGE,100.0,Gimp.LayerMode.NORMAL)\n"
            "img.insert_layer(layer,None,0);layer.fill(Gimp.FillType.TRANSPARENT);layer.set_tattoo(tattoo)\n"
            + (f"color=Gegl.Color.new({_q(background_color)});Gimp.context_push();Gimp.context_set_foreground(color);layer.edit_fill(Gimp.FillType.FOREGROUND);Gimp.context_pop()\n" if background_color else "")
            + _save(s.document)
            + "result={'width':w,'height':h,'layer_id':tattoo,'layer_name':name}\nimg.delete()\n")
        self._mirror(s)
        return result

    def document_info(self, s: ArtworkSession) -> dict[str, Any]:
        return self.bridge.run_json(_load(s.document) + "layers=img.get_layers()\nitems=[]\nfor x in layers:\n ok,ox,oy=x.get_offsets()\n Gimp.Selection.none(img)\n selected=img.select_item(Gimp.ChannelOps.REPLACE,x)\n bok,non_empty,x1,y1,x2,y2=Gimp.Selection.bounds(img)\n items.append({'layer_id':int(x.get_tattoo()),'name':x.get_name(),'x':int(ox) if ok else 0,'y':int(oy) if ok else 0,'width':x.get_width(),'height':x.get_height(),'content_x':int(x1) if bok and non_empty else None,'content_y':int(y1) if bok and non_empty else None,'content_width':int(x2-x1) if bok and non_empty else 0,'content_height':int(y2-y1) if bok and non_empty else 0,'visible':x.get_visible(),'opacity':x.get_opacity()})\nGimp.Selection.none(img)\nresult={'width':img.get_width(),'height':img.get_height(),'layer_count':len(layers),'layers':items}\nimg.delete()\n")

    def document_resize(self, s: ArtworkSession, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> dict[str, Any]:
        if not (1 <= int(width) <= 20000 and 1 <= int(height) <= 20000):
            raise GimpMcpError('INVALID_ARGUMENT', 'width and height must be within 1..20000', False)
        body = _load(s.document)
        body += f"w={int(width)};h={int(height)};ox={int(offset_x)};oy={int(offset_y)}\nimg.resize(w,h,ox,oy)\n"
        body += _save(s.document) + "result={'width':img.get_width(),'height':img.get_height(),'offset_x':ox,'offset_y':oy}\nimg.delete()\n"
        return self._mutate(s, body)

    def document_scale(self, s: ArtworkSession, width: int, height: int) -> dict[str, Any]:
        if not (1 <= int(width) <= 20000 and 1 <= int(height) <= 20000):
            raise GimpMcpError('INVALID_ARGUMENT', 'width and height must be within 1..20000', False)
        body = _load(s.document)
        body += f"w={int(width)};h={int(height)}\nimg.scale(w,h)\n"
        body += _save(s.document) + "result={'width':img.get_width(),'height':img.get_height()}\nimg.delete()\n"
        return self._mutate(s, body)

    def document_crop(self, s: ArtworkSession, width: int, height: int, x: int = 0, y: int = 0) -> dict[str, Any]:
        if int(width) <= 0 or int(height) <= 0 or int(x) < 0 or int(y) < 0:
            raise GimpMcpError('INVALID_ARGUMENT', 'crop width/height must be positive and x/y non-negative', False)
        body = _load(s.document)
        body += f"w={int(width)};h={int(height)};x={int(x)};y={int(y)}\n"
        body += "if x+w>img.get_width() or y+h>img.get_height(): raise RuntimeError('CROP_OUT_OF_BOUNDS')\nimg.crop(w,h,x,y)\n"
        body += _save(s.document) + "result={'width':img.get_width(),'height':img.get_height(),'x':x,'y':y}\nimg.delete()\n"
        return self._mutate(s, body)

    def document_autocrop(self, s: ArtworkSession, layer_id: int | None = None) -> dict[str, Any]:
        body = _load(s.document)
        if layer_id is None:
            body += "layers=img.get_layers()\nif not layers: raise RuntimeError('NO_LAYERS')\ndrawable=layers[0]\n"
        else:
            body += _layer_lookup(int(layer_id)) + "drawable=layer\n"
        body += "pdb=Gimp.get_pdb();proc=pdb.lookup_procedure('gimp-image-autocrop')\nif proc is None: raise RuntimeError('AUTOCROP_MISSING')\nconfig=proc.create_config();config.set_property('image',img);config.set_property('drawable',drawable);proc.run(config)\n"
        body += _save(s.document) + "result={'width':img.get_width(),'height':img.get_height()}\nimg.delete()\n"
        return self._mutate(s, body)

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

    def layer_duplicate(self, s: ArtworkSession, layer_id: int, name: str | None = None) -> dict[str, Any]:
        tattoo = random.randint(100000, 2_000_000_000)
        body = _load(s.document) + _layer_lookup(layer_id)
        body += f"new_name={_q(name or '')};tattoo={tattoo}\nposition=list(img.get_layers()).index(layer)\ndup=layer.copy()\nif dup is None: raise RuntimeError('LAYER_COPY_FAILED')\ndup.set_tattoo(tattoo)\nif new_name: dup.set_name(new_name)\nimg.insert_layer(dup,None,position)\n"
        body += _save(s.document) + "ok,ox,oy=dup.get_offsets()\nresult={'layer_id':tattoo,'name':dup.get_name(),'x':int(ox) if ok else 0,'y':int(oy) if ok else 0,'width':dup.get_width(),'height':dup.get_height()}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_resize(self, s: ArtworkSession, layer_id: int, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> dict[str, Any]:
        if int(width) <= 0 or int(height) <= 0:
            raise GimpMcpError('INVALID_ARGUMENT', 'layer width/height must be positive', False)
        body = _load(s.document) + _layer_lookup(layer_id)
        body += f"w={int(width)};h={int(height)};ox={int(offset_x)};oy={int(offset_y)}\nlayer.resize(w,h,ox,oy)\n"
        body += _save(s.document) + "result={'layer_id':layer_id,'width':layer.get_width(),'height':layer.get_height()}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_resize_to_image(self, s: ArtworkSession, layer_id: int) -> dict[str, Any]:
        body = _load(s.document) + _layer_lookup(layer_id)
        body += "layer.resize_to_image_size()\n" + _save(s.document)
        body += "result={'layer_id':layer_id,'width':layer.get_width(),'height':layer.get_height()}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_add_alpha(self, s: ArtworkSession, layer_id: int) -> dict[str, Any]:
        body = _load(s.document) + _layer_lookup(layer_id)
        body += "before=layer.has_alpha();layer.add_alpha();after=layer.has_alpha()\n" + _save(s.document)
        body += "result={'layer_id':layer_id,'had_alpha':bool(before),'has_alpha':bool(after)}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_merge_down(self, s: ArtworkSession, layer_id: int, merge_type: str = 'expand') -> dict[str, Any]:
        modes={'expand':'EXPAND_AS_NECESSARY','clip-image':'CLIP_TO_IMAGE','clip-bottom':'CLIP_TO_BOTTOM_LAYER'}
        key=str(merge_type or 'expand').strip().lower().replace('_','-')
        if key not in modes:
            raise GimpMcpError('INVALID_ARGUMENT', 'merge_type must be expand, clip-image, or clip-bottom', False)
        tattoo=random.randint(100000,2_000_000_000)
        body=_load(s.document)+_layer_lookup(layer_id)+f"mode=Gimp.MergeType.{modes[key]};tattoo={tattoo}\n"
        body += "merged=img.merge_down(layer,mode)\nif merged is None: raise RuntimeError('MERGE_DOWN_FAILED')\nmerged.set_tattoo(tattoo)\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'name':merged.get_name(),'merge_type':"+_q(key)+"}\nimg.delete()\n"
        return self._mutate(s, body)

    def layers_merge_visible(self, s: ArtworkSession, merge_type: str = 'expand') -> dict[str, Any]:
        modes={'expand':'EXPAND_AS_NECESSARY','clip-image':'CLIP_TO_IMAGE','clip-bottom':'CLIP_TO_BOTTOM_LAYER'}
        key=str(merge_type or 'expand').strip().lower().replace('_','-')
        if key not in modes:
            raise GimpMcpError('INVALID_ARGUMENT', 'merge_type must be expand, clip-image, or clip-bottom', False)
        tattoo=random.randint(100000,2_000_000_000)
        body=_load(s.document)+f"mode=Gimp.MergeType.{modes[key]};tattoo={tattoo}\nmerged=img.merge_visible_layers(mode)\nif merged is None: raise RuntimeError('MERGE_VISIBLE_FAILED')\nmerged.set_tattoo(tattoo)\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'name':merged.get_name(),'layer_count':len(img.get_layers()),'merge_type':"+_q(key)+"}\nimg.delete()\n"
        return self._mutate(s, body)

    def document_flatten(self, s: ArtworkSession) -> dict[str, Any]:
        tattoo=random.randint(100000,2_000_000_000)
        body=_load(s.document)+f"tattoo={tattoo}\nmerged=img.flatten()\nif merged is None: raise RuntimeError('FLATTEN_FAILED')\nmerged.set_tattoo(tattoo)\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'name':merged.get_name(),'layer_count':len(img.get_layers())}\nimg.delete()\n"
        return self._mutate(s, body)

    def layer_set(self, s: ArtworkSession, layer_id: int, *, name: str | None = None, visible: bool | None = None, opacity: float | None = None, x: int | None = None, y: int | None = None, blend_mode: str | None = None, lock_alpha: bool | None = None) -> dict[str, Any]:
        if opacity is not None and not 0 <= opacity <= 100:
            raise GimpMcpError('INVALID_ARGUMENT', 'opacity must be 0..100', False)
        if self._live_ready() and x is None and y is None and blend_mode is None and lock_alpha is None:
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
        if x is not None or y is not None:
            body += "ok,curx,cury=layer.get_offsets()\n"
            body += f"layer.set_offsets({int(x) if x is not None else 'int(curx)'},{int(y) if y is not None else 'int(cury)'})\n"
        if blend_mode is not None:
            aliases={'normal':'NORMAL','multiply':'MULTIPLY','screen':'SCREEN','overlay':'OVERLAY','addition':'ADDITION','subtract':'SUBTRACT','darken':'DARKEN_ONLY','lighten':'LIGHTEN_ONLY'}
            key=str(blend_mode).strip().lower().replace('-','_').replace(' ','_'); enum_name=aliases.get(key,key.upper())
            body += f"mode=getattr(Gimp.LayerMode,{_q(enum_name)},None)\nif mode is None: raise RuntimeError('UNSUPPORTED_BLEND_MODE:'+{_q(enum_name)})\nlayer.set_mode(mode)\n"
        if lock_alpha is not None: body += f"layer.set_lock_alpha({bool(lock_alpha)})\n"
        body += _save(s.document) + "ok,ox,oy=layer.get_offsets()\nresult={'layer_id':layer_id,'name':layer.get_name(),'visible':layer.get_visible(),'opacity':layer.get_opacity(),'x':int(ox) if ok else 0,'y':int(oy) if ok else 0,'blend_mode':str(layer.get_mode().value_nick),'lock_alpha':bool(layer.get_lock_alpha())}\nimg.delete()\n"
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
        action=str(action).strip().lower().replace('_','-')
        body = _load(s.document)
        if action == 'none': body += "ok=Gimp.Selection.none(img)\n"
        elif action == 'all': body += "ok=Gimp.Selection.all(img)\n"
        elif action == 'invert': body += "ok=Gimp.Selection.invert(img)\n"
        elif action in {'rectangle','ellipse'}:
            op_name=str(kwargs.get('operation','replace')).strip().lower()
            op_map={'replace':'REPLACE','add':'ADD','subtract':'SUBTRACT','intersect':'INTERSECT'}
            if op_name not in op_map: raise GimpMcpError('INVALID_ARGUMENT','selection operation must be replace/add/subtract/intersect',False)
            x=float(kwargs.get('x',0)); y=float(kwargs.get('y',0)); w=float(kwargs.get('width',0)); h=float(kwargs.get('height',0))
            if w <= 0 or h <= 0: raise GimpMcpError('INVALID_ARGUMENT','selection width/height must be positive',False)
            method='select_rectangle' if action=='rectangle' else 'select_ellipse'
            body += f"ok=img.{method}(Gimp.ChannelOps.{op_map[op_name]},{x},{y},{w},{h})\n"
        elif action == 'polygon':
            op_name=str(kwargs.get('operation','replace')).strip().lower()
            op_map={'replace':'REPLACE','add':'ADD','subtract':'SUBTRACT','intersect':'INTERSECT'}
            if op_name not in op_map: raise GimpMcpError('INVALID_ARGUMENT','selection operation must be replace/add/subtract/intersect',False)
            points=kwargs.get('points')
            if not isinstance(points,(list,tuple)) or len(points) < 6 or len(points) % 2 or any(not isinstance(v,(int,float)) or isinstance(v,bool) for v in points):
                raise GimpMcpError('INVALID_ARGUMENT','polygon points must be [x1,y1,x2,y2,x3,y3,...]',False)
            body += f"ok=img.select_polygon(Gimp.ChannelOps.{op_map[op_name]},{[float(v) for v in points]!r})\n"
        elif action == 'feather': body += f"ok=Gimp.Selection.feather(img,{float(kwargs.get('radius',0))})\n"
        elif action == 'grow': body += f"ok=Gimp.Selection.grow(img,{int(kwargs.get('steps',0))})\n"
        elif action == 'shrink': body += f"ok=Gimp.Selection.shrink(img,{int(kwargs.get('steps',0))})\n"
        elif action == 'border': body += f"ok=Gimp.Selection.border(img,{int(kwargs.get('radius',0))})\n"
        elif action == 'translate': body += f"ok=Gimp.Selection.translate(img,{int(kwargs.get('dx',0))},{int(kwargs.get('dy',0))})\n"
        else: raise GimpMcpError('UNSUPPORTED_OPERATION', f'Unknown selection action: {action}', False)
        body += _save(s.document) + f"result={{'action':{_q(action)},'ok':bool(ok)}}\nimg.delete()\n"
        return self._mutate(s, body)

    def guide_add(self, s: ArtworkSession, orientation: str, position: int) -> dict[str, Any]:
        orientation=str(orientation).strip().lower()
        if orientation not in {'horizontal','vertical'}:
            raise GimpMcpError('INVALID_ARGUMENT','orientation must be horizontal or vertical',False)
        body=_load(s.document)+f"position={int(position)}\n"
        body += "guide=img.add_hguide(position)\n" if orientation=='horizontal' else "guide=img.add_vguide(position)\n"
        body += _save(s.document) + f"result={{'guide_id':int(guide),'orientation':{_q(orientation)},'position':position}}\nimg.delete()\n"
        return self._mutate(s, body)

    def guide_list(self, s: ArtworkSession) -> dict[str, Any]:
        body = _load(s.document)
        body += "items=[];gid=img.find_next_guide(0)\n"
        body += "while gid:\n orient=img.get_guide_orientation(gid);pos=img.get_guide_position(gid);items.append({'guide_id':int(gid),'orientation':str(orient.value_nick),'position':int(pos)});gid=img.find_next_guide(gid)\n"
        body += "result={'count':len(items),'guides':items}\nimg.delete()\n"
        return self.bridge.run_json(body)

    def guide_delete(self, s: ArtworkSession, guide_id: int) -> dict[str, Any]:
        body=_load(s.document)+f"gid={int(guide_id)};img.delete_guide(gid)\n"+_save(s.document)+"result={'deleted':True,'guide_id':gid}\nimg.delete()\n"
        return self._mutate(s, body)

    def import_layer(self, s: ArtworkSession, source: Path, name: str | None = None) -> dict[str, Any]:
        tattoo=random.randint(100000,2_000_000_000)
        body=_load(s.document)+f"source={_q(source)};new_name={_q(name or '')};tattoo={tattoo}\npdb=Gimp.get_pdb();proc=pdb.lookup_procedure('gimp-file-load-layer')\nif proc is None: raise RuntimeError('GIMP_FILE_LOAD_LAYER_MISSING')\nconfig=proc.create_config();config.set_property('run-mode',Gimp.RunMode.NONINTERACTIVE);config.set_property('image',img);config.set_property('file',Gio.File.new_for_path(source))\nret=proc.run(config)\nlayer=ret.index(1) if ret.length()>1 else None\nif layer is None: raise RuntimeError('IMPORT_LAYER_FAILED')\nlayer.set_tattoo(tattoo)\nif new_name: layer.set_name(new_name)\nimg.insert_layer(layer,None,0)\n"
        body += _save(s.document)+"ok,ox,oy=layer.get_offsets()\nresult={'layer_id':tattoo,'name':layer.get_name(),'x':int(ox) if ok else 0,'y':int(oy) if ok else 0,'width':layer.get_width(),'height':layer.get_height(),'source':source}\nimg.delete()\n"
        return self._mutate(s, body)

    def text_create(self, s: ArtworkSession, text: str, x: float, y: float, size: float, font_name: str = 'Sans', color: str | None = None) -> dict[str, Any]:
        if not 1 <= size <= 2000: raise GimpMcpError('INVALID_ARGUMENT', 'font size must be 1..2000 px', False)
        if color is not None and (not str(color).strip() or len(str(color)) > 128):
            raise GimpMcpError('INVALID_ARGUMENT', 'invalid text color', False)
        tattoo = random.randint(100000, 2_000_000_000)
        body = _load(s.document) + f"text={_q(text)};font_name={_q(font_name)};size={float(size)};x={float(x)};y={float(y)};tattoo={tattoo};color_text={_q(color or '')}\n"
        body += "font=Gimp.Font.get_by_name(font_name)\nif font is None:\n font=Gimp.context_get_font()\nif font is None: raise RuntimeError('FONT_NOT_FOUND')\n"
        body += "img.undo_group_start()\nlayer=Gimp.TextLayer.new(img,text,font,size,Gimp.Unit.pixel());layer.set_tattoo(tattoo);img.insert_layer(layer,None,0);layer.set_offsets(int(x),int(y))\nif color_text: layer.set_color(Gegl.Color.new(color_text))\nimg.undo_group_end()\n"
        body += _save(s.document) + "result={'layer_id':tattoo,'text':text,'font':font_name,'size':size,'x':x,'y':y,'color':color_text or None}\nimg.delete()\n"
        return self._mutate(s, body)

    def text_update(self, s: ArtworkSession, layer_id: int, *, text: str | None = None, size: float | None = None, font_name: str | None = None, color: str | None = None) -> dict[str, Any]:
        if size is not None and not 1 <= float(size) <= 2000:
            raise GimpMcpError('INVALID_ARGUMENT', 'font size must be 1..2000 px', False)
        body = _load(s.document) + _layer_lookup(layer_id)
        body += "if not isinstance(layer,Gimp.TextLayer): raise RuntimeError('NOT_TEXT_LAYER')\n"
        if text is not None: body += f"layer.set_text({_q(text)})\n"
        if font_name is not None:
            body += f"font=Gimp.Font.get_by_name({_q(font_name)})\nif font is None: raise RuntimeError('FONT_NOT_FOUND')\nlayer.set_font(font)\n"
        if size is not None: body += f"layer.set_font_size({float(size)},Gimp.Unit.pixel())\n"
        if color is not None: body += f"layer.set_color(Gegl.Color.new({_q(color)}))\n"
        body += _save(s.document)
        body += "size_info=layer.get_font_size();size_px=float(size_info[0]) if isinstance(size_info,(tuple,list)) else float(size_info)\nresult={'layer_id':layer_id,'text':layer.get_text(),'size':size_px,'font':layer.get_font().get_name() if layer.get_font() else None}\nimg.delete()\n"
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

    @staticmethod
    def _normalize_filter_parameters(operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(parameters, dict):
            raise GimpMcpError("INVALID_ARGUMENT", "filter parameters must be an object", False)
        op = str(operation).strip().lower()
        params = dict(parameters)
        aliases: dict[str, dict[str, str]] = {
            "gegl:brightness-contrast": {"brightness_amount": "brightness", "contrast_amount": "contrast"},
            "gegl:unsharp-mask": {"radius": "std-dev", "sigma": "std-dev", "amount": "scale"},
            "gegl:color-temperature": {"original_temperature": "original-temperature", "source_temperature": "original-temperature", "temperature": "intended-temperature", "target_temperature": "intended-temperature"},
            "gegl:shadows-highlights": {"white_point": "whitepoint", "shadow": "shadows", "highlight": "highlights"},
        }
        for old, new in aliases.get(op, {}).items():
            if old in params and new not in params:
                params[new] = params.pop(old)
        if op == "gegl:brightness-contrast":
            if "brightness_percent" in params and "brightness" not in params:
                params["brightness"] = max(-100.0, min(100.0, float(params.pop("brightness_percent")))) / 100.0
            if "contrast_percent" in params and "contrast" not in params:
                params["contrast"] = 1.0 + max(-100.0, min(100.0, float(params.pop("contrast_percent")))) / 100.0
            # Some models express both controls as familiar percentages (e.g. 5/5).
            # Brightness outside GEGL's legal -3..3 range is an unambiguous signal.
            try:
                b = float(params.get("brightness")) if "brightness" in params else None
            except (TypeError, ValueError):
                b = None
            if b is not None and abs(b) > 3.0 and abs(b) <= 100.0:
                params["brightness"] = b / 100.0
                if "contrast" in params:
                    try:
                        c = float(params["contrast"])
                        if -100.0 <= c <= 100.0:
                            params["contrast"] = 1.0 + c / 100.0
                    except (TypeError, ValueError):
                        pass
        if op == "gegl:gaussian-blur":
            # Models commonly call the single conceptual blur control radius/size/sigma.
            scalar = None
            for key in ("radius", "size", "sigma", "std-dev", "std_dev"):
                if key in params:
                    scalar = params.pop(key)
                    break
            if scalar is not None:
                params.setdefault("std-dev-x", scalar)
                params.setdefault("std-dev-y", scalar)
            if "std_dev_x" in params and "std-dev-x" not in params:
                params["std-dev-x"] = params.pop("std_dev_x")
            if "std_dev_y" in params and "std-dev-y" not in params:
                params["std-dev-y"] = params.pop("std_dev_y")
        return params

    def filter_apply(self, s: ArtworkSession, layer_id: int, operation: str, parameters: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        operation = str(operation).strip().lower().replace("_", "-")
        if not operation.startswith("gegl:"):
            operation = "gegl:" + operation
        params = self._normalize_filter_parameters(operation, parameters)
        body = f"path={_q(s.document)}\nimg=Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,Gio.File.new_for_path(path))\n"
        body += "flt=None\nappended=False\ntry:\n"
        body += f" layer_id={int(layer_id)}\n"
        body += " layer=next((x for x in img.get_layers() if int(x.get_tattoo())==layer_id),None)\n if layer is None: raise RuntimeError('LAYER_NOT_FOUND')\n"
        body += f" operation={_q(operation)}\n params=json.loads({_q(json.dumps(params, ensure_ascii=False))})\n filter_name={_q(name or operation)}\n"
        body += " flt=Gimp.DrawableFilter.new(layer,operation,filter_name)\n if flt is None: raise RuntimeError('FILTER_NOT_FOUND:'+operation)\n"
        body += " config=flt.get_config()\n valid={spec.name for spec in config.list_properties()}\n unknown=sorted(set(params)-valid)\n"
        body += " if unknown: raise RuntimeError('UNSUPPORTED_FILTER_PROPERTIES:'+operation+':'+','.join(unknown)+'; valid='+','.join(sorted(valid)))\n"
        body += " for key,value in params.items(): config.set_property(key,value)\n flt.update()\n layer.append_filter(flt)\n appended=True\n"
        body += f" Gimp.file_save(Gimp.RunMode.NONINTERACTIVE,img,Gio.File.new_for_path({_q(s.document)}),None)\n"
        body += " result={'layer_id':layer_id,'operation':operation,'name':filter_name,'parameters':params,'filter_count':len(layer.get_filters())}\n"
        body += "finally:\n if flt is not None and not appended and flt.is_valid(): flt.delete()\n if img is not None and img.is_valid(): img.delete()\n"
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

    def pdb_call(self, s: ArtworkSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a GIMP PDB procedure with typed JSON-safe argument coercion.

        JSON primitives pass through. Session image references bind automatically;
        item-like values use persistent tattoo IDs; colors accept CSS/Gegl strings;
        enum values accept their normal symbolic names; common GIMP resources accept
        resource names. GFile remains sandboxed to the session workdir.
        """
        if not isinstance(arguments, dict):
            raise GimpMcpError('INVALID_ARGUMENT', 'arguments must be an object', False)
        body = _load(s.document) + f"name={_q(name)};args=json.loads({_q(json.dumps(arguments, ensure_ascii=False))});session_root={_q(s.workdir.resolve())}\n"
        body += "pdb=Gimp.get_pdb();proc=pdb.lookup_procedure(name)\nif proc is None: raise RuntimeError('PDB_PROCEDURE_NOT_FOUND:'+name)\nconfig=proc.create_config();specs={x.name:x for x in proc.get_arguments()}\n"
        body += "unknown=sorted(set(args)-set(specs))\nif unknown: raise RuntimeError('UNKNOWN_PDB_ARGUMENTS:'+','.join(unknown))\n"
        body += (
            "def _all_layers(items):\n"
            " out=[]\n"
            " for item in items:\n"
            "  out.append(item)\n"
            "  try:\n"
            "   children=item.get_children()\n"
            "   if children: out.extend(_all_layers(children))\n"
            "  except Exception: pass\n"
            " return out\n"
            "def _by_tattoo(items,tattoo):\n"
            " for item in items:\n"
            "  try:\n"
            "   if int(item.get_tattoo())==tattoo: return item\n"
            "  except Exception: pass\n"
            " return None\n"
            "all_layers=_all_layers(list(img.get_layers()))\n"
            "for key,value in args.items():\n"
            " spec=specs[key];tn=spec.value_type.name\n"
            " if tn=='GimpImage': value=img\n"
            " elif tn in ('GimpDrawable','GimpLayer','GimpItem','GimpTextLayer','GimpVectorLayer','GimpGroupLayer','GimpLinkLayer','GimpRasterizable'):\n"
            "  lid=int(value);value=_by_tattoo(all_layers,lid)\n"
            "  if value is None: raise RuntimeError('ITEM_NOT_FOUND:'+str(lid))\n"
            " elif tn in ('GimpChannel','GimpLayerMask'):\n"
            "  iid=int(value);pool=list(img.get_channels())+all_layers;value=_by_tattoo(pool,iid)\n"
            "  if value is None: raise RuntimeError('CHANNEL_OR_MASK_NOT_FOUND:'+str(iid))\n"
            " elif tn=='GimpPath':\n"
            "  iid=int(value);value=_by_tattoo(list(img.get_paths()),iid)\n"
            "  if value is None: raise RuntimeError('PATH_NOT_FOUND:'+str(iid))\n"
            " elif tn=='GFile':\n"
            "  candidate=os.path.realpath(os.path.expanduser(str(value)))\n"
            "  if os.path.commonpath([session_root,candidate]) != session_root: raise RuntimeError('PDB_GFILE_OUTSIDE_SESSION_WORKDIR')\n"
            "  value=Gio.File.new_for_path(candidate)\n"
            " elif tn=='GimpRunMode': value=getattr(Gimp.RunMode,str(value).upper().replace('-','_'),Gimp.RunMode.NONINTERACTIVE)\n"
            " elif tn=='GeglColor':\n"
            "  value=Gegl.Color.new(str(value))\n"
            "  if value is None: raise RuntimeError('INVALID_COLOR')\n"
            " elif tn in ('GimpBrush','GimpFont','GimpGradient','GimpPalette','GimpPattern'):\n"
            "  cls=getattr(Gimp,tn[4:]);value=cls.get_by_name(str(value))\n"
            "  if value is None: raise RuntimeError('RESOURCE_NOT_FOUND:'+tn+':'+str(args[key]))\n"
            " elif tn.startswith('Gimp') and isinstance(value,str):\n"
            "  cls=getattr(Gimp,tn[4:],None)\n"
            "  if cls is not None:\n"
            "   enum_name=value.strip().upper().replace('-','_').replace(' ','_')\n"
            "   candidate=getattr(cls,enum_name,None)\n"
            "   if candidate is not None: value=candidate\n"
            " elif tn.startswith('Gegl') and isinstance(value,str):\n"
            "  cls=getattr(Gegl,tn[4:],None)\n"
            "  if cls is not None:\n"
            "   enum_name=value.strip().upper().replace('-','_').replace(' ','_')\n"
            "   candidate=getattr(cls,enum_name,None)\n"
            "   if candidate is not None: value=candidate\n"
            " config.set_property(key,value)\n"
        )
        body += "ret=proc.run(config);vals=[]\nfor i in range(ret.length()):\n v=ret.index(i);vals.append(v if isinstance(v,(str,int,float,bool,type(None))) else str(v))\n"
        body += _save(s.document) + "result={'procedure':name,'returns':vals}\nimg.delete()\n"
        return self._mutate(s, body)

    def pdb_describe(self, name: str) -> dict[str, Any]:
        return self.bridge.run_json(f"name={_q(name)}\npdb=Gimp.get_pdb();proc=pdb.lookup_procedure(name)\nif proc is None: result={{'found':False,'name':name}}\nelse:\n args=[{{'name':s.name,'type':s.value_type.name,'blurb':getattr(s,'blurb','')}} for s in proc.get_arguments()]\n rets=[{{'name':s.name,'type':s.value_type.name,'blurb':getattr(s,'blurb','')}} for s in proc.get_return_values()]\n result={{'found':True,'name':name,'arguments':args,'returns':rets}}\n")
