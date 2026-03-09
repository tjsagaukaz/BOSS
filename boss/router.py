from __future__ import annotations

from typing import Any

from boss.configuration import BOSSConfig, ModelConfig
from boss.models.local_model_manager import LocalModelManager


class ModelRouter:
    """Chooses the provider/model pair for each agent role or task category."""

    def __init__(self, config: BOSSConfig, local_model_manager: LocalModelManager | None = None) -> None:
        self.config = config
        self.local_model_manager = local_model_manager
        self._client_cache: dict[tuple[str, str], Any] = {}

    def role_for_task(self, task_type: str) -> str:
        return self.config.routing.get(task_type, task_type)

    def config_for_role(self, role: str) -> ModelConfig:
        if role not in self.config.models:
            raise KeyError(f"No model configured for role '{role}'.")
        return self.config.models[role]

    def client_for_role(self, role: str) -> Any:
        cfg = self.config_for_role(role)
        return self.client_for_config(cfg)

    def client_for_config(self, cfg: ModelConfig) -> Any:
        cache_key = (cfg.provider, cfg.model)
        if cache_key not in self._client_cache:
            self._client_cache[cache_key] = self._build_client(cfg)
        return self._client_cache[cache_key]

    def config_for_request(
        self,
        role: str,
        prompt: str,
        request_options: dict[str, Any] | None = None,
    ) -> ModelConfig:
        options = request_options or {}
        if role == "conversation":
            conversation_cfg = self._conversation_config_for_request(request_options=options)
            if conversation_cfg is not None:
                return conversation_cfg

        cfg = self.config_for_role(role)
        if role != "engineer":
            return cfg

        escalation_model = str(cfg.extra.get("escalation_model", "")).strip()
        if not escalation_model:
            return cfg

        if not self._should_escalate_engineer(prompt=prompt, cfg=cfg, request_options=options):
            return cfg

        escalated_extra = dict(cfg.extra)
        escalated_extra["base_model"] = cfg.model
        return ModelConfig(
            provider=cfg.provider,
            model=escalation_model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            extra=escalated_extra,
        )

    def client_for_request(self, role: str, prompt: str, tools=None, request_options: dict[str, Any] | None = None) -> Any:
        options = request_options or {}
        if self.local_model_manager is not None:
            local_client = self.local_model_manager.client_for_request(
                role=role,
                prompt=prompt,
                tools=tools,
                request_options=options,
            )
            if local_client is not None:
                return local_client
        cfg = self.config_for_request(role=role, prompt=prompt, request_options=options)
        return self.client_for_config(cfg)

    def route(self, task_type: str) -> tuple[str, Any, ModelConfig]:
        role = self.role_for_task(task_type)
        cfg = self.config_for_role(role)
        return role, self.client_for_role(role), cfg

    def describe_models(self) -> str:
        items = []
        for role, cfg in self.config.models.items():
            label = f"{role}={cfg.provider}:{cfg.model}"
            escalation_model = str(cfg.extra.get("escalation_model", "")).strip()
            if escalation_model:
                label += f" (pro:{escalation_model})"
            items.append(label)
        if self.local_model_manager is not None:
            selected = self.local_model_manager.selected_model()
            if selected is not None:
                items.append(f"local={selected['backend']}:{selected['model']}")
        return " | ".join(items)

    def model_status(self) -> dict[str, Any]:
        if self.local_model_manager is None:
            return {
                "configured_models": [
                    {"role": role, "provider": cfg.provider, "model": cfg.model}
                    for role, cfg in self.config.models.items()
                ],
                "local_models": [],
                "selected_local_model": None,
                "performance": [],
            }
        return self.local_model_manager.model_status(self.config.models)

    def estimate_cost_for_role(self, role: str, usage: dict[str, int] | None) -> float | None:
        if not usage:
            return None
        cfg = self.config_for_role(role)
        input_rate = self._cost_rate(cfg.extra, "input")
        output_rate = self._cost_rate(cfg.extra, "output")
        if input_rate is None and output_rate is None:
            return None

        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        total = 0.0
        if input_rate is not None:
            total += (input_tokens / 1_000_000) * input_rate
        if output_rate is not None:
            total += (output_tokens / 1_000_000) * output_rate
        return total

    def record_model_run(
        self,
        role: str,
        provider: str,
        model: str,
        duration_seconds: float,
        success: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.local_model_manager is None:
            return
        self.local_model_manager.record_model_run(
            role=role,
            provider=provider,
            model=model,
            duration_seconds=duration_seconds,
            success=success,
            metadata=metadata,
        )

    def _build_client(self, cfg: ModelConfig) -> Any:
        provider = cfg.provider.lower()
        if provider == "openai":
            from boss.models.openai_client import OpenAIModelClient

            return OpenAIModelClient(
                model=cfg.model,
                default_max_tokens=cfg.max_tokens,
                default_temperature=cfg.temperature,
            )
        if provider == "anthropic":
            from boss.models.anthropic_client import AnthropicModelClient

            return AnthropicModelClient(
                model=cfg.model,
                default_max_tokens=cfg.max_tokens,
                default_temperature=cfg.temperature,
            )
        raise ValueError(f"Unsupported model provider '{cfg.provider}'.")

    def _cost_rate(self, extra: dict[str, Any], kind: str) -> float | None:
        keys = [
            f"{kind}_cost_per_1m_tokens",
            f"{kind}_cost_per_million_tokens",
            f"{kind}_cost_per_million",
        ]
        for key in keys:
            if key in extra:
                return float(extra[key])
        return None

    def _should_escalate_engineer(
        self,
        prompt: str,
        cfg: ModelConfig,
        request_options: dict[str, Any],
    ) -> bool:
        if bool(request_options.get("force_pro")) or bool(request_options.get("deep")):
            return True

        attempt = int(request_options.get("attempt", 1) or 1)
        min_attempt = int(cfg.extra.get("escalation_min_attempt", 2))
        if attempt >= min_attempt:
            return True

        complexity = str(request_options.get("complexity", "")).strip().lower()
        if complexity in {"high", "very_high"}:
            return True

        prompt_chars = len(prompt or "")
        max_chars = int(cfg.extra.get("escalation_prompt_chars", 18000))
        if prompt_chars >= max_chars:
            return True

        return False

    def _conversation_config_for_request(self, request_options: dict[str, Any]) -> ModelConfig | None:
        cfg = self.config_for_role("conversation")
        mode = str(request_options.get("mode", "")).strip().lower()
        conversation_type = str(request_options.get("conversation_type", "")).strip().lower()

        if mode == "research" and "research" in self.config.models:
            return self.config_for_role("research")

        role_map = {
            "discussion": str(cfg.extra.get("discussion_role", "architect")).strip() or "architect",
            "planning": str(cfg.extra.get("planning_role", "architect")).strip() or "architect",
            "execution": str(cfg.extra.get("execution_role", "engineer")).strip() or "engineer",
        }
        target_role = role_map.get(conversation_type)
        if target_role and target_role in self.config.models:
            return self.config_for_role(target_role)
        return cfg
