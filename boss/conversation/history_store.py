from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class ConversationHistoryStore:
    GLOBAL_SCOPE = "__global__"

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

    def upsert_preference(
        self,
        *,
        preference: str,
        category: str = "general",
        project_name: str | None = None,
        source_message: str = "",
    ) -> None:
        normalized_preference = preference.strip()
        if not normalized_preference:
            return
        scope_key = self._scope_key(project_name)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_preferences (
                    scope_key, category, preference, source_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope_key, category, preference) DO UPDATE SET
                    source_message = excluded.source_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope_key,
                    category.strip() or "general",
                    normalized_preference,
                    source_message.strip(),
                ),
            )

    def recent_preferences(self, project_name: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        scope_key = self._scope_key(project_name)
        query = """
            SELECT id, scope_key, category, preference, source_message, created_at, updated_at
            FROM conversation_preferences
        """
        params: list[Any]
        if project_name:
            query += """
                WHERE scope_key IN (?, ?)
                ORDER BY CASE WHEN scope_key = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC
                LIMIT ?
            """
            params = [scope_key, self.GLOBAL_SCOPE, scope_key, limit]
        else:
            query += " WHERE scope_key = ? ORDER BY updated_at DESC, id DESC LIMIT ?"
            params = [self.GLOBAL_SCOPE, limit]
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_preference(row) for row in rows]

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL DEFAULT '__global__',
                    category TEXT NOT NULL,
                    preference TEXT NOT NULL,
                    source_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scope_key, category, preference)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_preferences_scope_updated
                ON conversation_preferences(scope_key, updated_at DESC)
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

    def _row_to_preference(self, row: sqlite3.Row) -> dict[str, Any]:
        scope_key = row["scope_key"]
        return {
            "id": int(row["id"]),
            "project_name": None if scope_key == self.GLOBAL_SCOPE else scope_key,
            "category": row["category"],
            "preference": row["preference"],
            "source_message": row["source_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _scope_key(self, project_name: str | None) -> str:
        value = str(project_name or "").strip()
        return value or self.GLOBAL_SCOPE
