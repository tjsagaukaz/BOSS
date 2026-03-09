from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class ConversationHistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append_turn(
        self,
        *,
        project_name: str | None,
        message: str,
        response: str,
        intent: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_history (
                    project_name, message, response, intent, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    project_name,
                    message.strip(),
                    response.strip(),
                    intent.strip(),
                    json.dumps(metadata or {}),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, project_name: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        query = """
            SELECT id, project_name, message, response, intent, metadata, created_at
            FROM conversation_history
        """
        params: list[Any] = []
        if project_name:
            query += " WHERE project_name = ?"
            params.append(project_name)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        results = [self._row_to_dict(row) for row in rows]
        results.reverse()
        return results

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT,
                    message TEXT NOT NULL,
                    response TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_history_project_created
                ON conversation_history(project_name, created_at DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        metadata = json.loads(row["metadata"] or "{}")
        return {
            "id": int(row["id"]),
            "project_name": row["project_name"],
            "message": row["message"],
            "response": row["response"],
            "intent": row["intent"],
            "metadata": metadata,
            "created_at": row["created_at"],
        }
