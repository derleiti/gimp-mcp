from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class StudioMcpError(RuntimeError):
    pass


class StudioMcpClient:
    """Small synchronous adapter for Studio jobs over the real local MCP transport."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8000/mcp", timeout: float = 20.0, bearer_token: str = "") -> None:
        self.endpoint = endpoint
        self.timeout = max(2.0, float(timeout))
        self.bearer_token = bearer_token.strip()

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(arguments)
        mode = str(arguments.pop("__job_mode", "auto") or "auto")
        if mode not in {"auto", "live", "batch"}:
            raise StudioMcpError(f"invalid Studio job mode: {mode}")
        headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
        async with httpx2.AsyncClient(headers=headers) as http:
            async with streamable_http_client(self.endpoint, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=self.timeout) as session:
                    await session.initialize()
                    if mode == "live" and name != "session_create":
                        live = await session.call_tool("live_bridge_status", {}, read_timeout_seconds=self.timeout)
                        live_structured = getattr(live, "structured_content", None)
                        live_data = live_structured.get("data", {}) if isinstance(live_structured, dict) else {}
                        if not live_data.get("ok"):
                            raise StudioMcpError("live mode requested but GIMP Live Bridge is offline")
                    result = await session.call_tool(name, arguments, read_timeout_seconds=self.timeout)
                    structured = getattr(result, "structured_content", None)
                    if isinstance(structured, dict):
                        return structured
                    for item in getattr(result, "content", []) or []:
                        text = getattr(item, "text", None)
                        if text:
                            try:
                                parsed = json.loads(text)
                                if isinstance(parsed, dict):
                                    return parsed
                            except json.JSONDecodeError:
                                continue
                    if getattr(result, "is_error", False):
                        raise StudioMcpError(f"MCP tool {name} returned an error without structured content")
                    raise StudioMcpError(f"MCP tool {name} returned no usable structured result")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return asyncio.run(asyncio.wait_for(self._call(name, dict(arguments)), timeout=self.timeout + 5.0))
        except Exception as exc:
            if isinstance(exc, StudioMcpError):
                raise
            raise StudioMcpError(f"MCP call {name} via {self.endpoint} failed: {exc}") from exc
