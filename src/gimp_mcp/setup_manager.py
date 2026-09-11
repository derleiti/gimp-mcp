from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local/bin/uv"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
SYSTEM_PACKAGES = [
    "gimp", "python3-gi", "gir1.2-gimp-3.0", "python3-venv",
    "python3-pyqt6", "git", "curl",
]


def _run(argv: list[str], *, timeout: int = 60, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd or ROOT), text=True, capture_output=True, timeout=timeout)


def _version(cmd: list[str]) -> str:
    try:
        r = _run(cmd, timeout=10)
        text = (r.stdout or r.stderr).strip().splitlines()
        return text[0] if text else "unknown"
    except Exception:
        return "missing"


def _apt_policy(package: str) -> dict[str, str]:
    r = _run(["apt-cache", "policy", package], timeout=15)
    installed = candidate = "unknown"
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("Installiert:") or s.startswith("Installed:"):
            installed = s.split(":", 1)[1].strip()
        elif s.startswith("Installationskandidat:") or s.startswith("Candidate:"):
            candidate = s.split(":", 1)[1].strip()
    return {"installed": installed, "candidate": candidate}


def _fetch_json(url: str, timeout: int = 8) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gimp-mcp-studio/0.4"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:
        return None


def _fetch_text(url: str, timeout: int = 8) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gimp-mcp-studio/0.4"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


@dataclass(slots=True)
class CheckRow:
    component: str
    installed: str
    available: str
    source: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class SetupManager:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def local_checks(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        for package in SYSTEM_PACKAGES:
            p = _apt_policy(package)
            installed = p["installed"]
            candidate = p["candidate"]
            runtime_note = ""
            if package == "python3-pyqt6" and installed in {"(keine)", "(none)", "none"}:
                try:
                    probe = _run(["/usr/bin/python3", "-c", "import PyQt6; print(PyQt6.__file__)"], timeout=10)
                    if probe.returncode == 0:
                        runtime_note = f"runtime available: {probe.stdout.strip()}"
                except Exception:
                    pass
            if installed in {"(keine)", "(none)", "none"}:
                status = "runtime-ok" if runtime_note else "missing"
                if runtime_note:
                    installed = runtime_note
            elif candidate not in {"unknown", "(keine)", "(none)"} and installed != candidate:
                status = "update"
            else:
                status = "ok"
            rows.append(CheckRow(package, installed, candidate, "APT", status))

        uv = _find_uv()
        rows.append(CheckRow("uv", _version([uv, "--version"]) if uv else "missing", "check upstream", "Astral", "ok" if uv else "missing"))

        try:
            import importlib.metadata as md
            mcp_ver = md.version("mcp")
        except Exception:
            mcp_ver = "missing"
        rows.append(CheckRow("mcp", mcp_ver, "uv.lock", "uv/PyPI", "ok" if mcp_ver != "missing" else "missing"))
        return rows

    def update_check(self) -> dict[str, Any]:
        result: dict[str, Any] = {"local": [r.as_dict() for r in self.local_checks()]}

        uv_latest = _fetch_json("https://pypi.org/pypi/uv/json")
        result["uv_upstream"] = ((uv_latest or {}).get("info") or {}).get("version")

        mcp_latest = _fetch_json("https://pypi.org/pypi/mcp/json")
        result["mcp_upstream"] = ((mcp_latest or {}).get("info") or {}).get("version")

        pyqt_latest = _fetch_json("https://pypi.org/pypi/PyQt6/json")
        result["pyqt6_upstream"] = ((pyqt_latest or {}).get("info") or {}).get("version")

        gimp_releases = _fetch_json("https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgimp/releases")
        releases = gimp_releases if isinstance(gimp_releases, list) else []
        result["gimp_upstream"] = None
        for item in releases:
            name = str(item.get("name") or "")
            m = re.search(r"GIMP\s+(3\.\d+\.\d+)", name, re.I)
            if m:
                result["gimp_upstream"] = m.group(1)
                break

        try:
            _run(["git", "fetch", "--quiet", "origin", "main"], timeout=30, cwd=self.root)
            r = _run(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], timeout=10, cwd=self.root)
            parts = r.stdout.strip().split()
            result["git"] = {
                "behind": int(parts[1]) if len(parts) > 1 else 0,
                "ahead": int(parts[0]) if parts else 0,
            }
        except Exception as exc:
            result["git"] = {"error": str(exc)}
        return result


    def installed_gimp_version(self, executable: str | None = None) -> str | None:
        exe = executable or shutil.which("gimp") or "/usr/bin/gimp"
        try:
            r = _run([str(exe), "--version"], timeout=15)
            text = (r.stdout or r.stderr or "").strip()
            m = re.search(r"(?:GIMP|Program Version)\s+(\d+\.\d+\.\d+)", text, re.I)
            return m.group(1) if m else None
        except Exception:
            return None

    def latest_gimp_release(self) -> dict[str, Any]:
        releases = _fetch_json("https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgimp/releases?per_page=10", timeout=15)
        for item in releases if isinstance(releases, list) else []:
            name = str(item.get("name") or "")
            m = re.search(r"GIMP\s+(3\.\d+\.\d+)$", name, re.I)
            if m:
                version = m.group(1)
                arch = platform.machine().lower()
                app_arch = "aarch64" if arch in {"aarch64", "arm64"} else "x86_64"
                series = ".".join(version.split(".")[:2])
                filename = f"GIMP-{version}-{app_arch}.AppImage"
                base = f"https://download.gimp.org/gimp/v{series}/linux"
                return {"version": version, "arch": app_arch, "filename": filename, "url": f"{base}/{filename}", "checksums_url": f"{base}/SHA256SUMS", "source": "official-gimp"}
        raise RuntimeError("Could not determine the current stable GIMP release from the official GNOME release feed")

    def managed_gimp_status(self) -> dict[str, Any]:
        release = self.latest_gimp_release()
        current = self.installed_gimp_version()
        managed = Path.home() / ".local/share/gimp-mcp/runtime/gimp.AppImage"
        managed_version = self.installed_gimp_version(str(managed)) if managed.exists() else None
        return {"system_version": current, "managed_version": managed_version, "latest_version": release["version"], "managed_path": str(managed), "update_available": (managed_version or current) != release["version"], "release": release}

    def install_latest_gimp_appimage(self, progress: Any | None = None) -> dict[str, Any]:
        release = self.latest_gimp_release()
        checksums = _fetch_text(release["checksums_url"], timeout=20)
        expected = None
        for line in checksums.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == release["filename"]:
                expected = parts[0].lower(); break
        if not expected:
            raise RuntimeError("Official SHA256 checksum for the GIMP AppImage was not found")
        runtime = Path.home() / ".local/share/gimp-mcp/runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        versioned = runtime / release["filename"]
        tmp = versioned.with_suffix(versioned.suffix + ".part")
        req = urllib.request.Request(release["url"], headers={"User-Agent": "GIMP-MCP-Studio/0.4"})
        sha = hashlib.sha256(); downloaded = 0
        with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while True:
                block = resp.read(1024 * 1024)
                if not block: break
                out.write(block); sha.update(block); downloaded += len(block)
                if progress: progress(downloaded, total)
            out.flush(); os.fsync(out.fileno())
        actual = sha.hexdigest().lower()
        if actual != expected:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"GIMP AppImage checksum mismatch: expected {expected}, got {actual}")
        tmp.chmod(0o755); tmp.replace(versioned)
        link = runtime / "gimp.AppImage"
        link.unlink(missing_ok=True); link.symlink_to(versioned.name)
        return {"ok": True, "version": release["version"], "path": str(link), "sha256": actual, "source": release["url"]}

    def gimp_mcp_update_status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"source": "TriForce curated search + GitHub origin", "current": None, "behind": 0, "ahead": 0}
        try:
            result["current"] = _run(["git", "rev-parse", "--short", "HEAD"], timeout=10, cwd=self.root).stdout.strip()
            _run(["git", "fetch", "--quiet", "origin", "main"], timeout=30, cwd=self.root)
            counts = _run(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], timeout=10, cwd=self.root).stdout.split()
            result["ahead"] = int(counts[0]) if counts else 0
            result["behind"] = int(counts[1]) if len(counts) > 1 else 0
        except Exception as exc:
            result["error"] = str(exc)
        # TriForce search is advisory; GitHub origin remains the deterministic source of code truth.
        try:
            query = json.dumps({"query": "derleiti gimp-mcp github latest release", "mode": "code", "limit": 8}).encode()
            req = urllib.request.Request("https://api.ailinux.me/v1/search/curated", data=query, headers={"Content-Type": "application/json", "User-Agent": "GIMP-MCP-Studio/0.4"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.load(resp)
            results = payload.get("results") if isinstance(payload, dict) else []
            relevant = [x for x in (results or []) if "gimp-mcp" in str(x).lower() or "derleiti" in str(x).lower()]
            result["triforce_search"] = {"ok": True, "result_count": len(results or []), "relevant_count": len(relevant), "mode": payload.get("mode") if isinstance(payload, dict) else None}
        except Exception as exc:
            result["triforce_search"] = {"ok": False, "error": str(exc)}
        return result

    def install_system_dependencies(self) -> subprocess.CompletedProcess[str]:
        if not shutil.which("pkexec"):
            raise RuntimeError("PolicyKit (pkexec) is not available; install system packages manually with apt")
        cmd = ["pkexec", "apt-get", "install", "-y", *SYSTEM_PACKAGES]
        return _run(cmd, timeout=300, cwd=self.root)

    def sync_project(self, *, upgrade: bool = False) -> subprocess.CompletedProcess[str]:
        uv = _find_uv()
        if not uv:
            raise RuntimeError("uv is not installed")
        if upgrade:
            lock = _run([uv, "lock", "--upgrade"], timeout=300, cwd=self.root)
            if lock.returncode != 0:
                return lock
        return _run([uv, "sync", "--group", "dev"], timeout=300, cwd=self.root)

    def update_uv(self) -> subprocess.CompletedProcess[str]:
        uv = _find_uv()
        if not uv:
            raise RuntimeError("uv is not installed; install it from the official Astral documentation")
        return _run([uv, "self", "update"], timeout=180, cwd=self.root)

    def self_test(self) -> dict[str, Any]:
        uv = _find_uv()
        if not uv:
            return {"ok": False, "error": "uv is not installed"}
        test = _run([uv, "run", "pytest", "-q"], timeout=300, cwd=self.root)
        probe = _run([uv, "run", "python", "-c", "from gimp_mcp.bridge import GimpBridge; print(GimpBridge().probe())"], timeout=90, cwd=self.root)
        return {
            "ok": test.returncode == 0 and probe.returncode == 0,
            "pytest": test.stdout.strip() or test.stderr.strip(),
            "gimp_probe": probe.stdout.strip() or probe.stderr.strip(),
        }
