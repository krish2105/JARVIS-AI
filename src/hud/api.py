"""Read-mostly backend handlers for the React HUD app's request/response
side-channel (see src/hud/server.py's _handle_request). Runs inside the
HUD process (src.main), which already has filesystem access to
config.yaml, memories/, and the log files — none of this needs to go
through the voice pipeline process.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.brain.memory import MEMORY_ROOT, MemoryStore
from src.system.config import ConfigValidationError, load_config, update_config_yaml

LOG_DIR = Path.home() / "Library" / "Logs"
TRANSCRIPT_PATH = LOG_DIR / "jarvis_transcript.jsonl"
TOOL_CALLS_PATH = LOG_DIR / "jarvis_tool_calls.jsonl"

# Settings the React Settings view is allowed to change. Kept as an
# allowlist so a malformed request can't smuggle arbitrary keys into
# config.yaml.
_EDITABLE_CONFIG_PATHS = {
    "wake_word",
    "voice",
    "model.local",
    "model.local_heavy",
    "audio.wake_word_sensitivity",
    "audio.vad_silence_ms",
    "audio.max_record_seconds",
    "filesystem_allowlist",
    "require_confirmation_for",
    "onboarded",
}


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()[-limit:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def list_memories(_params: dict) -> dict:
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    files = [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(MEMORY_ROOT.iterdir())
        if f.is_file() and not f.name.startswith(".")
    ]
    return {"files": files}


def read_memory(params: dict) -> dict:
    name = params.get("name", "")
    store = MemoryStore()
    content = store.view(f"/memories/{name}")
    if content.startswith("Error") or "does not exist" in content:
        return {"error": content}
    return {"name": name, "content": content}


def delete_memory(params: dict) -> dict:
    name = params.get("name", "")
    store = MemoryStore()
    result = store.delete(f"/memories/{name}")
    return {"result": result}


def list_transcript(params: dict) -> dict:
    limit = int(params.get("limit", 50))
    return {"turns": _tail_jsonl(TRANSCRIPT_PATH, limit)}


def list_tool_calls(params: dict) -> dict:
    limit = int(params.get("limit", 50))
    return {"calls": _tail_jsonl(TOOL_CALLS_PATH, limit)}


def get_config(_params: dict) -> dict:
    return {"config": load_config().redacted(), "editable_paths": sorted(_EDITABLE_CONFIG_PATHS)}


def save_config(params: dict) -> dict:
    flat_patch = params.get("patch", {}) or {}
    rejected = [k for k in flat_patch if k not in _EDITABLE_CONFIG_PATHS]
    if rejected:
        return {"error": f"not editable: {rejected}"}

    nested_patch: dict = {}
    for dotted_key, value in flat_patch.items():
        parts = dotted_key.split(".")
        node = nested_patch
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    try:
        update_config_yaml(nested_patch)
    except ConfigValidationError as e:
        return {"error": str(e)}
    return {"ok": True, "config": load_config().redacted()}


HANDLERS = {
    "list_memories": list_memories,
    "read_memory": read_memory,
    "delete_memory": delete_memory,
    "list_transcript": list_transcript,
    "list_tool_calls": list_tool_calls,
    "get_config": get_config,
    "save_config": save_config,
}
