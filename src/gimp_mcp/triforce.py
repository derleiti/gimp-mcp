from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TriForceAccount:
    base_url: str = os.getenv("GIMP_MCP_TRIFORCE_URL", "https://api.ailinux.me")
    token: str = os.getenv("GIMP_MCP_TRIFORCE_TOKEN", "")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "gimp-mcp/0.2"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"TriForce HTTP {exc.code}: {body[:300]}") from exc

    def login(self, email: str, password: str) -> dict[str, Any]:
        result = self._request("POST", "/v1/auth/login", {"email": email, "password": password})
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            raise RuntimeError("TriForce login returned no token")
        self.token = str(token)
        safe = {k: v for k, v in result.items() if k not in {"token", "refresh_token"}}
        return safe

    def models(self) -> Any:
        # Prefer the established client endpoint; installations may expose a compatible alias.
        return self._request("GET", "/v1/client/models")
