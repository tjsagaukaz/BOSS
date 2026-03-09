from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from boss.types import ModelRunResult, ToolDefinition


class LocalModelClient:
    def __init__(self, backend: str, model: str, endpoint: str, timeout: int = 120) -> None:
        self.backend = backend
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.provider = f"local-{backend}"

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
        on_text_delta=None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        max_tool_rounds: int | None = None,
    ) -> ModelRunResult:
        if tools:
            raise RuntimeError("BOSS local model routing currently supports prompt-only requests without tool use.")
        started = time.perf_counter()
        if self.backend == "ollama":
            text = self._generate_ollama(prompt, system_prompt, max_tokens, temperature, timeout_seconds=timeout_seconds)
        else:
            text = self._generate_openai_compatible(
                prompt,
                system_prompt,
                max_tokens,
                temperature,
                timeout_seconds=timeout_seconds,
            )
        duration = time.perf_counter() - started
        if stream and on_text_delta:
            on_text_delta(text)
        return ModelRunResult(
            text=text,
            provider=self.provider,
            model=self.model,
            duration_seconds=duration,
        )

    def _generate_ollama(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int | None,
        temperature: float | None,
        timeout_seconds: float | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt or "You are a local BOSS assistant."},
                {"role": "user", "content": prompt},
            ],
            "options": {},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        response = _request_json(
            f"{self.endpoint}/api/chat",
            method="POST",
            payload=payload,
            timeout=timeout_seconds or self.timeout,
        )
        message = response.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
        text = response.get("response", "")
        return text.strip() if isinstance(text, str) else ""

    def _generate_openai_compatible(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int | None,
        temperature: float | None,
        timeout_seconds: float | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or "You are a local BOSS assistant."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        response = _request_json(
            f"{self.endpoint}/chat/completions",
            method="POST",
            payload=payload,
            timeout=timeout_seconds or self.timeout,
        )
        choices = response.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "\n".join(part for part in parts if part).strip()
        return str(content).strip()


class LocalModelManager:
    DEFAULT_ENDPOINTS = {
        "ollama": "http://127.0.0.1:11434",
        "lmstudio": "http://127.0.0.1:1234/v1",
    }

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def list_models(self, refresh: bool = True) -> list[dict[str, Any]]:
        if refresh:
            discovered = self._discover_models()
            self._replace_cached_models(discovered)
        selected = self._selected_preference()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT backend, model_name, endpoint, metadata, available, updated_at
                FROM local_models
                ORDER BY backend ASC, model_name ASC
                """
            ).fetchall()
        return [
            {
                "backend": row["backend"],
                "model": row["model_name"],
                "endpoint": row["endpoint"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "available": bool(row["available"]),
                "selected": bool(
                    selected
                    and selected.get("backend") == row["backend"]
                    and selected.get("model") == row["model_name"]
                ),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def select_model(self, model: str, backend: str | None = None) -> dict[str, Any]:
        models = self.list_models(refresh=True)
        for item in models:
            if item["model"] != model:
                continue
            if backend and item["backend"] != backend:
                continue
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO local_model_preferences (preference_key, value, updated_at)
                    VALUES ('selected_model', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(preference_key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (json.dumps({"backend": item["backend"], "model": item["model"]}),),
                )
            selected_item = dict(item)
            selected_item["selected"] = True
            return selected_item
        raise ValueError(f"Local model '{model}' was not found.")

    def selected_model(self) -> dict[str, Any] | None:
        payload = self._selected_preference()
        if payload is None:
            return self.best_local_model()
        backend = payload.get("backend")
        model = payload.get("model")
        for item in self.list_models(refresh=False):
            if item["backend"] == backend and item["model"] == model:
                return item
        return self.best_local_model()

    def should_use_local(
        self,
        role: str,
        prompt: str,
        tools: list[ToolDefinition] | None = None,
        request_options: dict[str, Any] | None = None,
    ) -> bool:
        if request_options and (request_options.get("deep") or request_options.get("force_pro")):
            return False
        if tools:
            return False
        if not self.list_models(refresh=True):
            return False
        prompt_size = len(prompt or "")
        if role in {"documentation", "test"} and prompt_size <= 14000:
            return True
        if role in {"architect", "auditor"} and prompt_size <= 7000:
            return True
        if role == "engineer" and prompt_size <= 2500:
            return True
        return False

    def client_for_request(
        self,
        role: str,
        prompt: str,
        tools: list[ToolDefinition] | None = None,
        request_options: dict[str, Any] | None = None,
    ) -> LocalModelClient | None:
        if not self.should_use_local(role=role, prompt=prompt, tools=tools, request_options=request_options):
            return None
        chosen = self.selected_model() or self.best_local_model()
        if chosen is None:
            return None
        return LocalModelClient(
            backend=str(chosen["backend"]),
            model=str(chosen["model"]),
            endpoint=str(chosen["endpoint"]),
        )

    def best_local_model(self) -> dict[str, Any] | None:
        models = self.list_models(refresh=True)
        if not models:
            return None
        performance = {
            (str(item["provider"]).replace("local-", ""), str(item["model"])): item
            for item in self.performance_summary(limit=50)
            if str(item["provider"]).startswith("local-")
        }
        ranked = sorted(
            models,
            key=lambda item: (
                -float(performance.get((str(item["backend"]), str(item["model"])), {}).get("success_rate", 0.0)),
                float(
                    performance.get((str(item["backend"]), str(item["model"])), {}).get("avg_duration_seconds", 9999.0)
                ),
                str(item["backend"]),
                str(item["model"]),
            ),
        )
        return ranked[0]

    def record_model_run(
        self,
        role: str,
        provider: str,
        model: str,
        duration_seconds: float,
        success: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_performance (
                    role, provider, model_name, duration_seconds, success, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    role,
                    provider,
                    model,
                    float(duration_seconds),
                    1 if success else 0,
                    json.dumps(metadata or {}),
                ),
            )

    def performance_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    role,
                    provider,
                    model_name,
                    COUNT(*) AS run_count,
                    AVG(duration_seconds) AS avg_duration_seconds,
                    AVG(success) AS success_rate,
                    MAX(created_at) AS last_used_at
                FROM model_performance
                GROUP BY role, provider, model_name
                ORDER BY last_used_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "role": row["role"],
                "provider": row["provider"],
                "model": row["model_name"],
                "run_count": int(row["run_count"]),
                "avg_duration_seconds": float(row["avg_duration_seconds"] or 0.0),
                "success_rate": float(row["success_rate"] or 0.0),
                "last_used_at": row["last_used_at"],
            }
            for row in rows
        ]

    def model_status(self, configured_models: dict[str, Any]) -> dict[str, Any]:
        return {
            "configured_models": [
                {
                    "role": role,
                    "provider": cfg.provider,
                    "model": cfg.model,
                }
                for role, cfg in configured_models.items()
            ],
            "local_models": self.list_models(refresh=True),
            "selected_local_model": self.selected_model(),
            "performance": self.performance_summary(limit=30),
        }

    def _discover_models(self) -> list[dict[str, Any]]:
        discovered: list[dict[str, Any]] = []
        discovered.extend(self._discover_ollama())
        discovered.extend(self._discover_lmstudio())
        return discovered

    def _discover_ollama(self) -> list[dict[str, Any]]:
        endpoint = self.DEFAULT_ENDPOINTS["ollama"]
        try:
            response = _request_json(f"{endpoint}/api/tags", timeout=5)
        except Exception:
            return []
        models = response.get("models", [])
        results: list[dict[str, Any]] = []
        for item in models if isinstance(models, list) else []:
            name = item.get("name")
            if not name:
                continue
            results.append(
                {
                    "backend": "ollama",
                    "model": str(name),
                    "endpoint": endpoint,
                    "metadata": {
                        "size": item.get("size"),
                        "modified_at": item.get("modified_at"),
                    },
                    "available": True,
                }
            )
        return results

    def _discover_lmstudio(self) -> list[dict[str, Any]]:
        endpoint = self.DEFAULT_ENDPOINTS["lmstudio"]
        try:
            response = _request_json(f"{endpoint}/models", timeout=5)
        except Exception:
            return []
        models = response.get("data", [])
        results: list[dict[str, Any]] = []
        for item in models if isinstance(models, list) else []:
            model_id = item.get("id")
            if not model_id:
                continue
            results.append(
                {
                    "backend": "lmstudio",
                    "model": str(model_id),
                    "endpoint": endpoint,
                    "metadata": {
                        "owned_by": item.get("owned_by"),
                    },
                    "available": True,
                }
            )
        return results

    def _replace_cached_models(self, models: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM local_models")
            for item in models:
                conn.execute(
                    """
                    INSERT INTO local_models (
                        backend, model_name, endpoint, metadata, available, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        item["backend"],
                        item["model"],
                        item["endpoint"],
                        json.dumps(item.get("metadata", {})),
                        1 if item.get("available", False) else 0,
                    ),
                )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_models (
                    backend TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    available INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (backend, model_name)
                );

                CREATE TABLE IF NOT EXISTS local_model_preferences (
                    preference_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_performance (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    success INTEGER NOT NULL DEFAULT 1,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )

    def _selected_preference(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value FROM local_model_preferences
                WHERE preference_key = 'selected_model'
                """
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"] or "{}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _request_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    request_payload = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = Request(url, data=request_payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Request failed for {url}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Unable to reach local model endpoint {url}: {exc.reason}") from exc
    if not body.strip():
        return {}
    return json.loads(body)
