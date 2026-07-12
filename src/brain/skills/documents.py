"""Local RAG over the user's files and notes.

Retrieval is fully local and dependency-free: documents are chunked and indexed
in a SQLite FTS5 table, ranked with FTS5's built-in BM25. No embedding model, no
cloud, no torch — consistent with the project's "everything runs on the Mac"
stance and the FTS5 approach already used by memory_db.py.

Sources:
- Text files (.md/.txt/…) under the folders in config `rag.folders`. Indexing is
  bounded (small files only, capped count) and incremental (unchanged files, by
  mtime, are skipped).
- Apple Notes (via AppleScript `plaintext`), if `rag.include_notes` is on.

The `search_documents` tool returns the top matching snippets with their source;
the agent reads those and composes the answer (standard retrieve-then-read RAG).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from src.system.config import Config

logger = logging.getLogger("jarvis.documents")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "memories" / "jarvis_docs.db"

_CHUNK_CHARS = 800
_CHUNK_OVERLAP = 120
_STALE_SECONDS = 300  # re-scan folders at most this often on search

# US-ASCII field/record separators for unambiguous AppleScript output.
_FS = ""
_RS = ""

_NOTES_SCRIPT = [
    "on run argv",
    "with timeout of 45 seconds",
    "set maxN to (item 1 of argv) as integer",
    'tell application "Notes"',
    'set out to ""',
    "set c to 0",
    "repeat with n in notes",
    "if c ≥ maxN then exit repeat",
    f'set out to out & (id of n) & "{_FS}" & (name of n) & "{_FS}" & (plaintext of n) & "{_RS}"',
    "set c to c + 1",
    "end repeat",
    "end tell",
    "end timeout",
    "return out",
    "end run",
]


def _chunk(text: str) -> list[str]:
    """Pack blank-line-separated paragraphs into ~_CHUNK_CHARS windows; hard-
    split any single paragraph that's longer than a window."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > _CHUNK_CHARS:
            if cur:
                chunks.append(cur)
                cur = ""
            step = _CHUNK_CHARS - _CHUNK_OVERLAP
            for i in range(0, len(p), step):
                chunks.append(p[i : i + _CHUNK_CHARS])
            continue
        if len(cur) + len(p) + 1 <= _CHUNK_CHARS:
            cur = (cur + "\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression: OR of quoted word
    tokens, so punctuation in the query can't be read as FTS5 operators."""
    tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in tokens)


class DocIndex:
    def __init__(self, cfg: Config, db_path: Path = DEFAULT_DB_PATH):
        self.cfg = cfg
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._last_scan = 0.0
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                body, path UNINDEXED, title UNINDEXED, source UNINDEXED, ord UNINDEXED
            );
            """
        )
        self._conn.commit()

    # --- ingestion --------------------------------------------------------
    def _candidate_files(self) -> tuple[list[Path], bool]:
        """Return (files, truncated). `truncated` is True if the max_files cap
        stopped the scan before all matching files were seen — the caller must
        then NOT treat unseen known files as deleted."""
        exts = {e.lower() for e in self.cfg.rag.extensions}
        max_kb = self.cfg.rag.max_file_kb
        cap = self.cfg.rag.max_files
        found: list[Path] = []
        truncated = False
        for folder in self.cfg.rag.folders:
            root = Path(folder).expanduser()
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in exts:
                    continue
                try:
                    if p.stat().st_size > max_kb * 1024:
                        continue
                except OSError:
                    continue
                if len(found) >= cap:
                    truncated = True
                    break
                found.append(p)
            if truncated:
                break
        return found, truncated

    def _index_one(self, path: str, title: str, source: str, text: str) -> int:
        self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        pieces = _chunk(text)
        self._conn.executemany(
            "INSERT INTO chunks (body, path, title, source, ord) VALUES (?,?,?,?,?)",
            [(c, path, title, source, i) for i, c in enumerate(pieces)],
        )
        return len(pieces)

    def _ingest_notes(self) -> int:
        if not self.cfg.rag.include_notes:
            return 0
        try:
            cmd = ["osascript"]
            for line in _NOTES_SCRIPT:
                cmd += ["-e", line]
            cmd.append(str(self.cfg.rag.max_files))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0
        if r.returncode != 0:
            logger.info("notes ingest skipped: %s", r.stderr.strip()[:120])
            return 0
        n = 0
        for rec in r.stdout.split(_RS):
            rec = rec.strip()
            if not rec:
                continue
            note_id, _, rest = rec.partition(_FS)
            name, _, body = rest.partition(_FS)
            path = f"note://{note_id.strip()}"
            n += self._index_one(path, name.strip() or "Note", "note", body)
        return n

    def reindex(self, force: bool = False) -> str:
        """Bring the index up to date. Incremental by file mtime; notes are
        re-read each time (they have no cheap change signal)."""
        with self._lock:
            known = {row["path"]: row["mtime"] for row in self._conn.execute("SELECT path, mtime FROM files")}
            seen: set[str] = set()
            files_indexed = 0
            candidates, truncated = self._candidate_files()
            for p in candidates:
                sp = str(p)
                seen.add(sp)
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if not force and known.get(sp) == mtime:
                    continue
                try:
                    text = p.read_text(errors="replace")
                except OSError:
                    continue
                self._index_one(sp, p.name, "file", text)
                self._conn.execute(
                    "INSERT INTO files (path, mtime) VALUES (?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime",
                    (sp, mtime),
                )
                files_indexed += 1
            # Drop files that disappeared from disk — but ONLY when we scanned
            # everything. If the cap truncated the scan, unseen files may just be
            # beyond the cap, not deleted, so leave them indexed.
            if not truncated:
                for gone in set(known) - seen:
                    self._conn.execute("DELETE FROM chunks WHERE path = ?", (gone,))
                    self._conn.execute("DELETE FROM files WHERE path = ?", (gone,))
            capped_note = " (file cap reached — increase rag.max_files to index more)" if truncated else ""
            # Notes: clear old note chunks, re-ingest.
            self._conn.execute("DELETE FROM chunks WHERE source = 'note'")
            notes_chunks = self._ingest_notes()
            self._conn.commit()
            self._last_scan = time.time()
        total = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return (
            f"Indexed {files_indexed} changed file(s), {notes_chunks} note chunk(s). "
            f"{total} chunks total.{capped_note}"
        )

    def _maybe_refresh(self) -> None:
        empty = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
        if empty or (time.time() - self._last_scan) > _STALE_SECONDS:
            try:
                self.reindex()
            except Exception:  # noqa: BLE001 - retrieval must still try even if a scan fails
                logger.exception("reindex during search failed")

    # --- retrieval --------------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict]:
        self._maybe_refresh()
        match = _fts_query(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT body, path, title, source, bm25(chunks) AS score "
                "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
                (match, k),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]


_index: DocIndex | None = None
_index_lock = threading.Lock()


def get_index(cfg: Config) -> DocIndex:
    global _index
    with _index_lock:
        if _index is None:
            _index = DocIndex(cfg)
        return _index


def _label(row: dict) -> str:
    if row["source"] == "note":
        return f"Note: {row['title']}"
    return Path(row["path"]).name


def search_documents(cfg: Config, query: str, k: int = 5) -> tuple[str, list[str]]:
    """Retrieve the top snippets for `query`. Returns (context_text, sources)."""
    query = (query or "").strip()
    if not query:
        return "Error: what should I look for in your notes and files?", []
    rows = get_index(cfg).search(query, k)
    if not rows:
        return (
            "I couldn't find anything about that in your indexed notes and files.",
            [],
        )
    blocks = []
    sources = []
    for r in rows:
        label = _label(r)
        sources.append(label)
        snippet = " ".join(r["body"].split())
        blocks.append(f"[{label}]\n{snippet}")
    return "\n\n".join(blocks), sources


def reindex_documents(cfg: Config) -> str:
    return get_index(cfg).reindex(force=True)
