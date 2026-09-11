# GIMP MCP

Produktionsnaher MCP-Server fuer strukturierte GIMP-3-Automation. Ziel ist eine semantische Art-Editing-API fuer KI-Agenten statt GUI-Klickautomation oder tausender roher PDB-Tools.

## Getesteter Stand

- GIMP 3.2.2 via offizieller `python-fu-eval` BatchProcedure
- MCP Python SDK 2.2.x / `MCPServer`
- XCF-gestuetzte Artwork-Sessions mit Snapshot Undo/Redo
- stabile Layer-IDs ueber GIMP tattoos
- Path Policy gegen Writes ausserhalb freigegebener Roots
- strukturierte Fehlerantworten
- PNG/JPEG/WebP Export und PNG Preview Resource
- PDB Search/Describe
- nicht-destruktive `Gimp.DrawableFilter`-Effekte

## Architektur

`MCP client -> semantic tools -> SessionManager -> GimpBridge -> GIMP python-fu-eval -> libgimp/PDB/DrawableFilter`

Version 0.4 kombiniert crash-isolierte Batch-Aufrufe mit einer optionalen persistenten GIMP-Bridge. Direkte Live-Operationen werden fuer sichere Layer-Mutationen verwendet; bei Ausfall bleibt Batch der deterministische Fallback.

## Installation

```bash
uv sync
uv run pytest -q
uv run gimp-mcp
```

Inspector:

```bash
uv run mcp dev src/gimp_mcp/server.py:mcp
```

Streamable HTTP kann mit dem SDK-Runner gestartet werden:

```bash
uv run mcp run src/gimp_mcp/server.py:mcp --transport streamable-http
```

## MCP Tools

Session/Document: `gimp_status`, `session_create`, `session_open`, `session_info`, `session_undo`, `session_redo`, `session_close`, `document_save_as`.

Layers/Editing: `layer_create`, `layer_delete`, `layer_update`, `layer_reorder`, `layer_fill`, `selection_set`, `text_create`, `transform_layer`.

Effects/Output: `filter_list`, `filter_describe`, `filter_apply`, `preview_render`, `export_image`.

Expert discovery: `pdb_search`, `pdb_describe`.

Preview wird zusaetzlich als binaere MCP Resource `gimp-preview://{session_id}` angeboten.

## Security

Default erlaubte Roots: `~/Pictures`, `~/Downloads`, `~/gimp-mcp/workspace`, `/tmp/gimp-mcp`. Ueberschreiben muss explizit aktiviert werden. `raw_python` wurde aus der normalen Tool-Oberflaeche entfernt.

Konfiguration via Env: `GIMP_MCP_GIMP`, `GIMP_MCP_TIMEOUT`, `GIMP_MCP_PREVIEW_MAX`, `GIMP_MCP_ALLOWED_ROOTS`, `GIMP_MCP_SESSION_ROOT`, `GIMP_MCP_EXPERT`.

## Python/GI Fallstrick

GIMPs Python-Plug-ins muessen mit einer Python-Umgebung laufen, die `gi`/PyGObject besitzt. Eine `uv`-venv darf `/usr/bin/env python3` nicht auf eine inkompatible venv-Python umbiegen. Der Bridge-Prozess entfernt deshalb `VIRTUAL_ENV`, `PYTHONHOME`, `PYTHONPATH` und setzt einen kontrollierten System-`PATH`.

## GIMP 3.2.2 Besonderheit

`Gegl.list_operations()` laesst den getesteten `python-fu-eval` Prozess reproduzierbar abstuerzen. 0.1 verwendet deshalb eine kleine validierte Filter-Registry fuer Discovery. Exakte Filter werden weiterhin dynamisch ueber `Gimp.DrawableFilter.new()` und dessen Config introspektiert. Getestet sind u.a. `gegl:gaussian-blur`, `gegl:brightness-contrast`, `gegl:unsharp-mask`, `gegl:color-temperature`, `gegl:shadows-highlights`.

## Tests

`tests/unit`: Path Policy. `tests/integration`: echte GIMP-Probe, Canvas, Layer, Opacity, XCF und PNG. `tests/test_mcp.py`: echter MCP Client gegen den Server sowie Tool-Discovery und `gimp_status`.

## Control Center and live observation

`GIMP MCP` now includes an optional PyQt6 control surface for operating and observing the server:

```bash
cd ~/gimp-mcp
./scripts/gimp-mcp-control
```

The Control Center provides:

- **Live** — recovered artwork sessions, operation event stream and automatically refreshed previews.
- **Server** — start/stop the local Streamable HTTP MCP endpoint.
- **AI Control** — optional art-director provider/model selection through native official-client account integrations.
- **Settings** — preview interval and size settings stored privately under `~/.local/state/gimp-mcp/control.json`.

Provider credentials are deliberately **not copied into GIMP MCP**. ChatGPT/Codex, Claude Code, Mistral Vibe and Google Antigravity remain owners of their OAuth state. ChatGPT/Codex, Claude Code, Mistral Vibe and Google Antigravity are queried directly. AILinux/TriForce can be configured independently with GIMP_MCP_TRIFORCE_TOKEN.

### AI-client routing rule

When a user explicitly asks to **draw, paint, edit or create an image with GIMP**, an AI client integrating this server should route the task through GIMP MCP rather than silently substituting an unrelated image generator. Image generation may still be used as an explicit source/reference only when the user asks for it or approves a hybrid workflow.

Recommended workflow:

```text
user prompt
  -> session_create/session_open
  -> semantic GIMP edits
  -> preview_render
  -> vision/art review
  -> further edits
  -> document_save_as + export_image
```

`events_recent` exposes the operation timeline for observers. `session_list` includes recovered sessions and preview locations. Sessions survive a server restart as long as their working XCF remains under the configured session root.

## Architecture direction

The current production-safe path remains batch-isolated GIMP execution. The next major bridge is a persistent GIMP 3 plug-in/IPC mode for truly visible edits inside one open GIMP GUI. The semantic MCP layer, session IDs, event bus and Control Center are intentionally transport-independent so a future `PersistentPluginBridge` can replace or complement the current batch bridge without changing AI-facing tools.

## One-shot setup and update checks

Launch `./scripts/gimp-mcp-control` and open **Setup / Updates**. The studio can:

- check required Debian/Ubuntu/AILinux packages and configured APT candidates,
- compare selected upstream versions for GIMP, uv, MCP and PyQt6,
- install/repair system dependencies through PolicyKit after explicit confirmation,
- `uv sync` the project or explicitly upgrade/re-lock dependencies,
- update `uv`,
- fetch repository update state, and
- run pytest plus a real GIMP bridge probe.

The updater distinguishes **distribution candidate** from **upstream latest**. It does not silently replace distro packages with foreign repositories.

## Persistent visible GIMP mode (0.3)

Install the bundled GIMP 3 persistent plug-in:

```bash
./scripts/install-gimp-live-plugin
```

Restart GIMP once. The plug-in starts automatically as a `PERSISTENT` GIMP procedure and creates a user-only Unix socket at:

```text
~/.local/state/gimp-mcp/gimp-live.sock
```

When the socket is available, successful MCP document mutations are mirrored into a visible GIMP display. If GIMP is closed or the live plug-in is unavailable, GIMP MCP falls back to the crash-isolated batch workflow automatically.

The persistent bridge started as a visible document mirror. In 0.4 it also supports a small whitelist of direct in-process layer operations; all unsupported operations remain on the isolated batch path. Arbitrary Python execution is not exposed.

See `docs/CREATIVE_AI_CLOUD.md` for the shared multi-application direction, including a proposed FL Studio MCP adapter.

## GIMP MCP Studio 0.4

The default Control Center view is now **Studio**: prompt -> selected provider/model -> persistent artwork job -> safe semantic GIMP actions -> live preview -> follow-up/export workflow. Jobs survive application restarts and recover as paused instead of silently disappearing.

Model output is treated as untrusted input. The PromptRunner accepts only structured JSON plans using a small semantic allowlist, validates arguments, rejects invented/executable tools, limits repair attempts, and never executes model-provided Python or shell commands.

Direct persistent editing currently covers layer rename, visibility, opacity and translation. The plug-in saves direct mutations back to the session XCF and flushes displays. Other operations continue through the isolated batch path and mirror back into the visible GIMP document.

See `docs/QUICKSTART.md` and `CHANGELOG.md`.

Studio plans can use `$background_layer_id` for the initial layer and `$last_layer_id` for a layer created earlier in the same plan; numeric IDs should never be invented by a model.

### Live preview and export
The Control Center Live view refreshes `preview.png` from the selected artwork session and exposes **EXPORT XCF**, **EXPORT PNG**, and **EXPORT JPG**. Studio exposes compact XCF/PNG/JPG export buttons as well. XCF copies the canonical editable `document.xcf`; PNG/JPG are rendered by GIMP from that same document. The MCP surface also exposes `export_artwork`.
