from __future__ import annotations

import logging, shutil, time
from pathlib import Path
from typing import Any, Literal
from mcp.server import MCPServer
from .bridge import GimpBridge
from .config import Settings
from .errors import GimpMcpError
from .events import EventBus
from .ai_control import AICoderAdapter, AIControlError
from .operations import GimpOperations
from .security import PathPolicy
from .sessions import SessionManager

settings = Settings()
policy = PathPolicy(settings.allowed_roots)
bridge = GimpBridge(settings.gimp_executable, settings.timeout)
sessions = SessionManager(settings.session_root)
ops = GimpOperations(bridge, sessions)
events = EventBus(settings.state_dir / "events.jsonl")
ai_control = AICoderAdapter()

mcp = MCPServer('GIMP MCP', version='0.1.0', instructions='Structured GIMP 3 artwork editing. Create/open a session first, use semantic tools, render previews to inspect progress, and use filter_list/filter_describe before unfamiliar GEGL effects.')


def _ok(data: Any) -> dict[str, Any]:
    return {'ok': True, 'data': data}

def _call(fn, *args, **kwargs):
    session_id = None
    if args and hasattr(args[0], "session_id"):
        session_id = getattr(args[0], "session_id", None)
    operation = getattr(fn, "__name__", str(fn))
    started = time.monotonic()
    events.emit("operation.started", session_id=session_id, operation=operation, status="running")
    try:
        result = _ok(fn(*args, **kwargs))
        events.emit("operation.completed", session_id=session_id, operation=operation, status="ok", duration_ms=round((time.monotonic()-started)*1000, 2))
        return result
    except GimpMcpError as exc:
        events.emit("operation.failed", session_id=session_id, operation=operation, status="error", duration_ms=round((time.monotonic()-started)*1000, 2), details={"code": exc.code, "message": str(exc)})
        return exc.as_dict()
    except Exception as exc:
        events.emit("operation.failed", session_id=session_id, operation=operation, status="error", duration_ms=round((time.monotonic()-started)*1000, 2), details={"code":"INTERNAL_ERROR","message":str(exc)})
        return {'ok': False, 'error': {'code': 'INTERNAL_ERROR', 'message': str(exc), 'recoverable': True}}

@mcp.tool()
def gimp_status() -> dict[str, Any]:
    '''Verify the real GIMP binary, Python GI bridge and PDB availability. Read-only. Use this first when diagnosing setup problems.'''
    return _call(bridge.probe)

@mcp.tool()
def session_list() -> dict[str, Any]:
    """List active and recovered artwork sessions, including preview paths and undo depth."""
    return _ok(sessions.list())

@mcp.tool()
def events_recent(limit: int = 100, session_id: str | None = None) -> dict[str, Any]:
    """Return recent live operation events for observers and control UIs."""
    return _ok(events.recent(limit, session_id=session_id))

@mcp.tool()
def provider_status() -> dict[str, Any]:
    """Report optional official-provider account status through the shared AICoder adapter. No credentials are returned."""
    try:
        return _ok(ai_control.providers())
    except AIControlError as exc:
        return {'ok': False, 'error': {'code': 'AI_CONTROL_UNAVAILABLE', 'message': str(exc), 'recoverable': True}}

@mcp.tool()
def provider_models(provider: str) -> dict[str, Any]:
    """List models exposed by an already-linked ChatGPT, Claude, Gemini or Mistral account."""
    try:
        return _ok(ai_control.models(provider))
    except Exception as exc:
        return {'ok': False, 'error': {'code': 'PROVIDER_ERROR', 'message': str(exc), 'recoverable': True}}

@mcp.tool()
def triforce_status() -> dict[str, Any]:
    """Report the existing AILinux/TriForce login reused from AICoder without exposing its bearer token."""
    return _ok(ai_control.triforce_status())

@mcp.tool()
def triforce_models() -> dict[str, Any]:
    """List models available to the currently logged-in AILinux/TriForce account."""
    try:
        return _ok(ai_control.triforce_models())
    except Exception as exc:
        return {'ok': False, 'error': {'code': 'TRIFORCE_ERROR', 'message': str(exc), 'recoverable': True}}

@mcp.tool()
def session_create(width: int = 1024, height: int = 768, background_name: str = 'Background') -> dict[str, Any]:
    '''Create a new isolated artwork session and XCF working document. Returns a session_id used by all editing tools.'''
    s = sessions.create()
    result = _call(ops.document_create, s, width, height, background_name)
    if result.get('ok'): result['data']['session_id'] = s.session_id
    return result

@mcp.tool()
def session_open(path: str) -> dict[str, Any]:
    '''Open an existing image/XCF into an isolated session copy so source files are never edited in-place.'''
    try:
        src = policy.resolve(path, must_exist=True); s = sessions.create(src)
        bridge.run_json(f"src={src.as_posix()!r};dst={s.document.as_posix()!r}\nimg=Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,Gio.File.new_for_path(src))\nGimp.file_save(Gimp.RunMode.NONINTERACTIVE,img,Gio.File.new_for_path(dst),None)\nresult={{'copied':dst}}\nimg.delete()\n")
        info = ops.document_info(s); info['session_id'] = s.session_id; info['source'] = str(src)
        return _ok(info)
    except GimpMcpError as exc: return exc.as_dict()
    except Exception as exc: return {'ok':False,'error':{'code':'GIMP_START_FAILED','message':str(exc),'recoverable':True}}

@mcp.tool()
def session_info(session_id: str) -> dict[str, Any]:
    '''Return dimensions, layers and current XCF state for an artwork session without modifying it.'''
    return _call(ops.document_info, sessions.get(session_id))

@mcp.tool()
def session_undo(session_id: str) -> dict[str, Any]:
    '''Undo the last successful mutating MCP operation using the session snapshot journal.'''
    try:
        s=sessions.get(session_id)
        with s.lock: changed=sessions.undo(s)
        return _ok({'undone':changed})
    except GimpMcpError as exc: return exc.as_dict()

@mcp.tool()
def session_redo(session_id: str) -> dict[str, Any]:
    '''Redo the last snapshot-level undo in this session.'''
    try:
        s=sessions.get(session_id)
        with s.lock: changed=sessions.redo(s)
        return _ok({'redone':changed})
    except GimpMcpError as exc: return exc.as_dict()

@mcp.tool()
def session_close(session_id: str, discard: bool = False) -> dict[str, Any]:
    '''Close a session. Set discard=true to delete its temporary XCF and history.'''
    return _call(sessions.close, session_id, discard=discard)

@mcp.tool()
def document_save_as(session_id: str, output_xcf: str, overwrite: bool = False) -> dict[str, Any]:
    '''Commit the current session document to an allowed XCF path. Refuses overwrites unless explicitly enabled.'''
    try:
        s=sessions.get(session_id); dst=policy.ensure_parent(output_xcf)
        if dst.exists() and not overwrite: raise GimpMcpError('INVALID_PATH',f'Output exists: {dst}',True)
        shutil.copy2(s.document,dst); return _ok({'saved':str(dst)})
    except GimpMcpError as exc: return exc.as_dict()

@mcp.tool()
def layer_create(session_id: str, name: str = 'Layer', width: int | None = None, height: int | None = None) -> dict[str, Any]:
    '''Create a transparent layer. Width/height default to canvas dimensions. Returns a persistent GIMP tattoo-based layer_id.'''
    return _call(ops.layer_create, sessions.get(session_id), name, width, height)

@mcp.tool()
def layer_delete(session_id: str, layer_id: int) -> dict[str, Any]:
    '''Delete one layer identified by layer_id. The operation is snapshot-undoable.'''
    return _call(ops.layer_delete, sessions.get(session_id), layer_id)

@mcp.tool()
def layer_update(session_id: str, layer_id: int, name: str | None = None, visible: bool | None = None, opacity: float | None = None) -> dict[str, Any]:
    '''Rename a layer and/or set visibility/opacity. Opacity is percent 0..100.'''
    return _call(ops.layer_set, sessions.get(session_id), layer_id, name=name, visible=visible, opacity=opacity)

@mcp.tool()
def layer_reorder(session_id: str, layer_id: int, position: int) -> dict[str, Any]:
    '''Move a layer to a zero-based stack position; 0 is the top of the layer stack.'''
    return _call(ops.layer_reorder, sessions.get(session_id), layer_id, position)

@mcp.tool()
def selection_set(session_id: str, action: Literal['none','all','invert','rectangle'], x: float = 0, y: float = 0, width: float = 0, height: float = 0) -> dict[str, Any]:
    '''Set the current selection. rectangle uses pixel coordinates and dimensions; other actions ignore geometry.'''
    return _call(ops.selection, sessions.get(session_id), action, x=x, y=y, width=width, height=height)

@mcp.tool()
def text_create(session_id: str, text: str, x: float, y: float, size: float = 64, font_name: str = 'Sans') -> dict[str, Any]:
    '''Create an editable GIMP text layer at pixel position x/y. Size is pixels; returns the new layer_id.'''
    return _call(ops.text_create, sessions.get(session_id), text, x, y, size, font_name)

@mcp.tool()
def transform_layer(session_id: str, layer_id: int, action: Literal['translate','rotate','scale'], values: list[float]) -> dict[str, Any]:
    '''Transform one layer. translate=[dx,dy] pixels; rotate=[degrees] or [degrees,cx,cy]; scale=[x0,y0,x1,y1].'''
    return _call(ops.transform, sessions.get(session_id), layer_id, action, values)

@mcp.tool()
def filter_list(query: str = '', limit: int = 100) -> dict[str, Any]:
    '''Discover available GEGL operations. Use this before filter_describe/filter_apply when the exact operation name is unknown.'''
    return _call(ops.filter_list, query, limit)

@mcp.tool()
def filter_describe(operation: str) -> dict[str, Any]:
    '''Describe a GEGL operation's configurable properties so an AI can construct a valid filter_apply call.'''
    return _call(ops.filter_describe, operation)

@mcp.tool()
def filter_apply(session_id: str, layer_id: int, operation: str, parameters: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    '''Apply a GEGL operation as a non-destructive GIMP DrawableFilter to a layer. Discover parameters first if uncertain.'''
    return _call(ops.filter_apply, sessions.get(session_id), layer_id, operation, parameters, name)

@mcp.tool()
def export_image(session_id: str, output_path: str, overwrite: bool = False) -> dict[str, Any]:
    '''Export the session to PNG/JPEG/WebP or another GIMP-supported format inferred from the extension.'''
    try:
        s=sessions.get(session_id); dst=policy.ensure_parent(output_path)
        if dst.exists() and not overwrite: raise GimpMcpError('INVALID_PATH',f'Output exists: {dst}',True)
        return _ok(ops.export(s,dst))
    except GimpMcpError as exc: return exc.as_dict()

@mcp.tool()
def preview_render(session_id: str, max_width: int = 1200, max_height: int = 1200) -> dict[str, Any]:
    '''Render a bounded PNG preview of the current artwork for vision review. Returns a gimp-preview resource URI.'''
    try:
        s=sessions.get(session_id); max_width=max(1,min(max_width,settings.preview_max)); max_height=max(1,min(max_height,settings.preview_max)); out=s.workdir/'preview.png'
        started=time.monotonic(); events.emit('preview.started', session_id=session_id, operation='preview_render', status='running')
        data=ops.export(s,out,max_width,max_height); data['resource_uri']=f'gimp-preview://{session_id}'; events.emit('preview.ready', session_id=session_id, operation='preview_render', status='ok', duration_ms=round((time.monotonic()-started)*1000,2), details={'path':str(out),'width':data.get('width'),'height':data.get('height')}); return _ok(data)
    except GimpMcpError as exc: return exc.as_dict()

@mcp.resource('gimp-preview://{session_id}', mime_type='image/png', name='Artwork preview')
def preview_resource(session_id: str) -> bytes:
    '''Binary PNG preview generated by preview_render.'''
    s=sessions.get(session_id); path=s.workdir/'preview.png'
    if not path.exists(): raise FileNotFoundError('Run preview_render first')
    return path.read_bytes()

@mcp.tool()
def pdb_search(query: str = '', limit: int = 100) -> dict[str, Any]:
    '''Expert discovery: search GIMP PDB procedure names without exporting thousands of procedures as MCP tools.'''
    return _call(ops.pdb_search, query, limit)

@mcp.tool()
def pdb_describe(name: str) -> dict[str, Any]:
    '''Expert discovery: inspect the arguments and returns of one GIMP PDB procedure. This tool is read-only.'''
    return _call(ops.pdb_describe, name)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()

if __name__ == '__main__': main()
