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
        self._lock = threading.Lock()

    def do_query_procedures(self):
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

    def _handle(self, payload):
        command = str(payload.get('command') or '')
        if command == 'ping':
            return {'ok': True, 'mode': 'persistent', 'gimp_version': str(Gimp.version()), 'socket': str(SOCKET_PATH)}
        if command == 'active_info':
            image = self._image
            return {
                'ok': True,
                'image': None if image is None else {
                    'width': image.get_width(), 'height': image.get_height(),
                    'layers': [x.get_name() for x in image.get_layers()],
                },
            }
        if command == 'show_document':
            raw = str(payload.get('path') or '')
            path = Path(raw).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != '.xcf':
                raise RuntimeError('show_document requires an existing .xcf file')
            new_image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(str(path)))
            if new_image is None:
                raise RuntimeError('GIMP could not load document')
            old_image = self._image
            if old_image is not None:
                try:
                    Gimp.displays_reconnect(old_image, new_image)
                    old_image.delete()
                except Exception:
                    Gimp.Display.new(new_image)
            else:
                Gimp.Display.new(new_image)
            self._image = new_image
            Gimp.displays_flush()
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
