from __future__ import annotations
import hashlib, mimetypes, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT=Path(os.getenv('GIMP_MCP_STATE_DIR',str(Path.home()/'.local/state/gimp-mcp')))/'public-exports'
PREFIX='/gimp-mcp/download/'

class Handler(BaseHTTPRequestHandler):
    server_version='GimpMcpDownloads/1.0'
    def do_GET(self):
        path=unquote(urlparse(self.path).path)
        parts=path[len(PREFIX):].split('/') if path.startswith(PREFIX) else []
        if len(parts)!=2 or parts[1] not in {'artwork.xcf','artwork.jpg','artwork.png'}: return self._not_found()
        token,filename=parts; d=ROOT/hashlib.sha256(token.encode()).hexdigest()
        try: expires=int((d/'expires').read_text())
        except Exception: return self._not_found()
        file=d/filename
        if expires < int(time.time()) or not file.is_file(): return self._not_found()
        media={'xcf':'application/x-xcf','jpg':'image/jpeg','png':'image/png'}[filename.rsplit('.',1)[1]]
        self.send_response(200); self.send_header('Content-Type',media); self.send_header('Content-Length',str(file.stat().st_size)); self.send_header('Content-Disposition',f'attachment; filename="{filename}"'); self.send_header('Cache-Control','private, max-age=300'); self.end_headers()
        with file.open('rb') as src:
            while chunk:=src.read(1024*1024): self.wfile.write(chunk)
    def _not_found(self): self.send_response(404); self.send_header('Content-Length','0'); self.end_headers()
    def log_message(self,fmt,*args): pass

def main():
    host=os.getenv('GIMP_MCP_DOWNLOAD_HOST','127.0.0.1'); port=int(os.getenv('GIMP_MCP_DOWNLOAD_PORT','8011'))
    ThreadingHTTPServer((host,port),Handler).serve_forever()
if __name__=='__main__': main()
