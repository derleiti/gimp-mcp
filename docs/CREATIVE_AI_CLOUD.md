# Creative AI Cloud direction

GIMP MCP is the first adapter of a broader, host-neutral creative automation architecture.
The product concept is **Creative AI Cloud**: one prompt/control experience, multiple real creative applications.

## Shared architecture

```text
Creative AI Studio
  -> AI provider / AILinux TriForce
  -> Creative job + event bus + approvals
  -> semantic MCP adapter
       -> GIMP adapter (images)
       -> FL Studio adapter (music, proposed)
       -> future Blender/Krita/OBS adapters
```

The common layer owns jobs, prompts, model routing, progress, approval policy, history and live telemetry. Each application adapter owns only host-specific capabilities and state.

## FL Studio MCP feasibility

FL Studio exposes an official Python MIDI Scripting API inside FL Studio. It includes modules for transport, channels, mixer, patterns, playlist, plugins, UI and undo/history-oriented actions. A safe FL Studio adapter should therefore mirror the GIMP architecture:

```text
FL Studio
  -> in-process MIDI Script / bridge
  -> localhost IPC
  -> flstudio-mcp semantic tools
  -> Creative AI Studio
```

Suggested semantic tool groups:

- project/session: inspect, save, render
- transport: play, stop, position, tempo
- channel rack: list, select, mute, volume, pan
- patterns: create/select/rename, sequence operations
- playlist: inspect tracks, place/move clips where supported
- mixer: inspect/select, volume/pan/mute, routing where supported
- plugins: inspect focused plugin/parameters, safe parameter changes
- automation: controlled parameter automation where official APIs permit it
- undo/redo and checkpoints

Do not expose arbitrary Python execution to remote models. Keep IPC local by default and expose semantic, validated commands.

## Product rule

A creative request explicitly naming an application must route to that application's adapter. "Do this in GIMP" must not silently use an image generator; "do this in FL Studio" must not silently synthesize audio elsewhere.
