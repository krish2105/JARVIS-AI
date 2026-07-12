"""Local RAG over the user's files and notes.

Retrieval is fully local. Documents are chunked and indexed in SQLite; search is
HYBRID:
- BM25 lexical ranking via SQLite FTS5 (exact keyword matches), and
- semantic similarity via a local MiniLM embedding model (src/brain/skills/
  embeddings.py), stored per-chunk as a float32 vector.
The two rankings are fused with Reciprocal Rank Fusion. If the embedding model
isn't available, search degrades gracefully to BM25 alone. No cloud, no torch.

Sources:
- Text files under config `rag.folders` — bounded (small files, capped count)
  and incremental (unchanged files skipped by mtime).
- Apple Notes (via AppleScript `plaintext`), if `rag.include_notes` is on.

Config is read FRESH (load_config) on each index/search so that editing folders
in the Settings UI takes effect without restarting the pipeline, and both the
HUD and pipeline processes always agree on what to index.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from src.brain.skills.embeddings import DIM, get_embedder
from src.system.config import RagConfig, load_config

logger = logging.getLogger("jarvis.documents")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "memories" / "jarvis_docs.db"

_CHUNK_CHARS = 800
_CHUNK_OVERLAP = 120
_STALE_SECONDS = 300  # re-scan folders at most this often on search
_CANDIDATES = 25  # per-ranker candidate pool before fusion
_RRF_K = 60  # Reciprocal Rank Fusion constant

# US-ASCII unit/record separators — unambiguous split of AppleScript output.
_FS = "\x1f"
_RS = "\x1e"

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


def _rag() -> RagConfig:
    """Current RAG settings, read fresh from config.yaml each time."""
    return load_config().rag


def _chunk(text: str) -> list[str]:
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
    tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in tokens)


class DocIndex:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # The HUD (Settings "Reindex") and the pipeline (search) may both touch
        # this db from separate processes — wait rather than error on a lock.
        self._conn.execute("PRAGMA busy_timeout=5000")
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
            CREATE TABLE IF NOT EXISTS vectors (rowid INTEGER PRIMARY KEY, vec BLOB NOT NULL);
            """
        )
        self._conn.commit()

    # --- ingestion --------------------------------------------------------
    def _candidate_files(self, rag: RagConfig) -> tuple[list[Path], bool]:
        exts = {e.lower() for e in rag.extensions}
        max_bytes = rag.max_file_kb * 1024
        cap = rag.max_files
        found: list[Path] = []
        truncated = False
        for folder in rag.folders:
            root = Path(folder).expanduser()
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in exts:
                    continue
                try:
                    if p.stat().st_size > max_bytes:
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

    def _purge(self, where: str, params: tuple) -> None:
        """Delete chunks matching a (trusted, non-user) condition AND their
        vectors so no orphan embeddings linger."""
        # `where` is always a trusted literal from this module ("path = ?" /
        # "source = 'note'") — never user input. Values ride through params.
        rowids = [r[0] for r in self._conn.execute(f"SELECT rowid FROM chunks WHERE {where}", params)]  # noqa: S608
        if rowids:
            self._conn.executemany("DELETE FROM vectors WHERE rowid = ?", [(r,) for r in rowids])
            self._conn.execute(f"DELETE FROM chunks WHERE {where}", params)  # noqa: S608

    def _index_one(self, path: str, title: str, source: str, text: str, rag: RagConfig) -> int:
        self._purge("path = ?", (path,))
        pieces = _chunk(text)
        if not pieces:
            return 0
        vecs = get_embedder().embed(pieces) if rag.use_embeddings else None
        for i, body in enumerate(pieces):
            cur = self._conn.execute(
                "INSERT INTO chunks (body, path, title, source, ord) VALUES (?,?,?,?,?)",
                (body, path, title, source, i),
            )
            if vecs is not None:
                self._conn.execute(
                    "INSERT OR REPLACE INTO vectors (rowid, vec) VALUES (?, ?)",
                    (cur.lastrowid, vecs[i].tobytes()),
                )
        return len(pieces)

    def _ingest_notes(self, rag: RagConfig) -> int:
        if not rag.include_notes:
            return 0
        try:
            cmd = ["osascript"]
            for line in _NOTES_SCRIPT:
                cmd += ["-e", line]
            cmd.append(str(rag.max_files))
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
            n += self._index_one(path, name.strip() or "Note", "note", body, rag)
        return n

    def reindex(self, force: bool = False) -> str:
        rag = _rag()
        with self._lock:
            known = {row["path"]: row["mtime"] for row in self._conn.execute("SELECT path, mtime FROM files")}
            seen: set[str] = set()
            files_indexed = 0
            candidates, truncated = self._candidate_files(rag)
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
                self._index_one(sp, p.name, "file", text, rag)
                self._conn.execute(
                    "INSERT INTO files (path, mtime) VALUES (?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime",
                    (sp, mtime),
                )
                files_indexed += 1
            # Drop files that disappeared — only if the cap didn't truncate the
            # scan (otherwise unseen files may just be beyond the cap).
            if not truncated:
                for gone in set(known) - seen:
                    self._purge("path = ?", (gone,))
                    self._conn.execute("DELETE FROM files WHERE path = ?", (gone,))
            capped_note = " (file cap reached — raise rag.max_files to index more)" if truncated else ""
            self._purge("source = 'note'", ())
            notes_chunks = self._ingest_notes(rag)
            self._conn.commit()
            self._last_scan = time.time()
        total = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        mode = "semantic + keyword" if rag.use_embeddings and get_embedder().available() else "keyword"
        return (
            f"Indexed {files_indexed} changed file(s), {notes_chunks} note chunk(s). "
            f"{total} chunks total ({mode} search).{capped_note}"
        )

    def _maybe_refresh(self) -> None:
        empty = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
        if empty or (time.time() - self._last_scan) > _STALE_SECONDS:
            try:
                self.reindex()
            except Exception:  # noqa: BLE001
                logger.exception("reindex during search failed")

    # --- retrieval --------------------------------------------------------
    def _bm25(self, query: str) -> list[int]:
        match = _fts_query(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, bm25(chunks) AS score FROM chunks WHERE chunks MATCH ? "
                "ORDER BY score LIMIT ?",
                (match, _CANDIDATES),
            ).fetchall()
        return [r["rowid"] for r in rows]

    def _semantic(self, query: str, rag: RagConfig) -> list[int]:
        if not rag.use_embeddings:
            return []
        qv = get_embedder().embed([query])
        if qv is None:
            return []
        with self._lock:
            vrows = self._conn.execute("SELECT rowid, vec FROM vectors").fetchall()
        if not vrows:
            return []
        ids = np.fromiter((r["rowid"] for r in vrows), dtype=np.int64, count=len(vrows))
        mat = np.frombuffer(b"".join(r["vec"] for r in vrows), dtype=np.float32).reshape(len(vrows), DIM)
        sims = mat @ qv[0]
        top = np.argsort(-sims)[:_CANDIDATES]
        return [int(ids[i]) for i in top]

    def search(self, query: str, k: int = 5) -> list[dict]:
        self._maybe_refresh()
        rag = _rag()
        bm = self._bm25(query)
        sem = self._semantic(query, rag)
        # Reciprocal Rank Fusion of the two ranked candidate lists.
        scores: dict[int, float] = {}
        for rank, rid in enumerate(bm):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, rid in enumerate(sem):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (_RRF_K + rank)
        if not scores:
            return []
        top_ids = sorted(scores, key=lambda r: scores[r], reverse=True)[:k]
        placeholders = ",".join("?" * len(top_ids))  # only '?'s — values in top_ids
        with self._lock:
            rows = {
                r["rowid"]: dict(r)
                for r in self._conn.execute(
                    f"SELECT rowid, body, path, title, source FROM chunks WHERE rowid IN ({placeholders})",  # noqa: S608
                    top_ids,
                )
            }
        return [rows[rid] for rid in top_ids if rid in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]


_index: DocIndex | None = None
_index_lock = threading.Lock()


def get_index() -> DocIndex:
    global _index
    with _index_lock:
        if _index is None:
            _index = DocIndex()
        return _index


def _label(row: dict) -> str:
    if row["source"] == "note":
        return f"Note: {row['title']}"
    return Path(row["path"]).name


def search_documents(query: str, k: int = 5) -> tuple[str, list[str]]:
    """Retrieve the top snippets for `query`. Returns (context_text, sources)."""
    query = (query or "").strip()
    if not query:
        return "Error: what should I look for in your notes and files?", []
    rows = get_index().search(query, k)
    if not rows:
        return "I couldn't find anything about that in your indexed notes and files.", []
    blocks, sources = [], []
    for r in rows:
        label = _label(r)
        sources.append(label)
        blocks.append(f"[{label}]\n{' '.join(r['body'].split())}")
    return "\n\n".join(blocks), sources


def reindex_documents() -> str:
    return get_index().reindex(force=True)
