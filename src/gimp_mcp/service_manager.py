from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


class ServiceManager:
    """Manage the per-user MCP server service without requiring root privileges."""

    UNIT = "gimp-mcp.service"

    def __init__(self, root: Path, state_dir: Path | None = None) -> None:
        self.root = root.resolve()
        self.state_dir = (state_dir or Path(os.getenv("GIMP_MCP_STATE_DIR", str(Path.home()/".local/state/gimp-mcp")))).resolve()
        self.unit_dir = Path.home()/".config/systemd/user"
        self.unit_path = self.unit_dir/self.UNIT

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["systemctl", "--user", *args], text=True, capture_output=True, timeout=timeout)

    def _server_command(self) -> str:
        python = self.root/".venv/bin/python"
        code = "from gimp_mcp.service_entry import main; main()"
        return f"{shlex.quote(str(python))} -c {shlex.quote(code)}"

    def unit_text(self) -> str:
        return f"""[Unit]
Description=GIMP MCP Studio Server
Documentation=https://github.com/derleiti/gimp-mcp
After=default.target

[Service]
Type=simple
WorkingDirectory={self.root}
Environment=GIMP_MCP_ROOT={self.root}
Environment=GIMP_MCP_STATE_DIR={self.state_dir}
ExecStart={self._server_command()}
Restart=on-failure
RestartSec=2
TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=default.target
"""

    def installed(self) -> bool:
        return self.unit_path.is_file()

    def install(self, *, enable: bool = True, start: bool = True) -> dict[str, Any]:
        python = self.root/".venv/bin/python"
        if not python.is_file():
            raise RuntimeError("Project environment is missing; run uv sync first")
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.unit_path.with_suffix(".service.tmp")
        tmp.write_text(self.unit_text(), encoding="utf-8")
        tmp.replace(self.unit_path)
        reload_result = self._run("daemon-reload")
        if reload_result.returncode:
            raise RuntimeError((reload_result.stderr or reload_result.stdout).strip())
        if enable:
            result = self._run("enable", self.UNIT)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
        if start:
            self.stop_legacy_server()
            result = self._run("restart", self.UNIT)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
        return self.status()

    def stop_legacy_server(self) -> bool:
        """Stop only the old PID-file managed gimp_mcp.server process during migration."""
        pidfile=self.state_dir/"server.pid"
        try:
            pid=int(pidfile.read_text().strip())
            cmdline=Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","replace")
            if "gimp_mcp.server" not in cmdline:
                return False
            os.kill(pid, 15)
            for _ in range(30):
                if not Path(f"/proc/{pid}").exists(): break
                import time; time.sleep(0.1)
            pidfile.unlink(missing_ok=True)
            return True
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pidfile.unlink(missing_ok=True)
            return False

    def uninstall(self, *, stop: bool = True) -> dict[str, Any]:
        if stop:
            self._run("stop", self.UNIT)
        self._run("disable", self.UNIT)
        self.unit_path.unlink(missing_ok=True)
        self._run("daemon-reload")
        return self.status()

    def action(self, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart", "enable", "disable"}:
            raise ValueError(f"unsupported service action: {action}")
        if not self.installed():
            raise RuntimeError("GIMP MCP systemd service is not installed")
        result = self._run(action, self.UNIT)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return self.status()

    def status(self) -> dict[str, Any]:
        active = self._run("is-active", self.UNIT).stdout.strip() if self.installed() else "not-installed"
        enabled = self._run("is-enabled", self.UNIT).stdout.strip() if self.installed() else "not-installed"
        pid = ""
        if self.installed():
            pid = self._run("show", self.UNIT, "--property=MainPID", "--value").stdout.strip()
        return {
            "installed": self.installed(), "unit": self.UNIT, "unit_path": str(self.unit_path),
            "active": active, "enabled": enabled, "pid": int(pid) if pid.isdigit() and int(pid) else None,
        }

    def logs(self, lines: int = 80) -> str:
        if not self.installed():
            return "GIMP MCP systemd service is not installed."
        proc = subprocess.run(["journalctl", "--user", "-u", self.UNIT, "-n", str(max(1,min(int(lines),500))), "--no-pager"], text=True, capture_output=True, timeout=15)
        return (proc.stdout or proc.stderr).strip()
