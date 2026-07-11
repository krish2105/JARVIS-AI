"""Tests for src/hud/api.py — the backend handlers behind the React app's
request/response side-channel (memory browser, transcript/activity logs,
settings panel)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.brain.memory as memory_module  # noqa: E402
import src.hud.api as api  # noqa: E402


@pytest.fixture()
def memory_dirs(tmp_path, monkeypatch):
    """Points both api.py's and memory.py's MEMORY_ROOT at the same temp
    dir — api.py imported its own reference at module-load time, and
    MemoryStore's internal _resolve() reads memory.py's own module
    global, so both need patching to stay consistent."""
    monkeypatch.setattr(api, "MEMORY_ROOT", tmp_path)
    monkeypatch.setattr(memory_module, "MEMORY_ROOT", tmp_path)
    return tmp_path


def test_list_memories_empty(memory_dirs):
    assert api.list_memories({}) == {"files": []}


def test_list_memories_lists_files_with_sizes(memory_dirs):
    (memory_dirs / "notes.md").write_text("hello")
    result = api.list_memories({})
    assert result["files"] == [{"name": "notes.md", "size": 5}]


def test_list_memories_skips_hidden_files(memory_dirs):
    (memory_dirs / ".gitkeep").write_text("")
    (memory_dirs / "visible.md").write_text("x")
    result = api.list_memories({})
    assert [f["name"] for f in result["files"]] == ["visible.md"]


def test_read_memory_returns_content(memory_dirs):
    (memory_dirs / "prefs.md").write_text("coffee: black")
    result = api.read_memory({"name": "prefs.md"})
    assert "coffee: black" in result["content"]


def test_read_memory_missing_file_returns_error(memory_dirs):
    result = api.read_memory({"name": "nope.md"})
    assert "error" in result


def test_delete_memory(memory_dirs):
    (memory_dirs / "temp.md").write_text("x")
    result = api.delete_memory({"name": "temp.md"})
    assert "Successfully deleted" in result["result"]
    assert not (memory_dirs / "temp.md").exists()


def test_list_transcript_reads_jsonl(tmp_path, monkeypatch):
    transcript_path = tmp_path / "transcript.jsonl"
    entries = [{"transcript": "hi", "reply": "hello"}, {"transcript": "bye", "reply": "goodbye"}]
    transcript_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(api, "TRANSCRIPT_PATH", transcript_path)

    result = api.list_transcript({"limit": 50})
    assert result["turns"] == entries


def test_list_transcript_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "TRANSCRIPT_PATH", tmp_path / "does-not-exist.jsonl")
    assert api.list_transcript({}) == {"turns": []}


def test_list_transcript_respects_limit(tmp_path, monkeypatch):
    transcript_path = tmp_path / "transcript.jsonl"
    entries = [{"transcript": str(i), "reply": str(i)} for i in range(5)]
    transcript_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(api, "TRANSCRIPT_PATH", transcript_path)

    result = api.list_transcript({"limit": 2})
    assert result["turns"] == entries[-2:]


def test_list_tool_calls_reads_jsonl(tmp_path, monkeypatch):
    calls_path = tmp_path / "tool_calls.jsonl"
    entry = {"tool": "web_search", "input": {"query": "weather"}, "result": "sunny"}
    calls_path.write_text(json.dumps(entry) + "\n")
    monkeypatch.setattr(api, "TOOL_CALLS_PATH", calls_path)

    result = api.list_tool_calls({})
    assert result["calls"] == [entry]


def test_list_tool_calls_skips_malformed_lines(tmp_path, monkeypatch):
    calls_path = tmp_path / "tool_calls.jsonl"
    calls_path.write_text("not json\n" + json.dumps({"tool": "x"}) + "\n")
    monkeypatch.setattr(api, "TOOL_CALLS_PATH", calls_path)

    result = api.list_tool_calls({})
    assert result["calls"] == [{"tool": "x"}]


def test_get_config_returns_redacted_config_and_editable_paths(monkeypatch):
    fake_cfg = type("FakeCfg", (), {"redacted": lambda self: {"wake_word": "jarvis"}})()
    monkeypatch.setattr(api, "load_config", lambda: fake_cfg)

    result = api.get_config({})
    assert result["config"] == {"wake_word": "jarvis"}
    assert "model.local" in result["editable_paths"]


def test_save_config_rejects_non_editable_keys(monkeypatch):
    result = api.save_config({"patch": {"not_a_real_setting": "x"}})
    assert "error" in result


def test_save_config_applies_editable_patch(monkeypatch):
    captured = {}
    monkeypatch.setattr(api, "update_config_yaml", lambda patch: captured.update(nested=patch))
    fake_cfg = type("FakeCfg", (), {"redacted": lambda self: {"voice": "af_heart"}})()
    monkeypatch.setattr(api, "load_config", lambda: fake_cfg)

    result = api.save_config({"patch": {"voice": "af_heart", "model.local": "some-model"}})

    assert result["ok"] is True
    assert captured["nested"] == {"voice": "af_heart", "model": {"local": "some-model"}}
