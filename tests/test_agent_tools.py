"""Tests for the confirmation-gate/filesystem-scoping logic in
src/brain/tools_config.py and the memory tool's file operations in
src/brain/memory.py. These run anywhere (no MLX/Porcupine/macOS needed) —
they exercise pure Python logic against a real temp directory.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def memory_module(tmp_path, monkeypatch):
    """Reloads src.brain.memory with MEMORY_ROOT pointed at a temp dir, so
    tests never touch the real memories/ directory."""
    import src.brain.memory as memory

    monkeypatch.setattr(memory, "MEMORY_ROOT", tmp_path)
    memory._store = memory.MemoryStore()
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


# --- confirmation gate / filesystem scoping -------------------------------

from src.brain.tools_config import _path_in_allowlist, _tool_needs_confirmation  # noqa: E402


def test_path_in_allowlist(tmp_path):
    allowed = tmp_path / "Documents"
    allowed.mkdir()
    inside = allowed / "notes.txt"
    outside = tmp_path / "elsewhere" / "secret.txt"
    assert _path_in_allowlist(str(inside), [allowed])
    assert not _path_in_allowlist(str(outside), [allowed])


def test_write_requires_confirmation():
    needs, desc = _tool_needs_confirmation("Write", {"file_path": "/x"}, ["Write"])
    assert needs is True
    assert "/x" in desc


def test_bash_requires_confirmation_via_alias():
    needs, _ = _tool_needs_confirmation("Bash", {"command": "rm -rf /"}, ["run_shell_command"])
    assert needs is True


def test_read_never_requires_confirmation():
    needs, _ = _tool_needs_confirmation("Read", {"file_path": "/x"}, ["Write", "run_shell_command"])
    assert needs is False


def test_gmail_send_requires_confirmation():
    needs, desc = _tool_needs_confirmation(
        "mcp__gmail__send_email", {"to": "me@example.com"}, ["send_email"]
    )
    assert needs is True
    assert "me@example.com" in desc
