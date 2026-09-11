#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
import traceback
from pathlib import Path

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp, Gio, GLib

PROC = 'plug-in-gimp-mcp-live'
SOCKET_PATH = Path(os.environ.get('GIMP_MCP_LIVE_SOCKET', str(Path.home() / '.local/state/gimp-mcp/gimp-live.sock')))


class GimpMcpLive(Gimp.PlugIn):
    def __init__(self):
        super().__init__()
        self._loop = None
        self._listener = None
        self._thread = None
        self._image = None
        self._images = {}
        self._headless = os.environ.get("GIMP_MCP_HEADLESS") == "1"
        self._lock = threading.Lock()

    def do_set_i18n(self, procedure_name):
        # This plug-in currently ships English-only UI strings and no gettext catalog.
        # Explicitly disable localization so GIMP does not probe a non-existent locale dir.
        return False, None, None

    def do_query_procedures(self):
        if os.environ.get('GIMP_MCP_BATCH') == '1':
            return []
        return [PROC]

    def do_create_procedure(self, name):
        if name != PROC:
            return None
        proc = Gimp.Procedure.new(self, name, Gimp.PDBProcType.PERSISTENT, self._run, None)
        proc.set_documentation(
            'GIMP MCP persistent live bridge',
            'Keeps a local Unix socket open so GIMP MCP can mirror artwork into a visible GIMP display.',
            name,
        )
        proc.set_attribution('AILinux', 'AILinux', '2026')
        return proc

    def _run(self, procedure, *args):
        if os.environ.get('GIMP_MCP_BATCH') == '1':
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            SOCKET_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        self.persistent_enable()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, stat.S_IRUSR | stat.S_IWUSR)
        self._listener.listen(8)
        self._listener.settimeout(1.0)
        self._thread = threading.Thread(target=self._accept_loop, name='gimp-mcp-live', daemon=True)
        self._thread.start()
        procedure.persistent_ready()
        self._loop = GLib.MainLoop()
        try:
            self._loop.run()
        finally:
            try:
                self._listener.close()
            except Exception:
                pass
            try:
                SOCKET_PATH.unlink(missing_ok=True)
            except OSError:
                pass
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)

    def _accept_loop(self):
        while self._listener is not None:
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn):
        with conn:
            data = b''
            while b'\n' not in data and len(data) < 1024 * 1024:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            try:
                payload = json.loads(data.split(b'\n', 1)[0].decode('utf-8'))
            except Exception as exc:
                self._send(conn, {'ok': False, 'error': f'invalid request: {exc}'})
                return
            done = threading.Event()
            result = {}

            def execute():
                try:
                    result.update(self._handle(payload))
                except Exception as exc:
                    result.update({'ok': False, 'error': str(exc), 'traceback': traceback.format_exc(limit=5)})
                finally:
                    done.set()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(execute)
            if not done.wait(30):
                self._send(conn, {'ok': False, 'error': 'GIMP main-loop timeout'})
                return
            self._send(conn, result)

    @staticmethod
    def _send(conn, payload):
        conn.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))

    def _image_for_payload(self, payload, *, required=True):
        raw = str(payload.get('path') or '').strip()
        if raw:
            key = str(Path(raw).expanduser().resolve())
            image = self._images.get(key)
            if image is not None and not image.is_valid():
                self._images.pop(key, None); image = None
            if image is None and Path(key).is_file():
                image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(key))
                if image is not None:
                    self._images[key] = image
            if image is not None:
                self._image = image
                return image
        image = self._image
        if image is not None and not image.is_valid():
            self._image = None; image = None
        if required and image is None:
            raise RuntimeError('no GIMP workspace image is open')
        return image

    def _handle(self, payload):
        command = str(payload.get('command') or '')
        if command == 'ping':
            return {'ok': True, 'mode': 'headless' if self._headless else 'persistent', 'headless': self._headless, 'gimp_version': str(Gimp.version()), 'socket': str(SOCKET_PATH), 'documents': len(self._images)}
        if command == 'active_info':
            image = self._image
            if image is not None and not image.is_valid():
                self._image = None
                image = None
            return {
                'ok': True,
                'image': None if image is None else {
                    'width': image.get_width(), 'height': image.get_height(),
                    'layers': [x.get_name() for x in image.get_layers()],
                },
            }
        if command == 'vision_snapshot':
            image = self._image_for_payload(payload)
            output = Path(str(payload.get('output') or '')).expanduser().resolve()
            if output.suffix.lower() != '.png':
                raise RuntimeError('vision snapshot output must be .png')
            output.parent.mkdir(parents=True, exist_ok=True)
            duplicate = image.duplicate()
            if duplicate is None:
                raise RuntimeError('could not duplicate live image')
            try:
                Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, duplicate, Gio.File.new_for_path(str(output)), None)
            finally:
                if duplicate.is_valid():
                    duplicate.delete()
            info_layers = []
            for layer in image.get_layers():
                ook, ox, oy = layer.get_offsets()
                Gimp.Selection.none(image)
                image.select_item(Gimp.ChannelOps.REPLACE, layer)
                bok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
                info_layers.append({
                    'layer_id': int(layer.get_tattoo()), 'name': layer.get_name(),
                    'x': int(ox) if ook else 0, 'y': int(oy) if ook else 0,
                    'width': layer.get_width(), 'height': layer.get_height(),
                    'content_x': int(x1) if bok and non_empty else None,
                    'content_y': int(y1) if bok and non_empty else None,
                    'content_width': int(x2-x1) if bok and non_empty else 0,
                    'content_height': int(y2-y1) if bok and non_empty else 0,
                    'visible': layer.get_visible(), 'opacity': layer.get_opacity(),
                })
            Gimp.Selection.none(image)
            return {
                'ok': True, 'command': command, 'output': str(output),
                'width': image.get_width(), 'height': image.get_height(),
                'layers': len(info_layers), 'source': 'live-gimp',
                'document_info': {'width': image.get_width(), 'height': image.get_height(), 'layer_count': len(info_layers), 'layers': info_layers},
            }
        if command in {'layer_update', 'translate', 'undo'}:
            image = self._image_for_payload(payload)
            raw = str(payload.get('path') or '')
            path = Path(raw).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != '.xcf':
                raise RuntimeError('direct editing requires the active session .xcf path')
            if command == 'undo':
                changed = bool(image.undo())
                Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(str(path)), None)
                Gimp.displays_flush()
                return {'ok': True, 'command': command, 'changed': changed}
            layer_id = int(payload.get('layer_id'))
            layer = next((x for x in image.get_layers() if int(x.get_tattoo()) == layer_id), None)
            if layer is None:
                raise RuntimeError('LAYER_NOT_FOUND')
            image.undo_group_start()
            try:
                if command == 'layer_update':
                    if payload.get('name') is not None:
                        layer.set_name(str(payload['name']))
                    if payload.get('visible') is not None:
                        layer.set_visible(bool(payload['visible']))
                    if payload.get('opacity') is not None:
                        opacity = float(payload['opacity'])
                        if not 0.0 <= opacity <= 100.0:
                            raise RuntimeError('opacity must be 0..100')
                        layer.set_opacity(opacity)
                elif command == 'translate':
                    layer.transform_translate(float(payload.get('dx', 0.0)), float(payload.get('dy', 0.0)))
            finally:
                image.undo_group_end()
            Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(str(path)), None)
            Gimp.displays_flush()
            return {'ok': True, 'command': command, 'layer_id': layer_id, 'name': layer.get_name(), 'visible': layer.get_visible(), 'opacity': layer.get_opacity()}
        if command == 'show_document':
            raw = str(payload.get('path') or '')
            path = Path(raw).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != '.xcf':
                raise RuntimeError('show_document requires an existing .xcf file')
            key = str(path)
            old_for_path = self._images.get(key)
            new_image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(key))
            if new_image is None:
                raise RuntimeError('GIMP could not load document')
            self._images[key] = new_image
            old_image = self._image
            if not self._headless:
                if old_image is not None and old_image.is_valid():
                    try:
                        Gimp.displays_reconnect(old_image, new_image)
                    except Exception:
                        Gimp.Display.new(new_image)
                else:
                    Gimp.Display.new(new_image)
                Gimp.displays_flush()
            self._image = new_image
            if old_for_path is not None and old_for_path is not new_image and old_for_path.is_valid():
                old_for_path.delete()
            return {
                'ok': True, 'path': str(path), 'width': new_image.get_width(),
                'height': new_image.get_height(), 'layers': len(new_image.get_layers()),
            }
        raise RuntimeError(f'unsupported command: {command}')

    def do_quit(self):
        if self._loop is not None:
            self._loop.quit()
        if self._listener is not None:
            try:
                self._listener.close()
            except Exception:
                pass


Gimp.main(GimpMcpLive.__gtype__, sys.argv)
