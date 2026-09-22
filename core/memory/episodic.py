"""
CORTEX - Episodic Memory: SQLite conversation history.
"""

import sqlite3
import json
from pathlib import Path
from loguru import logger


class EpisodicMemory:
    def __init__(self, db_path: str = "./data/sqlite/eva.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                persona TEXT DEFAULT 'eva',
                metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                summary TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                outcome TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations(created_at);
        """)
        self._conn.commit()
        logger.info("CORTEX Episodic: Database initialized")

    def add_message(self, session_id: str, role: str, content: str, persona: str = "eva", metadata: dict | None = None) -> int:
        cursor = self._conn.execute(
            "INSERT INTO conversations (session_id, role, content, persona, metadata) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, persona, json.dumps(metadata or {})),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_session_history(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content, persona, metadata, created_at FROM conversations WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def search_history(self, query: str, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT session_id, role, content, created_at FROM conversations WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()