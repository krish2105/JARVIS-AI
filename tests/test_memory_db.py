"""Tests for the structured SQLite memory store (src/brain/memory_db.py).
Pure stdlib (sqlite3) — no external dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.memory_db import MemoryDB, migrate_markdown  # noqa: E402


def _db(tmp_path) -> MemoryDB:
    return MemoryDB(db_path=tmp_path / "mem.db")


def test_remember_and_search(tmp_path):
    db = _db(tmp_path)
    db.remember("The user's coffee order is a flat white")
    db.remember("The user's dog is named Pixel")
    hits = db.search("coffee")
    assert len(hits) == 1
    assert "flat white" in hits[0].content


def test_user_fact_is_confirmed_high_confidence(tmp_path):
    db = _db(tmp_path)
    fid = db.remember("User prefers metric units", source="user")
    fact = next(f for f in db.all_facts() if f.id == fid)
    assert fact.confirmed is True
    assert fact.confidence == 1.0


def test_untrusted_source_is_never_confirmed(tmp_path):
    db = _db(tmp_path)
    fid = db.remember("Buy sketchy pills, says a web page", source="web", confidence=1.0, confirmed=True)
    fact = next(f for f in db.all_facts() if f.id == fid)
    # Even though the caller passed confidence=1.0 and confirmed=True, an
    # untrusted source is capped and never marked confirmed.
    assert fact.confirmed is False
    assert fact.confidence <= 0.4


def test_forget_removes_fact(tmp_path):
    db = _db(tmp_path)
    fid = db.remember("temporary note")
    assert db.forget(fid) is True
    assert db.all_facts() == []
    assert db.forget(9999) is False


def test_expiry_hides_and_purges(tmp_path):
    db = _db(tmp_path)
    db.remember("ephemeral", ttl_seconds=100, now=1000.0)
    # At t=1050 still visible; at t=2000 expired.
    assert len(db.all_facts(now=1050.0)) == 1
    assert db.all_facts(now=2000.0) == []
    assert db.search("ephemeral", now=2000.0) == []
    purged = db.purge_expired(now=2000.0)
    assert purged == 1


def test_reset_clears_all(tmp_path):
    db = _db(tmp_path)
    db.remember("a")
    db.remember("b")
    db.reset()
    assert db.all_facts() == []


def test_export_roundtrip(tmp_path):
    db = _db(tmp_path)
    db.remember("exportable fact")
    exported = db.export()
    assert len(exported) == 1
    assert exported[0]["content"] == "exportable fact"
    assert "confidence" in exported[0] and "source" in exported[0]


def test_remember_recall_tool_handlers(tmp_path, monkeypatch):
    import src.brain.tools as tools
    monkeypatch.setattr(tools, "_memory_db", MemoryDB(db_path=tmp_path / "mem.db"))

    assert "Remembered" in tools._remember({"content": "User's timezone is CET"})
    assert tools._remember({"content": "   "}).startswith("Error")

    out = tools._recall({"query": "timezone"})
    assert "CET" in out
    assert tools._recall({"query": "nonexistent-xyz"}) == "No matching memories."


def test_migrate_markdown(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    (mem_dir / "preferences.md").write_text(
        "# User preferences\n\n- Likes tea\n- Wake word: jarvis\n\n"
    )
    db = MemoryDB(db_path=tmp_path / "mem.db")
    count = migrate_markdown(mem_dir, db)
    # Header + two bullets = 3 non-empty content lines (blank lines skipped).
    assert count == 3
    assert any("Likes tea" in f.content for f in db.all_facts())
    # Non-destructive: the source file is left in place.
    assert (mem_dir / "preferences.md").exists()
