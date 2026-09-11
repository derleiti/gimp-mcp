# Changelog

## 0.4.0 - 2026-09-11

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
