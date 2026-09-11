from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    pass


_PROVIDER_LABELS = {
    "chatgpt": "ChatGPT / OpenAI",
    "claude": "Claude / Anthropic",
    "gemini": "Google Antigravity",
    "mistral": "Mistral Vibe",
    "triforce": "AILinux / TriForce",
}


class ProviderManager:
    """Native provider integration with no AICoder dependency.

    OAuth/account credentials remain owned by each official provider client.
    GIMP MCP only invokes their documented CLI/app-server surfaces.
    """

    def __init__(self, timeout: int = 120) -> None:
        self.timeout = max(10, min(int(timeout), 3600))

    @staticmethod
    def _candidate(name: str) -> str | None:
        found = shutil.which(name)
        if found:
            return found
        home = Path.home()
        candidates = [
            home / ".local/bin" / name,
            home / ".npm-global/bin" / name,
            home / ".cargo/bin" / name,
            home / ".bun/bin" / name,
        ]
        for path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        return None

    @staticmethod
    def _clean_env() -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([
            str(Path.home() / ".local/bin"),
            str(Path.home() / ".npm-global/bin"),
            "/usr/local/bin", "/usr/bin", "/bin",
        ])
        # Never inject API credentials into account-backed CLI sessions.
        for key in (
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL", "MISTRAL_API_KEY", "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            env.pop(key, None)
        return env

    @staticmethod
    def _codex_mcp_disable_args() -> list[str]:
        """Disable user-configured MCP servers without discarding Codex account/model config."""
        config = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8"))
            servers = data.get("mcp_servers") or {}
        except Exception:
            servers = {}
        args: list[str] = []
        if isinstance(servers, dict):
            for name, value in servers.items():
                if isinstance(value, dict):
                    args.extend(["-c", f"mcp_servers.{name}.enabled=false"])
        return args

    def _run(self, argv: list[str], *, timeout: int | None = None, cwd: str | None = None,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv, cwd=cwd, env=env or self._clean_env(), text=True,
            capture_output=True, timeout=timeout or self.timeout,
        )

    def _codex_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        exe = self._candidate("codex")
        if not exe:
            raise ProviderError("Codex CLI is not installed")
        proc = subprocess.Popen(
            [exe, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            env=self._clean_env(),
        )
        if proc.stdin is None or proc.stdout is None:
            proc.kill()
            raise ProviderError("Unable to start Codex app-server")
        try:
            def send(payload: dict[str, Any]) -> None:
                proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            def receive(request_id: int) -> dict[str, Any]:
                deadline = time.monotonic() + min(self.timeout, 30)
                while time.monotonic() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("id") == request_id:
                        if payload.get("error"):
                            raise ProviderError(str(payload["error"].get("message") or payload["error"]))
                        return payload.get("result") or {}
                raise ProviderError(f"Codex app-server timed out waiting for {method}")

            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "gimp-mcp", "version": "0.4.0"},
                "capabilities": {},
            }})
            receive(1)
            send({"id": 2, "method": method, "params": params})
            return receive(2)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _status_chatgpt(self) -> dict[str, Any]:
        exe = self._candidate("codex")
        if not exe:
            return self._status("chatgpt", installed=False, authenticated=False, detail="Codex CLI not installed")
        r = self._run([exe, "login", "status"], timeout=15)
        text = (r.stdout or r.stderr).strip()
        authenticated = r.returncode == 0 and "logged in" in text.lower()
        return self._status("chatgpt", installed=True, authenticated=authenticated, detail=text or "Not logged in")

    def _status_claude(self) -> dict[str, Any]:
        exe = self._candidate("claude")
        if not exe:
            return self._status("claude", installed=False, authenticated=False, detail="Claude Code not installed")
        r = self._run([exe, "auth", "status", "--json"], timeout=15)
        try:
            data = json.loads(r.stdout)
        except Exception:
            data = {}
        authenticated = r.returncode == 0 and bool(data.get("loggedIn"))
        detail = data.get("authMethod") or (r.stderr.strip() if r.stderr else "Not logged in")
        result = self._status("claude", installed=True, authenticated=authenticated, detail=str(detail))
        for key in ("email", "subscriptionType", "authMethod"):
            if data.get(key):
                result[key] = data[key]
        return result

    def _status_gemini(self) -> dict[str, Any]:
        exe = self._candidate("agy")
        if not exe:
            return self._status("gemini", installed=False, authenticated=False, detail="Antigravity CLI not installed")
        r = self._run([exe, "models"], timeout=20)
        text = (r.stdout or r.stderr).strip()
        authenticated = r.returncode == 0 and "sign in" not in text.lower()
        return self._status("gemini", installed=True, authenticated=authenticated, detail="Connected" if authenticated else text[:300])

    def _status_mistral(self) -> dict[str, Any]:
        exe = self._candidate("vibe")
        if not exe:
            return self._status("mistral", installed=False, authenticated=False, detail="Mistral Vibe not installed")
        config = Path(os.getenv("VIBE_HOME", str(Path.home() / ".vibe"))) / "config.toml"
        # Vibe has no cheap status command. Presence of its own config means configured;
        # actual auth is verified on the first model request without reading credentials.
        configured = config.is_file()
        return self._status("mistral", installed=True, authenticated=configured,
                            detail="Vibe configured; credentials remain provider-owned" if configured else "Run Vibe setup")

    @staticmethod
    def _status(provider: str, *, installed: bool, authenticated: bool, detail: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "display": _PROVIDER_LABELS[provider],
            "installed": installed,
            "authenticated": authenticated,
            "linked": authenticated,
            "detail": detail,
        }

    def provider_status(self, provider: str) -> dict[str, Any]:
        provider = provider.lower().strip()
        if provider == "chatgpt": return self._status_chatgpt()
        if provider == "claude": return self._status_claude()
        if provider == "gemini": return self._status_gemini()
        if provider == "mistral": return self._status_mistral()
        if provider == "triforce": return self.triforce_status()
        raise ProviderError(f"Unknown provider: {provider}")

    def providers(self) -> list[dict[str, Any]]:
        return [self.provider_status(p) for p in ("chatgpt", "claude", "gemini", "mistral")]

    def models(self, provider: str) -> list[dict[str, Any]]:
        provider = provider.lower().strip()
        if provider == "chatgpt":
            result = self._codex_rpc("model/list", {"limit": 100, "includeHidden": False})
            rows = result.get("data") or []
            return [{
                "provider": "chatgpt", "model": str(x.get("model") or x.get("id")),
                "id": f"account:chatgpt/{x.get('model') or x.get('id')}",
                "display": str(x.get("displayName") or x.get("model") or x.get("id")),
                "is_default": bool(x.get("isDefault")),
            } for x in rows if isinstance(x, dict) and (x.get("model") or x.get("id"))]
        if provider == "claude":
            if not self._status_claude().get("authenticated"):
                return []
            return [
                {"provider": "claude", "model": name, "id": f"account:claude/{name}", "display": label}
                for name, label in (("sonnet", "Claude Sonnet (latest)"), ("opus", "Claude Opus (latest)"),
                                    ("fable", "Claude Fable (latest)"), ("haiku", "Claude Haiku (latest)"))
            ]
        if provider == "gemini":
            exe = self._candidate("agy")
            if not exe: return []
            r = self._run([exe, "models"], timeout=20)
            if r.returncode != 0: return []
            rows: list[dict[str, Any]] = []
            for line in r.stdout.splitlines():
                text = line.strip().lstrip("*-• ")
                if not text or text.lower().startswith(("fetching", "available")):
                    continue
                model = text.split()[0]
                if "/" in model or "gemini" in model.lower() or model.lower().startswith("claude"):
                    rows.append({"provider":"gemini", "model":model, "id":f"account:gemini/{model}", "display":text})
            return rows
        if provider == "mistral":
            # Vibe currently exposes model switching interactively rather than a stable
            # machine-readable catalog. Keep model IDs editable in the GUI and expose
            # common provider aliases instead of scraping credentials/config secrets.
            return [
                {"provider":"mistral", "model":"devstral", "id":"account:mistral/devstral", "display":"Devstral"},
                {"provider":"mistral", "model":"mistral-large", "id":"account:mistral/mistral-large", "display":"Mistral Large"},
                {"provider":"mistral", "model":"mistral-medium", "id":"account:mistral/mistral-medium", "display":"Mistral Medium"},
            ] if self._status_mistral().get("authenticated") else []
        if provider == "triforce":
            return self.triforce_models()
        raise ProviderError(f"Unknown provider: {provider}")

    @staticmethod
    def _strip_account_model(provider: str, model: str) -> str:
        provider = provider.lower().strip()
        model = str(model or "").strip()
        if model.startswith("account:"):
            prefix, sep, inner = model.partition("/")
            model_provider = prefix.removeprefix("account:").lower().strip()
            if not sep or not inner:
                raise ProviderError(f"Invalid account model id: {model}")
            if model_provider != provider:
                raise ProviderError(
                    f"Provider/model mismatch: provider={provider}, model belongs to {model_provider}: {model}"
                )
            return inner
        return model

    def chat(self, provider: str, model: str, message: str, system_prompt: str, timeout: int | None = None) -> str:
        provider = provider.lower().strip()
        request_timeout = max(10, min(int(timeout if timeout is not None else self.timeout), 3600))
        model = self._strip_account_model(provider, str(model).strip())
        if not model:
            rows = self.models(provider)
            if not rows:
                raise ProviderError(f"No model available for {provider}")
            model = str(next((x["model"] for x in rows if x.get("is_default")), rows[0]["model"]))
        transcript = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{message}"
        if provider == "chatgpt":
            exe = self._candidate("codex")
            if not exe: raise ProviderError("Codex CLI is not installed")
            with tempfile.TemporaryDirectory(prefix="gimp-mcp-codex-") as tmp:
                out = Path(tmp) / "answer.txt"
                r = self._run([exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                               "-c", "approval_policy=\"never\"", *self._codex_mcp_disable_args(),
                               "-m", model, "-C", tmp, "-o", str(out), transcript],
                              timeout=request_timeout, cwd=tmp)
                if r.returncode != 0:
                    raise ProviderError((r.stderr or r.stdout).strip()[-1000:] or "Codex request failed")
                text = out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""
        elif provider == "claude":
            exe = self._candidate("claude")
            if not exe: raise ProviderError("Claude Code is not installed")
            env = self._clean_env()
            with tempfile.TemporaryDirectory(prefix="gimp-mcp-claude-") as tmp:
                r = self._run([exe, "--print", "--output-format", "text", "--model", model,
                               "--tools", "", "--disallowed-tools", "*", "--disable-slash-commands",
                               "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}", "--no-session-persistence", "--no-chrome", "--system-prompt", system_prompt,
                               message], timeout=request_timeout, cwd=tmp, env=env)
                if r.returncode != 0:
                    raise ProviderError((r.stderr or r.stdout).strip()[-1000:] or "Claude request failed")
                text = r.stdout.strip()
        elif provider == "gemini":
            exe = self._candidate("agy")
            if not exe: raise ProviderError("Antigravity CLI is not installed")
            with tempfile.TemporaryDirectory(prefix="gimp-mcp-agy-") as tmp:
                r = self._run([exe, "--print", transcript, "--model", model, "--output-format", "json",
                               "--mode", "plan", "--sandbox", "--disable-slash-commands",
                               "--print-timeout", f"{request_timeout}s"], timeout=request_timeout, cwd=tmp)
                if r.returncode != 0:
                    raise ProviderError((r.stderr or r.stdout).strip()[-1000:] or "Antigravity request failed")
                try:
                    payload = json.loads(r.stdout)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Antigravity returned invalid JSON") from exc
                text = str(payload.get("response") or payload.get("result") or payload.get("text") or "").strip()
                if isinstance(payload.get("result"), dict) and not text:
                    text = str(payload["result"].get("response") or "").strip()
        elif provider == "mistral":
            exe = self._candidate("vibe")
            if not exe: raise ProviderError("Mistral Vibe is not installed")
            with tempfile.TemporaryDirectory(prefix="gimp-mcp-vibe-") as tmp:
                env = self._clean_env(); env["VIBE_ACTIVE_MODEL"] = model
                r = self._run([exe, "--prompt", transcript, "--max-turns", "1", "--output", "text",
                               "--disabled-tools", "*", "--agent", "ask", "--workdir", tmp, "--trust"],
                              timeout=request_timeout, cwd=tmp, env=env)
                if r.returncode != 0:
                    raise ProviderError((r.stderr or r.stdout).strip()[-1000:] or "Mistral Vibe request failed")
                text = r.stdout.strip()
        elif provider == "triforce":
            return self._triforce_chat(model, message, system_prompt, timeout=request_timeout)
        else:
            raise ProviderError(f"Unknown provider: {provider}")
        if not text:
            raise ProviderError(f"{provider} returned an empty response")
        return text

    def connect(self, provider: str, *, open_browser: bool = True) -> dict[str, Any]:
        provider = provider.lower().strip()
        commands = {
            "chatgpt": [self._candidate("codex"), "login"],
            "claude": [self._candidate("claude"), "auth", "login"],
            "gemini": [self._candidate("agy")],
            "mistral": [self._candidate("vibe"), "--setup"],
        }
        cmd = commands.get(provider)
        if not cmd or not cmd[0]:
            raise ProviderError(f"Official client is not installed for {provider}")
        terminal = self._candidate("konsole") or self._candidate("x-terminal-emulator") or self._candidate("gnome-terminal")
        if terminal:
            name = Path(terminal).name
            if name == "gnome-terminal":
                argv = [terminal, "--", *cmd]
            else:
                argv = [terminal, "-e", *cmd]
            subprocess.Popen(argv, start_new_session=True, env=self._clean_env())
        else:
            subprocess.Popen(cmd, start_new_session=True, env=self._clean_env())
        return {"provider": provider, "started": True, "client": Path(str(cmd[0])).name}

    def disconnect(self, provider: str) -> dict[str, Any]:
        provider = provider.lower().strip()
        commands = {
            "chatgpt": [self._candidate("codex"), "logout"],
            "claude": [self._candidate("claude"), "auth", "logout"],
        }
        cmd = commands.get(provider)
        if not cmd or not cmd[0]:
            raise ProviderError(f"Disconnect is not available for {provider}; use the official client")
        r = self._run(cmd, timeout=30)
        if r.returncode != 0:
            raise ProviderError((r.stderr or r.stdout).strip() or "Logout failed")
        return {"provider": provider, "connected": False}

    # TriForce is independent too: optional explicit GIMP-MCP environment, never AICoder state.
    @staticmethod
    def _triforce_config() -> tuple[str, str]:
        return os.getenv("GIMP_MCP_TRIFORCE_URL", "https://api.ailinux.me").rstrip("/"), os.getenv("GIMP_MCP_TRIFORCE_TOKEN", "")

    def triforce_status(self) -> dict[str, Any]:
        base, token = self._triforce_config()
        return self._status("triforce", installed=True, authenticated=bool(token),
                            detail=f"Configured for {base}" if token else "Set GIMP_MCP_TRIFORCE_TOKEN for independent TriForce access")

    def _triforce_request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int | None = None) -> Any:
        base, token = self._triforce_config()
        if not token:
            raise ProviderError("TriForce token is not configured in GIMP MCP")
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "gimp-mcp/0.4",
        })
        try:
            with urllib.request.urlopen(req, timeout=max(10, min(int(timeout if timeout is not None else self.timeout), 3600))) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"TriForce HTTP {exc.code}") from exc

    def triforce_models(self) -> list[dict[str, Any]]:
        data = self._triforce_request("GET", "/v1/client/models")
        rows = data if isinstance(data, list) else data.get("models", [])
        return list(rows) if isinstance(rows, list) else []

    def _triforce_chat(self, model: str, message: str, system_prompt: str, *, timeout: int | None = None) -> str:
        data = self._triforce_request("POST", "/v1/client/chat", {
            "model": model, "message": message, "system_prompt": system_prompt,
            "temperature": 0.2, "max_tokens": 4096,
        }, timeout=timeout)
        text = str((data or {}).get("response") or (data or {}).get("text") or "").strip()
        if not text:
            raise ProviderError("TriForce returned an empty response")
        return text
