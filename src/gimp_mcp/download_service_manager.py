from __future__ import annotations
import subprocess
from pathlib import Path

UNIT='gimp-mcp-downloads.service'

def install(root: Path, state: Path, port: int = 8011) -> None:
    unitdir=Path.home()/'.config/systemd/user'; unitdir.mkdir(parents=True,exist_ok=True)
    text=f'''[Unit]\nDescription=GIMP MCP Public Download Server\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory={root}\nEnvironment=GIMP_MCP_STATE_DIR={state}\nEnvironment=GIMP_MCP_DOWNLOAD_HOST=10.10.0.2\nEnvironment=GIMP_MCP_DOWNLOAD_PORT={int(port)}\nExecStart={root}/.venv/bin/python -m gimp_mcp.download_server\nRestart=on-failure\nRestartSec=2\n\n[Install]\nWantedBy=default.target\n'''
    (unitdir/UNIT).write_text(text)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now',UNIT],check=True)
