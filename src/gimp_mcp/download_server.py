from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from .public_exports import PublicExportManager

ROOT=Path(os.getenv('GIMP_MCP_STATE_DIR',str(Path.home()/'.local/state/gimp-mcp')))/'public-exports'
app=FastAPI(title='GIMP MCP Downloads',docs_url=None,redoc_url=None,openapi_url=None)

@app.get('/gimp-mcp/download/{token}/{filename}')
def download(token: str, filename: str):
    # Resolve locally without needing GIMP/export dependencies.
    import hashlib,time
    if filename not in {'artwork.xcf','artwork.jpg','artwork.png'}: raise HTTPException(404)
    d=ROOT/hashlib.sha256(token.encode()).hexdigest()
    try: expires=int((d/'expires').read_text())
    except Exception: raise HTTPException(404)
    path=d/filename
    if expires < int(time.time()) or not path.is_file(): raise HTTPException(404)
    media={'xcf':'application/x-xcf','jpg':'image/jpeg','png':'image/png'}[filename.rsplit('.',1)[1]]
    return FileResponse(path,media_type=media,filename=filename)
