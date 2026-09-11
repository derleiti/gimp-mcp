from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any


class AIControlError(RuntimeError):
    pass


class AICoderAdapter:
    """Optional adapter to AICoder's provider-owned account integrations.

    Provider OAuth/access tokens remain owned by Codex/Claude/Vibe/Antigravity.
    GIMP MCP only asks AICoder for status, model lists and interactive login.
    """

    def __init__(self) -> None:
        self.root = Path(os.getenv("GIMP_MCP_AICODER_ROOT", "/home/zombie/ai-coder"))

    def _module(self):
        root = str(self.root)
        if not self.root.exists():
            raise AIControlError(f"AICoder not found: {self.root}")
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            return importlib.import_module("aicoder.account_providers")
        except Exception as exc:
            raise AIControlError(f"Unable to load AICoder account providers: {exc}") from exc

    def providers(self) -> list[dict[str, Any]]:
        mod = self._module()
        return [mod.account_status(spec.id) for spec in mod.ACCOUNT_PROVIDERS]

    def models(self, provider: str) -> list[dict[str, Any]]:
        return list(self._module().available_account_models(provider))

    def connect(self, provider: str, *, open_browser: bool = True) -> dict[str, Any]:
        return dict(self._module().connect_account(provider, open_browser=open_browser))


    def triforce_status(self) -> dict[str, Any]:
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            cfg = importlib.import_module("aicoder.config")
            state_mod = importlib.import_module("aicoder.session_state")
            session = cfg.load_session()
            state = state_mod.get_state()
            return {
                "provider": "triforce",
                "display_name": "AILinux / TriForce",
                "authenticated": True,
                "base_url": session.base_url,
                "user_id": session.user_id,
                "tier": session.tier,
                "account_role": getattr(session, "account_role", None),
                "selected_model": state.get("selected_model"),
            }
        except Exception as exc:
            return {"provider": "triforce", "display_name": "AILinux / TriForce", "authenticated": False, "detail": str(exc)}

    def triforce_models(self) -> list[dict[str, Any]]:
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        cfg = importlib.import_module("aicoder.config")
        client_mod = importlib.import_module("aicoder.client")
        session = cfg.load_session()
        client = client_mod.TriForceClient(session.base_url, token=session.token, timeout=20)
        rows = client.list_models()
        return list(rows) if isinstance(rows, list) else list(rows.get("models", []))

    def disconnect(self, provider: str) -> dict[str, Any]:
        self._module().disconnect_account(provider)
        return {"provider": provider, "connected": False}
