# GIMP MCP Studio Quickstart

1. Clone the repository and enter it.
2. Run `~/.local/bin/uv sync` (or `uv sync` when uv is on PATH).
3. Install/update the persistent GIMP plug-in with `./scripts/install-gimp-live-plugin`.
4. Restart GIMP once when it is safe to close existing work.
5. Start `./scripts/gimp-mcp-control`.
6. In **Studio**, select provider, model and `auto`, enter the artwork/edit prompt and press **RUN**.
7. Watch job state and the live preview. Use **PAUSE**, **STOP**, **UNDO**, **REDO**, a follow-up, or **MAKE IT COOLER** as needed.

`auto` prefers the persistent GIMP bridge and falls back to the crash-isolated batch workflow. Provider credentials remain owned by AICoder/official provider clients; GIMP MCP does not copy OAuth tokens.
