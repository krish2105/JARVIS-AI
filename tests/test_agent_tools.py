"""Tests for the memory tool's file operations (src/brain/memory.py), the
confirmation-gate/filesystem-scoping logic (src/brain/tools.py), and the
local tool-calling loop's JSON parsing (src/brain/agent.py). All of this is
pure Python with no MLX/mlx-lm/Porcupine/macOS dependency, so it runs
anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.brain.memory as memory  # noqa: E402

# --- memory tool file operations ------------------------------------------


@pytest.fixture()
def memory_module(tmp_path, monkeypatch):
    """Points MEMORY_ROOT at a temp dir so tests never touch the real
    memories/ directory."""
    monkeypatch.setattr(memory, "MEMORY_ROOT", tmp_path)
    return memory


def test_create_and_view_file(memory_module):
    store = memory_module.MemoryStore()
    result = store.create("/memories/notes.txt", "hello\nworld\n")
    assert result == "File created successfully at: /memories/notes.txt"

    viewed = store.view("/memories/notes.txt")
    assert "hello" in viewed
    assert "     1\thello" in viewed


def test_view_empty_directory_is_not_an_error(memory_module):
    store = memory_module.MemoryStore()
    result = store.view("/memories")
    assert "/memories" in result
    assert "does not exist" not in result


def test_str_replace_success(memory_module):
    store = memory_module.MemoryStore()
    store.create("/memories/prefs.txt", "Favorite color: blue\n")
    result = store.str_replace("/memories/prefs.txt", "Favorite color: blue", "Favorite color: green")
    assert "has been edited" in result
    assert "green" in store.view("/memories/prefs.txt")


def test_str_replace_missing_text(memory_module):
    store = memory_module.MemoryStore()
    store.create("/memories/prefs.txt", "hello\n")
    result = store.str_replace("/memories/prefs.txt", "not there", "x")
    assert "No replacement was performed" in result


def test_delete_and_missing_path(memory_module):
    store = memory_module.MemoryStore()
    store.create("/memories/temp.txt", "x")
    assert store.delete("/memories/temp.txt") == "Successfully deleted /memories/temp.txt"
    assert "does not exist" in store.delete("/memories/temp.txt")


def test_rename(memory_module):
    store = memory_module.MemoryStore()
    store.create("/memories/draft.txt", "x")
    result = store.rename("/memories/draft.txt", "/memories/final.txt")
    assert result == "Successfully renamed /memories/draft.txt to /memories/final.txt"
    assert "does not exist" not in store.view("/memories/final.txt")


def test_path_traversal_is_rejected(memory_module):
    store = memory_module.MemoryStore()
    result = store.create("/memories/../../etc/passwd", "pwned")
    assert result.startswith("Error:")


def test_cannot_delete_memory_root(memory_module):
    store = memory_module.MemoryStore()
    assert store.delete("/memories") == "Error: cannot delete the /memories root directory"


def test_memory_dispatch_routes_to_store(memory_module):
    result = memory_module.memory_dispatch({"command": "create", "path": "/memories/x.txt", "file_text": "hi"})
    assert "File created successfully" in result


def test_memory_dispatch_unknown_command(memory_module):
    assert "unknown command" in memory_module.memory_dispatch({"command": "nope"})


# --- confirmation gate / filesystem scoping -------------------------------

from src.brain.tool_types import Tool  # noqa: E402
from src.brain.tools import ToolGuard, _path_in_allowlist  # noqa: E402
from src.system.config import Config  # noqa: E402


def test_path_in_allowlist(tmp_path):
    allowed = tmp_path / "Documents"
    allowed.mkdir()
    inside = allowed / "notes.txt"
    outside = tmp_path / "elsewhere" / "secret.txt"
    assert _path_in_allowlist(str(inside), [allowed])
    assert not _path_in_allowlist(str(outside), [allowed])


def test_guard_allows_when_not_gated():
    cfg = Config(require_confirmation_for=["run_shell_command"])
    guard = ToolGuard(cfg, confirm_fn=lambda *a: False)  # would deny if asked
    tool = Tool(name="read_file", description="", parameters={}, handler=lambda i: "ok")
    allowed, reason = guard.check(tool, {})
    assert allowed is True


def test_guard_denies_without_confirmation():
    cfg = Config(require_confirmation_for=["run_shell_command"])
    guard = ToolGuard(cfg, confirm_fn=lambda *a: False)
    tool = Tool(name="run_shell", description="", parameters={}, handler=lambda i: "ok", confirm_key="run_shell_command")
    allowed, reason = guard.check(tool, {"command": "rm -rf /"})
    assert allowed is False
    assert "confirm" in reason.lower()


def test_guard_allows_with_confirmation():
    cfg = Config(require_confirmation_for=["Write"])
    guard = ToolGuard(cfg, confirm_fn=lambda *a: True)
    tool = Tool(name="write_file", description="", parameters={}, handler=lambda i: "ok", confirm_key="Write")
    allowed, _ = guard.check(tool, {"path": "/x"})
    assert allowed is True


def test_guard_gates_memory_delete():
    cfg = Config(require_confirmation_for=["delete_file"])
    calls = []
    guard = ToolGuard(cfg, confirm_fn=lambda desc, name, inp: calls.append(desc) or False)
    tool = Tool(name="memory", description="", parameters={}, handler=lambda i: "ok")
    allowed, _ = guard.check(tool, {"command": "delete", "path": "/memories/x.txt"})
    assert allowed is False
    assert calls  # confirm_fn was actually invoked


def test_guard_memory_view_never_gated():
    cfg = Config(require_confirmation_for=["delete_file"])
    guard = ToolGuard(cfg, confirm_fn=lambda *a: False)
    tool = Tool(name="memory", description="", parameters={}, handler=lambda i: "ok")
    allowed, _ = guard.check(tool, {"command": "view", "path": "/memories"})
    assert allowed is True


def test_guard_log_writes_structured_jsonl_entry(tmp_path, monkeypatch):
    import json

    import src.brain.tools as tools_module

    calls_path = tmp_path / "tool_calls.jsonl"
    monkeypatch.setattr(tools_module, "TOOL_CALLS_PATH", calls_path)
    monkeypatch.setattr(tools_module, "LOG_PATH", tmp_path / "jarvis.log")
    guard = ToolGuard(Config(), confirm_fn=lambda *a: True)

    guard.log("web_search", {"query": "weather"}, "sunny today")

    entry = json.loads(calls_path.read_text().splitlines()[0])
    assert entry["tool"] == "web_search"
    assert entry["input"] == {"query": "weather"}
    assert entry["result"] == "sunny today"
    assert "timestamp" in entry


# --- local tool-calling loop JSON parsing ---------------------------------

from src.brain.agent import extract_tool_call, run_turn, JarvisSession  # noqa: E402


def test_extract_tool_call_pure_json():
    call = extract_tool_call('{"tool_call": {"name": "web_search", "input": {"query": "weather"}}}')
    assert call == {"name": "web_search", "input": {"query": "weather"}}


def test_extract_tool_call_embedded_in_prose():
    text = 'Sure, let me check.\n{"tool_call": {"name": "memory", "input": {"command": "view", "path": "/memories"}}}\nDone.'
    call = extract_tool_call(text)
    assert call["name"] == "memory"


def test_extract_tool_call_none_for_plain_answer():
    assert extract_tool_call("15% of $340 is $51.") is None


def test_extract_tool_call_missing_input_defaults_empty_dict():
    call = extract_tool_call('{"tool_call": {"name": "web_search"}}')
    assert call == {"name": "web_search", "input": {}}


class _FakeLLM:
    """Replays a fixed sequence of responses, one per .chat() call."""

    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def chat(self, messages: list[dict]) -> str:
        return next(self._responses)


def test_run_turn_final_answer_no_tool_call(monkeypatch):
    monkeypatch.setattr("src.brain.agent.LocalLLM.get", lambda model_id: _FakeLLM(["Hello there."]))
    monkeypatch.setattr("src.brain.agent.build_tools", lambda cfg: {})
    cfg = Config()
    session = JarvisSession()
    reply = run_turn("hi", session, cfg, confirm_fn=lambda *a: True)
    assert reply == "Hello there."
    assert session.messages[0]["role"] == "system"


def test_run_turn_executes_tool_then_answers(monkeypatch):
    responses = [
        '{"tool_call": {"name": "echo", "input": {"text": "hi"}}}',
        "The tool said: hi",
    ]
    monkeypatch.setattr("src.brain.agent.LocalLLM.get", lambda model_id: _FakeLLM(responses))
    echo_tool = Tool(name="echo", description="", parameters={}, handler=lambda i: i["text"])
    monkeypatch.setattr("src.brain.agent.build_tools", lambda cfg: {"echo": echo_tool})
    cfg = Config()
    session = JarvisSession()
    reply = run_turn("say hi", session, cfg, confirm_fn=lambda *a: True)
    assert reply == "The tool said: hi"
    assert any("Tool result for echo" in m["content"] for m in session.messages if m["role"] == "user")


def test_run_turn_denies_unconfirmed_tool(monkeypatch):
    responses = [
        '{"tool_call": {"name": "danger", "input": {}}}',
        "It was denied, as expected.",
    ]
    monkeypatch.setattr("src.brain.agent.LocalLLM.get", lambda model_id: _FakeLLM(responses))
    danger_tool = Tool(name="danger", description="", parameters={}, handler=lambda i: "boom", confirm_key="run_shell_command")
    monkeypatch.setattr("src.brain.agent.build_tools", lambda cfg: {"danger": danger_tool})
    cfg = Config(require_confirmation_for=["run_shell_command"])
    session = JarvisSession()
    reply = run_turn("do the dangerous thing", session, cfg, confirm_fn=lambda *a: False)
    assert reply == "It was denied, as expected."
    tool_result_messages = [m["content"] for m in session.messages if "Tool result for danger" in m["content"]]
    assert "Denied" in tool_result_messages[0]
