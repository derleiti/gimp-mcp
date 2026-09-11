from __future__ import annotations

import os
from pathlib import Path

from .control_settings import ControlSettings


def main() -> None:
    state = Path(os.getenv("GIMP_MCP_STATE_DIR", str(Path.home()/".local/state/gimp-mcp")))
    saved = ControlSettings(state/"control.json").load()
    host = str(saved.get("mcp_bind_host", "127.0.0.1"))
    port = int(saved.get("mcp_port", 8000))
    if saved.get("mcp_network_enabled"):
        token = str(saved.get("mcp_auth_token") or "").strip()
        if not token:
            raise RuntimeError("Network MCP is enabled but no authentication token is configured")
        os.environ["GIMP_MCP_AUTH_TOKEN"] = token
        os.environ["GIMP_MCP_RESOURCE_URL"] = f"http://{host}:{port}/mcp"
    else:
        os.environ.pop("GIMP_MCP_AUTH_TOKEN", None)
        os.environ.pop("GIMP_MCP_RESOURCE_URL", None)
    os.environ["GIMP_MCP_PUBLIC_EXPORT_BASE_URL"] = str(saved.get("public_export_base_url") or "https://ailinux.me/gimp-mcp/download")
    if saved.get("gimp_runtime") == "managed" and saved.get("managed_gimp_path"):
        os.environ["GIMP_MCP_GIMP"] = str(saved["managed_gimp_path"])
    from .server import mcp
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
