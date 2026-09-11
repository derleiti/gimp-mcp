from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .errors import GimpMcpError


@dataclass(slots=True)
class GimpResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


class GimpBridge:
    """Crash-isolated bridge through GIMP's official python-fu batch interpreter."""

    def __init__(self, executable: str = "/usr/bin/gimp", timeout: int = 45) -> None:
        self.executable = executable
        self.timeout = timeout

    @staticmethod
    def _clean_env() -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        return env

    def run_python(self, code: str, *, timeout: int | None = None) -> GimpResult:
        cmd = [self.executable, "-i", "-c", "--batch-interpreter=python-fu-eval", "-b", "-", "--quit"]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, input=code, text=True, capture_output=True, timeout=timeout or self.timeout, env=self._clean_env())
        except FileNotFoundError as exc:
            raise GimpMcpError("GIMP_NOT_FOUND", f"GIMP executable not found: {self.executable}", False) from exc
        except subprocess.TimeoutExpired as exc:
            raise GimpMcpError("GIMP_TIMEOUT", f"GIMP operation exceeded {timeout or self.timeout}s") from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        result = GimpResult(proc.returncode, proc.stdout, proc.stderr, duration_ms)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout)[-3000:]
            raise GimpMcpError("PDB_CALL_FAILED", f"GIMP failed ({proc.returncode}): {tail}")
        return result

    def run_json(self, body: str, *, timeout: int | None = None) -> Any:
        marker = "__GIMP_MCP_JSON__"
        code = "import json, sys\nfrom gi.repository import Gimp, Gegl, Gio\n" + body + f"\nprint('{marker}' + json.dumps(result, ensure_ascii=False))\n"
        res = self.run_python(code, timeout=timeout)
        for line in reversed(res.stdout.splitlines()):
            if line.startswith(marker):
                return json.loads(line[len(marker):])
        raise GimpMcpError("INTERNAL_ERROR", "GIMP returned no structured result marker")

    def probe(self) -> dict[str, Any]:
        return self.run_json("result={'gimp_version':str(Gimp.version()),'python':sys.version.split()[0],'pdb_available':Gimp.get_pdb() is not None}")
