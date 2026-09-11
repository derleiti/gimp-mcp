# Changelog

## 0.4.0 - 2026-09-11

### Natural-language Studio workflow
- Made natural-language prompting the primary Studio workflow; JSON plans remain an internal execution detail.
- Added strict per-tool argument validation and safe GEGL filter validation before execution.
- Added per-step runtime status (`pending`, `running`, `ok`, `failed`) and visible step errors in Studio.
- Added semantic `shape_create` for filled ellipse/rectangle layers so subjects can be composed from real shapes instead of full rectangular fills.
- Planner now prefers shapes for object parts and explicitly avoids pretending flat fills are photorealistic.
- Verified an end-to-end Brumo bear prompt through Claude -> validated plan -> GIMP shapes -> preview.


### Autonomous runtime debugging and model-tolerance hardening
- Added `scripts/auto-debug` with persistent JSONL diagnostics for tests, providers, previews, exports, live bridge and MCP transport.
- Added rotating GUI exception/runtime logging under `~/.local/state/gimp-mcp/gui.log`.
- Fixed headless GIMP spawning the persistent live plug-in and leaving stale Unix sockets after batch operations.
- Added stale live-socket cleanup for refused connections.
- Expanded `layer_create` to safely accept common model-produced opacity, visibility and blend-mode arguments.
- Normalized transform values supplied as semantic objects (`{x,y}`, `{dx,dy}`, rotate/scale objects) as well as arrays.
- Added regression coverage for batch/live isolation and transform normalization.


### Live preview and export completion
- Live preview rendering is now atomic (`preview.tmp.png` -> `preview.png`) to prevent partial-frame reads.
- Qt preview loading now reads image bytes, validates them, keeps the last complete frame on transient errors, and rescales on window resize.
- Added XCF, PNG and JPG export from the canonical session document in both Studio and Live views.
- Added `export_artwork` MCP tool for explicit XCF/PNG/JPG output.
- Added `ExportService` with suffix normalization and overwrite handling.
- Hardened Qt helper-process shutdown to avoid QProcess teardown races.


### Provider routing hardening
- Reject cross-provider account model IDs before invoking any official CLI.
- Prevent stale asynchronous model catalogues from overwriting the currently selected provider.
- Clear editable model text immediately on provider changes.
- Disable user-configured Codex MCP servers per subprocess while preserving ChatGPT account/model configuration.
- Run Claude planning with an explicit empty strict MCP configuration.

### Native provider independence
- Removed the runtime dependency on AICoder for ChatGPT, Claude, Gemini and Mistral account access.
- Added native official-client status/login/chat routing via Codex CLI, Claude Code, Antigravity and Mistral Vibe.
- ChatGPT model selection now uses Codex app-server `model/list`, so the picker reflects the signed-in account catalog.
- Added a dedicated `gimp_mcp.provider_cli` for robust GUI subprocess calls without generated `python -c` snippets.
- Studio model picker is editable and refreshes per provider.
- TriForce can be configured independently through `GIMP_MCP_TRIFORCE_TOKEN`.

### Runtime hardening follow-up
- Fixed project-Python/uv path inconsistencies in the Control Center launcher and server startup.
- Provider status checks no longer block the GUI event loop.
- Added stale live-socket detection and safer PID validation.
- Enforced Auto/Live/Batch execution mode in Studio jobs.
- Undo/redo now resynchronizes the visible GIMP document; stale redo snapshots are removed after new edits.
- Fixed JSON boolean/null handling in filter parameters.
- Added safe `layer_fill` and runtime layer references (`$background_layer_id`, `$last_layer_id`).
- Prevented automatic replay of already-started plans after a tool failure.
- Added regression tests and a real ChatGPT -> plan -> GIMP -> preview smoke test.


### Added
- GIMP MCP Studio main workflow with prompt, provider/model, mode, run/pause/stop, undo/redo, follow-up and Make it Cooler controls.
- Persistent ArtworkJob / JobManager state with recovery after application restart.
- Safe PromptRunner translating model output into a strict semantic GIMP allowlist; no model Python or shell execution.
- Bounded repair attempts for malformed plans and failed semantic tool calls.
- Automatic preview checkpoints during artwork jobs.
- MCP artwork job lifecycle, follow-up and Make it Cooler tools.
- Direct persistent GIMP operations for layer rename/visibility/opacity and translation, with XCF synchronization and batch fallback.
- PyQt6 as an explicit project dependency.

### Security
- Model plans are rejected when they request unknown tools or executable code/shell fields.
- Persistent bridge remains a local Unix socket and accepts only structured whitelisted commands.
- Batch isolation remains the fallback whenever direct live editing is unavailable or fails.

### Known limitation
- The updated persistent GIMP plug-in is loaded by GIMP only after the application is restarted. Existing open work is therefore not force-restarted by setup/update logic.
