from __future__ import annotations
import subprocess
from pathlib import Path

UNIT='gimp-mcp-downloads.service'

def install(root: Path, state: Path, port: int = 8011) -> None:
    unitdir=Path.home()/'.config/systemd/user'; unitdir.mkdir(parents=True,exist_ok=True)
    text=f'''[Unit]\nDescription=GIMP MCP Public Download Server\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory={root}\nEnvironment=GIMP_MCP_STATE_DIR={state}\nExecStart={root}/.venv/bin/python -m uvicorn gimp_mcp.download_server:app --host 127.0.0.1 --port {int(port)}\nRestart=on-failure\nRestartSec=2\n\n[Install]\nWantedBy=default.target\n'''
    (unitdir/UNIT).write_text(text)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now',UNIT],check=True)
