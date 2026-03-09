from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import guard for environments without dependencies
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class ModelConfig:
    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.2
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BOSSConfig:
    models: dict[str, ModelConfig]
    routing: dict[str, str]
    embeddings: dict[str, Any]


@dataclass
class RuntimeConfig:
    timeouts: dict[str, int] = field(default_factory=lambda: {"engineer_step": 120, "test_step": 60, "audit_step": 45})


def _normalize_model_config(raw: Any) -> ModelConfig:
    if isinstance(raw, str):
        provider = "openai" if raw.startswith("gpt") or raw.startswith("o") else "anthropic"
        return ModelConfig(provider=provider, model=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid model configuration: {raw!r}")
    extra = {k: v for k, v in raw.items() if k not in {"provider", "model", "max_tokens", "temperature"}}
    return ModelConfig(
        provider=raw.get("provider", "openai"),
        model=raw["model"],
        max_tokens=int(raw.get("max_tokens", 4096)),
        temperature=float(raw.get("temperature", 0.2)),
        extra=extra,
    )


def load_config(config_path: str | Path) -> BOSSConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to load config/models.yaml. Install dependencies from requirements.txt."
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models_section = raw.get("models", {})
    if not models_section:
        raise ValueError("config/models.yaml must define a 'models' section.")

    models = {role: _normalize_model_config(cfg) for role, cfg in models_section.items()}
    routing = raw.get(
        "routing",
        {
            "architecture": "architect",
            "planning": "architect",
            "coding": "engineer",
            "implementation": "engineer",
            "debugging": "engineer",
            "review": "auditor",
            "audit": "auditor",
            "documentation": "architect",
        },
    )
    embeddings = raw.get("embeddings", {"provider": "local", "model": "hashed-256", "dimensions": 256})
    return BOSSConfig(models=models, routing=routing, embeddings=embeddings)


def load_runtime_config(config_path: str | Path) -> RuntimeConfig:
    path = Path(config_path)
    if not path.exists():
        return RuntimeConfig()
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to load config/runtime.yaml. Install dependencies from requirements.txt."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    timeouts = raw.get("timeouts", {}) or {}
    defaults = RuntimeConfig().timeouts
    merged = {**defaults, **{key: int(value) for key, value in timeouts.items()}}
    return RuntimeConfig(timeouts=merged)
