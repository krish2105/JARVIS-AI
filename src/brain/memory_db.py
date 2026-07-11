"""Structured local memory store (SQLite).

Replaces the flat Markdown files under memories/ with a queryable store that
records, per fact: provenance (where it came from), confidence, created/updated
times, an optional expiry, a sensitivity flag, and whether the user confirmed
it. This is what lets Jarvis distinguish "the user told me their coffee order"
(high confidence, user-sourced) from "a web page said X" (low confidence,
untrusted) — and expire or forget on request.

Uses only the Python standard library (sqlite3), so it is fully unit-testable
here without any extra dependency. Full-text search uses FTS5 when the SQLite
build has it, and falls back to LIKE otherwise.

Provenance rule: content that arrived from a web page, email, or tool result is
NEVER stored as user-approved. Only `source="user"` facts count as confirmed
unless explicitly marked.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "memories" / "jarvis_memory.db"

# Sources that are inherently untrusted — a fact from one of these is stored
# unconfirmed and low-confidence regardless of what the caller passes.
_UNTRUSTED_SOURCES = {"web", "email", "tool", "page", "browser"}


@dataclass
class Fact:
    id: int
    content: str
    source: str
    confidence: float
    sensitive: bool
    confirmed: bool
    created_at: float
    updated_at: float
    expires_at: float | None


class MemoryDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + a lock: the pipeline may touch the store
        # from the turn thread while a state event fires on another.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._fts = False
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                confidence REAL NOT NULL DEFAULT 1.0,
                sensitive INTEGER NOT NULL DEFAULT 0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL
            );
            """
        )
        try:
            self._conn.executescript(
                "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
                "USING fts5(content, content='facts', content_rowid='id');"
            )
            self._fts = True
        except sqlite3.OperationalError:
            self._fts = False  # SQLite built without FTS5; fall back to LIKE
        self._conn.commit()

    # --- writes ----------------------------------------------------------

    def remember(
        self,
        content: str,
        *,
        source: str = "user",
        confidence: float = 1.0,
        sensitive: bool = False,
        confirmed: bool | None = None,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> int:
        now = time.time() if now is None else now
        untrusted = source.lower() in _UNTRUSTED_SOURCES
        if untrusted:
            # Never trust content that came from outside the user.
            confidence = min(confidence, 0.4)
            confirmed = False
        if confirmed is None:
            confirmed = source == "user"
        expires_at = (now + ttl_seconds) if ttl_seconds else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO facts (content, source, confidence, sensitive, confirmed, "
                "created_at, updated_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (content, source, confidence, int(sensitive), int(bool(confirmed)), now, now, expires_at),
            )
            fact_id = cur.lastrowid
            if self._fts:
                self._conn.execute("INSERT INTO facts_fts(rowid, content) VALUES (?,?)", (fact_id, content))
            self._conn.commit()
        return fact_id

    def forget(self, fact_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            if self._fts:
                self._conn.execute("DELETE FROM facts_fts WHERE rowid=?", (fact_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM facts")
            if self._fts:
                self._conn.execute("DELETE FROM facts_fts")
            self._conn.commit()

    def purge_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT id FROM facts WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
        ).fetchall()
        for r in rows:
            self.forget(r["id"])
        return len(rows)

    # --- reads -----------------------------------------------------------

    def _row_to_fact(self, r: sqlite3.Row) -> Fact:
        return Fact(
            id=r["id"], content=r["content"], source=r["source"], confidence=r["confidence"],
            sensitive=bool(r["sensitive"]), confirmed=bool(r["confirmed"]),
            created_at=r["created_at"], updated_at=r["updated_at"], expires_at=r["expires_at"],
        )

    def all_facts(self, now: float | None = None) -> list[Fact]:
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE expires_at IS NULL OR expires_at > ? ORDER BY updated_at DESC",
            (now,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def search(self, query: str, limit: int = 10, now: float | None = None) -> list[Fact]:
        now = time.time() if now is None else now
        if self._fts and query.strip():
            try:
                rows = self._conn.execute(
                    "SELECT f.* FROM facts_fts fts JOIN facts f ON f.id = fts.rowid "
                    "WHERE facts_fts MATCH ? AND (f.expires_at IS NULL OR f.expires_at > ?) "
                    "ORDER BY rank LIMIT ?",
                    (query, now, limit),
                ).fetchall()
                return [self._row_to_fact(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # bad FTS query syntax; fall through to LIKE
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE content LIKE ? AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", now, limit),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def export(self) -> list[dict]:
        return [vars(f) for f in self.all_facts()]

    def close(self) -> None:
        self._conn.close()


def migrate_markdown(memory_dir: Path, db: MemoryDB) -> int:
    """One-time migration: import each line of each memories/*.md file as a
    user-sourced fact. Non-destructive — the .md files are left in place so the
    user can roll back. Returns the number of facts imported."""
    imported = 0
    for md in sorted(memory_dir.glob("*.md")):
        for raw_line in md.read_text(errors="replace").splitlines():
            line = raw_line.strip().lstrip("#-* ").strip()
            if not line:
                continue
            db.remember(line, source="user", confidence=1.0, confirmed=True)
            imported += 1
    return imported
