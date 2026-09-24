"""
CORTEX - persistent memory for E.V.A. (v0.2).

One SQLite file (data/cortex.db) holds everything:

    episodes  every user/assistant turn, searchable, survives restarts
    facts     durable things about the user ("lives in Breda", "prefers tea")
              with an embedding vector for semantic recall and deduplication

Why SQLite + numpy instead of ChromaDB: a personal memory holds hundreds to a
few thousand facts. Brute-force cosine over that in numpy takes well under a
millisecond, and it removes a heavy dependency and a second data store.
Embeddings come from Ollama (nomic-embed-text, ~270 MB), so no torch or
sentence-transformers load is needed. If the embedding model is missing,
CORTEX degrades to keyword search and keeps working.

Deduplication rule ("newest phrasing wins"): when a new fact is very similar
to an existing one in the same category, the old one is REPLACED. That handles
both restatements ("165 cm" vs "5 foot 5") and corrections ("moved to
Tilburg" replacing "lives in Breda") with one rule and no LLM call.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

import httpx
import numpy as np
from loguru import logger

EmbedFn = Callable[[Sequence[str]], Optional[list]]

# Things memory must never store, even if the user says them out loud.
_SECRET_RX = re.compile(
    r"\b(password|passcode|passwd|pin code|api[ _-]?key|secret key|access token|"
    r"private key|seed phrase|recovery phrase|iban|credit card|cvv)\b"
    r"|\b(?:\d[ -]?){13,19}\b",       # long digit runs that look like card numbers
    re.I,
)
_WORD_RX = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_WORD_RX.findall(text.lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_WORD_RX.findall(a.lower())), set(_WORD_RX.findall(b.lower()))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


class OllamaEmbedder:
    """Sync embedding client. Fails soft: returns None and backs off for 60s."""

    def __init__(self, model: str = "nomic-embed-text", url: str = "http://localhost:11434",
                 timeout: float = 4.0):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout
        self._down_until = 0.0

    def __call__(self, texts: Sequence[str]) -> Optional[list]:
        if not texts or time.time() < self._down_until:
            return None
        try:
            r = httpx.post(f"{self.url}/api/embed",
                           json={"model": self.model, "input": list(texts), "keep_alive": "30m"},
                           timeout=self.timeout)
            r.raise_for_status()
            vecs = r.json().get("embeddings")
            return [np.asarray(v, dtype=np.float32) for v in vecs] if vecs else None
        except Exception as e:
            logger.warning(f"CORTEX: embeddings unavailable ({e}); using keyword search for 60s")
            self._down_until = time.time() + 60
            return None


class Cortex:
    def __init__(self, db_path: str = "./data/cortex.db", embedder: Optional[EmbedFn] = None,
                 dedup_threshold: float = 0.88, jaccard_threshold: float = 0.8):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embed = embedder
        self.dedup_threshold = dedup_threshold
        self.jaccard_threshold = jaccard_threshold
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._fts = self._init_schema()
        logger.info(f"CORTEX: {self.stats()['facts']} facts, {self.stats()['episodes']} turns "
                    f"({'semantic' if embedder else 'keyword'} recall)")

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> bool:
        with self._lock:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, ts REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts);
                CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(session_id);
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL, norm TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    source TEXT NOT NULL DEFAULT 'user',
                    created REAL NOT NULL, updated REAL NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 1, embedding BLOB);
                CREATE INDEX IF NOT EXISTS idx_facts_norm ON facts(norm);
            """)
            try:
                self._db.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                        content, content='episodes', content_rowid='id');
                    CREATE TRIGGER IF NOT EXISTS ep_ai AFTER INSERT ON episodes BEGIN
                        INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS ep_ad AFTER DELETE ON episodes BEGIN
                        INSERT INTO episodes_fts(episodes_fts, rowid, content)
                        VALUES ('delete', old.id, old.content);
                    END;
                """)
                fts = True
            except sqlite3.OperationalError:
                logger.warning("CORTEX: SQLite built without FTS5; episode search uses LIKE")
                fts = False
            self._db.commit()
            return fts

    # ---------------------------------------------------------------- episodes
    def log_turn(self, session_id: str, role: str, content: str) -> None:
        if not content:
            return
        with self._lock:
            self._db.execute("INSERT INTO episodes(session_id, role, content, ts) VALUES (?,?,?,?)",
                             (session_id, role, content, time.time()))
            self._db.commit()

    def recent_turns(self, limit: int = 6, within_hours: float = 24.0,
                     session_id: str | None = None) -> list[dict]:
        """Most recent turns, oldest first, ready to prepend to chat history."""
        since = time.time() - within_hours * 3600
        if session_id:
            rows = self._db.execute(
                "SELECT role, content FROM episodes WHERE session_id=? AND ts>=? "
                "ORDER BY id DESC LIMIT ?", (session_id, since, limit)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT role, content FROM episodes WHERE ts>=? ORDER BY id DESC LIMIT ?",
                (since, limit)).fetchall()
        turns = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        while turns and turns[0]["role"] != "user":   # never start history mid-exchange
            turns.pop(0)
        return turns

    def search_episodes(self, query: str, limit: int = 10) -> list[dict]:
        if self._fts:
            terms = " OR ".join(f'"{w}"' for w in _WORD_RX.findall(query.lower()))
            if not terms:
                return []
            rows = self._db.execute(
                "SELECT e.session_id, e.role, e.content, e.ts FROM episodes_fts f "
                "JOIN episodes e ON e.id=f.rowid WHERE episodes_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (terms, limit)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT session_id, role, content, ts FROM episodes WHERE content LIKE ? "
                "ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------- facts
    def _vec(self, text: str, kind: str = "document") -> Optional[np.ndarray]:
        if not self.embed:
            return None
        prefix = "search_document: " if kind == "document" else "search_query: "
        out = self.embed([prefix + text])
        if not out:
            return None
        v = np.asarray(out[0], dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else None

    def _all_fact_rows(self, category: str | None = None) -> list[sqlite3.Row]:
        if category:
            return self._db.execute("SELECT * FROM facts WHERE category=?", (category,)).fetchall()
        return self._db.execute("SELECT * FROM facts").fetchall()

    def remember(self, text: str, category: str = "general", source: str = "user") -> dict:
        text = (text or "").strip().rstrip(".")
        if len(text) < 3:
            return {"status": "ignored", "reason": "empty"}
        if _SECRET_RX.search(text):
            logger.warning("CORTEX: refused to store something that looks like a secret")
            return {"status": "refused", "reason": "looks like a secret or credential"}
        category = (category or "general").strip().lower()
        norm, now = _norm(text), time.time()

        with self._lock:
            exact = self._db.execute("SELECT id FROM facts WHERE norm=?", (norm,)).fetchone()
            if exact:
                self._db.execute("UPDATE facts SET hits=hits+1, updated=? WHERE id=?", (now, exact["id"]))
                self._db.commit()
                return {"status": "duplicate", "id": exact["id"], "text": text}

        vec = self._vec(text)
        best_id, best_score, best_text = None, 0.0, None
        for row in self._all_fact_rows(category):
            if vec is not None and row["embedding"] is not None:
                score = float(np.dot(vec, np.frombuffer(row["embedding"], dtype=np.float32)))
                threshold = self.dedup_threshold
            else:
                score = _jaccard(text, row["text"])
                threshold = self.jaccard_threshold
            if score >= threshold and score > best_score:
                best_id, best_score, best_text = row["id"], score, row["text"]

        blob = vec.tobytes() if vec is not None else None
        with self._lock:
            if best_id is not None:
                self._db.execute(
                    "UPDATE facts SET text=?, norm=?, source=?, updated=?, hits=hits+1, embedding=? "
                    "WHERE id=?", (text, norm, source, now, blob, best_id))
                self._db.commit()
                logger.info(f"CORTEX: updated fact '{best_text}' -> '{text}'")
                return {"status": "updated", "id": best_id, "text": text, "replaced": best_text}
            cur = self._db.execute(
                "INSERT INTO facts(text, norm, category, source, created, updated, embedding) "
                "VALUES (?,?,?,?,?,?,?)", (text, norm, category, source, now, now, blob))
            self._db.commit()
        logger.info(f"CORTEX: new fact [{category}] '{text}'")
        return {"status": "added", "id": cur.lastrowid, "text": text}

    def embed_query(self, text: str) -> Optional[np.ndarray]:
        """Embed a user message once; reuse the vector for recall AND skill matching."""
        return self._vec(text, kind="query")

    def recall(self, query: str = "", k: int = 6, min_score: float = 0.35,
               query_vec: Optional[np.ndarray] = None) -> list[dict]:
        """Most relevant facts for a query. Empty query returns the most-used facts."""
        rows = self._all_fact_rows()
        if not rows:
            return []
        if not query.strip():
            ranked = sorted(rows, key=lambda r: (r["hits"], r["updated"]), reverse=True)
            return [self._fact(r) for r in ranked[:k]]
        qv = query_vec if query_vec is not None else self._vec(query, kind="query")
        scored = []
        for r in rows:
            if qv is not None and r["embedding"] is not None:
                s = float(np.dot(qv, np.frombuffer(r["embedding"], dtype=np.float32)))
            else:
                s = _jaccard(query, r["text"])
                s = s if s >= 0.15 else 0.0
            scored.append((s, r))
        floor = min_score if qv is not None else 0.15
        scored = [(s, r) for s, r in scored if s >= floor]
        scored.sort(key=lambda x: (x[0], x[1]["hits"]), reverse=True)
        return [{**self._fact(r), "score": round(s, 3)} for s, r in scored[:k]]

    def forget(self, query: str, threshold: float = 0.6) -> dict:
        """Delete the single best-matching fact if the match is clear; else return candidates."""
        matches = self.recall(query, k=3, min_score=0.0)
        if not matches:
            return {"status": "not_found"}
        top = matches[0]
        clear = top.get("score", 0) >= (threshold if self.embed else 0.4)
        if not clear:
            return {"status": "ambiguous", "candidates": [m["text"] for m in matches]}
        with self._lock:
            self._db.execute("DELETE FROM facts WHERE id=?", (top["id"],))
            self._db.commit()
        logger.info(f"CORTEX: forgot '{top['text']}'")
        return {"status": "forgotten", "text": top["text"]}

    def all_facts(self, limit: int = 200) -> list[dict]:
        rows = self._db.execute("SELECT * FROM facts ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [self._fact(r) for r in rows]

    @staticmethod
    def _fact(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "text": r["text"], "category": r["category"],
                "source": r["source"], "hits": r["hits"]}

    def stats(self) -> dict:
        f = self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        e = self._db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        return {"facts": f, "episodes": e, "db": str(self.db_path),
                "recall": "semantic" if self.embed else "keyword"}

    def close(self) -> None:
        self._db.close()


_cortex: Cortex | None = None


def get_cortex() -> Cortex:
    """Process-wide CORTEX configured from config/settings.yaml."""
    global _cortex
    if _cortex is None:
        from core.settings import get_settings
        s = get_settings()
        mem, local = s.get("memory", {}), s.get("inference", {}).get("local", {})
        embedder = None
        if mem.get("embed_model"):
            embedder = OllamaEmbedder(model=mem["embed_model"],
                                      url=local.get("base_url", "http://localhost:11434"))
        _cortex = Cortex(db_path=mem.get("cortex_path", "./data/cortex.db"), embedder=embedder,
                         dedup_threshold=float(mem.get("dedup_threshold", 0.88)))
    return _cortex
