from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


class ConversationHistoryStore:
    GLOBAL_SCOPE = "__global__"
    LEGACY_THREAD_PREFIX = "legacy:"

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
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        thread = self.ensure_thread(
            project_name=project_name,
            thread_id=thread_id,
            title=self._thread_title(message),
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_history (
                    project_name, thread_id, message, response, intent, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    project_name,
                    thread["id"],
                    message.strip(),
                    response.strip(),
                    intent.strip(),
                    json.dumps(metadata or {}),
                ),
            )
            conn.execute(
                """
                UPDATE conversation_threads
                SET updated_at = CURRENT_TIMESTAMP,
                    title = CASE
                        WHEN title = 'New chat' THEN ?
                        ELSE title
                    END
                WHERE id = ?
                """,
                (self._thread_title(message), thread["id"]),
            )
            return int(cursor.lastrowid)

    def recent(
        self,
        project_name: str | None = None,
        limit: int = 40,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_thread = thread_id
        if not resolved_thread:
            latest = self.latest_thread(project_name=project_name)
            resolved_thread = str(latest["id"]) if latest else None
        query = """
            SELECT id, project_name, thread_id, message, response, intent, metadata, created_at
            FROM conversation_history
        """
        params: list[Any] = []
        if resolved_thread and self._is_legacy_thread_id(resolved_thread):
            scope_key = self._legacy_scope_key(resolved_thread)
            query += " WHERE COALESCE(thread_id, '') = ''"
            if scope_key == self.GLOBAL_SCOPE:
                query += " AND project_name IS NULL"
            else:
                query += " AND project_name = ?"
                params.append(scope_key)
        elif resolved_thread:
            query += " WHERE thread_id = ?"
            params.append(resolved_thread)
        elif project_name:
            query += " WHERE project_name = ?"
            params.append(project_name)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        results = [self._row_to_dict(row) for row in rows]
        results.reverse()
        return results

    def start_thread(self, *, project_name: str | None = None, title: str = "New chat") -> dict[str, Any]:
        scope_key = self._scope_key(project_name)
        thread_id = f"thread_{uuid.uuid4().hex[:12]}"
        normalized_title = self._thread_title(title or "New chat")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_threads (
                    id, scope_key, project_name, title, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (thread_id, scope_key, project_name, normalized_title),
            )
        return self.thread(thread_id, project_name=project_name) or {
            "id": thread_id,
            "project_name": project_name,
            "title": normalized_title,
        }

    def ensure_thread(
        self,
        *,
        project_name: str | None = None,
        thread_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if thread_id:
            existing = self.thread(thread_id, project_name=project_name)
            if existing is None:
                raise ValueError(f"Unknown chat thread '{thread_id}'.")
            return existing
        latest = self.latest_thread(project_name=project_name)
        if latest is not None:
            return latest
        return self.start_thread(project_name=project_name, title=title or "New chat")

    def thread(self, thread_id: str, project_name: str | None = None) -> dict[str, Any] | None:
        if self._is_legacy_thread_id(thread_id):
            scope_key = self._legacy_scope_key(thread_id)
            if project_name and self._scope_key(project_name) != scope_key:
                return None
            return self._legacy_thread_summary(project_name if scope_key != self.GLOBAL_SCOPE else None)
        scope_key = self._scope_key(project_name)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, scope_key, project_name, title, created_at, updated_at
                FROM conversation_threads
                WHERE id = ? AND (? = '__any__' OR scope_key = ?)
                """,
                (thread_id, "__any__" if project_name is None else scope_key, scope_key),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_thread(row)

    def latest_thread(self, project_name: str | None = None) -> dict[str, Any] | None:
        scope_key = self._scope_key(project_name)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, scope_key, project_name, title, created_at, updated_at
                FROM conversation_threads
                WHERE scope_key = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (scope_key,),
            ).fetchone()
        if row is not None:
            return self._row_to_thread(row)
        return self._legacy_thread_summary(project_name)

    def list_threads(self, project_name: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        scope_key = self._scope_key(project_name)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, scope_key, project_name, title, created_at, updated_at
                FROM conversation_threads
                WHERE scope_key = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (scope_key, limit),
            ).fetchall()
        threads = [self._row_to_thread(row) for row in rows]
        legacy = self._legacy_thread_summary(project_name)
        if legacy is not None and not any(str(item.get("id")) == str(legacy["id"]) for item in threads):
            threads.append(legacy)
        threads.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return threads[:limit]

    def delete_thread(self, thread_id: str, *, project_name: str | None = None) -> bool:
        if self._is_legacy_thread_id(thread_id):
            scope_key = self._legacy_scope_key(thread_id)
            target_project = None if scope_key == self.GLOBAL_SCOPE else scope_key
            with self._connect() as conn:
                if target_project is None:
                    cursor = conn.execute(
                        """
                        DELETE FROM conversation_history
                        WHERE COALESCE(thread_id, '') = '' AND project_name IS NULL
                        """
                    )
                else:
                    cursor = conn.execute(
                        """
                        DELETE FROM conversation_history
                        WHERE COALESCE(thread_id, '') = '' AND project_name = ?
                        """,
                        (target_project,),
                    )
            return cursor.rowcount > 0
        scope_key = self._scope_key(project_name)
        with self._connect() as conn:
            owner = conn.execute(
                "SELECT scope_key FROM conversation_threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if owner is None or owner["scope_key"] != scope_key:
                return False
            conn.execute("DELETE FROM conversation_history WHERE thread_id = ?", (thread_id,))
            cursor = conn.execute("DELETE FROM conversation_threads WHERE id = ?", (thread_id,))
        return cursor.rowcount > 0

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
                    thread_id TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_conversation_history_thread_created
                ON conversation_history(thread_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_threads (
                    id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    project_name TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_threads_scope_updated
                ON conversation_threads(scope_key, updated_at DESC)
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
            self._ensure_column(conn, "conversation_history", "thread_id", "TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        metadata = json.loads(row["metadata"] or "{}")
        return {
            "id": int(row["id"]),
            "project_name": row["project_name"],
            "thread_id": row["thread_id"],
            "message": row["message"],
            "response": row["response"],
            "intent": row["intent"],
            "metadata": metadata,
            "created_at": row["created_at"],
        }

    def _row_to_thread(self, row: sqlite3.Row) -> dict[str, Any]:
        preview = self._thread_preview(str(row["id"]))
        return {
            "id": row["id"],
            "project_name": row["project_name"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            **preview,
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

    def _legacy_thread_id(self, project_name: str | None) -> str:
        return f"{self.LEGACY_THREAD_PREFIX}{self._scope_key(project_name)}"

    def _legacy_scope_key(self, thread_id: str) -> str:
        return thread_id.removeprefix(self.LEGACY_THREAD_PREFIX) or self.GLOBAL_SCOPE

    def _is_legacy_thread_id(self, thread_id: str) -> bool:
        return str(thread_id).startswith(self.LEGACY_THREAD_PREFIX)

    def _thread_preview(self, thread_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message, response, created_at
                FROM conversation_history
                WHERE thread_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            return {"preview": "", "last_message": "", "turn_count": 0}
        with self._connect() as conn:
            count_row = conn.execute(
                "SELECT COUNT(*) AS count FROM conversation_history WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        last_message = str(row["message"] or "")
        return {
            "preview": self._thread_title(last_message),
            "last_message": last_message,
            "turn_count": int((count_row or {})["count"]) if count_row is not None else 0,
        }

    def _legacy_thread_summary(self, project_name: str | None) -> dict[str, Any] | None:
        target_project = project_name
        with self._connect() as conn:
            if target_project is None:
                row = conn.execute(
                    """
                    SELECT message, response, created_at, COUNT(*) AS turn_count, MAX(created_at) AS updated_at
                    FROM conversation_history
                    WHERE COALESCE(thread_id, '') = '' AND project_name IS NULL
                    """,
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT message, response, created_at, COUNT(*) AS turn_count, MAX(created_at) AS updated_at
                    FROM conversation_history
                    WHERE COALESCE(thread_id, '') = '' AND project_name = ?
                    """,
                    (target_project,),
                ).fetchone()
        if row is None or int(row["turn_count"] or 0) == 0:
            return None
        preview = str(row["message"] or "")
        return {
            "id": self._legacy_thread_id(project_name),
            "project_name": project_name,
            "title": self._thread_title(preview),
            "preview": self._thread_title(preview),
            "last_message": preview,
            "turn_count": int(row["turn_count"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"] or row["created_at"],
            "legacy": True,
        }

    def _thread_title(self, value: str) -> str:
        text = " ".join(str(value or "").strip().split())
        if not text:
            return "New chat"
        if len(text) <= 64:
            return text
        return f"{text[:63].rstrip()}…"

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
