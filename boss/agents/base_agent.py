from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from boss.runtime import ContextEnvelopeBuilder
from boss.router import ModelRouter
from boss.types import AgentResult, ProjectContext, ToolDefinition


class BaseAgent:
    def __init__(self, role: str, router: ModelRouter, prompt_path: str | Path) -> None:
        self.role = role
        self.router = router
        self.prompt_path = Path(prompt_path)
        self.envelope_builder = ContextEnvelopeBuilder()

    def run(
        self,
        task: str,
        project_context: ProjectContext,
        tools: list[ToolDefinition] | None = None,
        supplemental_context: str = "",
        stream: bool = False,
        request_options: dict[str, object] | None = None,
        task_contract: dict[str, Any] | None = None,
        execution_rules: list[str] | None = None,
        execution_spine: dict[str, Any] | None = None,
    ) -> AgentResult:
        request_options = request_options or {}
        prompt = self._build_prompt(
            task=task,
            project_context=project_context,
            tools=tools or [],
            supplemental_context=supplemental_context,
            task_contract=task_contract,
            execution_rules=execution_rules,
            execution_spine=execution_spine,
        )
        client = self.router.client_for_request(
            self.role,
            prompt=prompt,
            tools=tools or [],
            request_options=request_options,
        )
        system_prompt = self._load_system_prompt()
        generation_options: dict[str, Any] = {}
        if request_options.get("timeout_seconds") is not None:
            generation_options["timeout_seconds"] = float(request_options["timeout_seconds"])
        if request_options.get("max_tool_rounds") is not None:
            generation_options["max_tool_rounds"] = int(request_options["max_tool_rounds"])
        try:
            result = client.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools or [],
                stream=stream,
                **generation_options,
            )
        except Exception as exc:
            self.router.record_model_run(
                role=self.role,
                provider=str(getattr(client, "provider", "unknown")),
                model=str(getattr(client, "model", "unknown")),
                duration_seconds=0.0,
                success=False,
                metadata={"error": str(exc), "tool_count": len(tools or []), **request_options},
            )
            raise
        self.router.record_model_run(
            role=self.role,
            provider=result.provider,
            model=result.model,
            duration_seconds=result.duration_seconds,
            success=True,
            metadata={"tool_count": len(tools or []), "stream": stream, **request_options},
        )
        estimated_cost = self.router.estimate_cost_for_role(self.role, result.usage)
        return AgentResult(
            agent_name=self.role,
            provider=result.provider,
            model=result.model,
            text=result.text,
            duration_seconds=result.duration_seconds,
            usage=result.usage,
            estimated_cost_usd=estimated_cost,
            tool_records=result.tool_records,
        )

    def _load_system_prompt(self) -> str:
        optimized_path = self.prompt_path.parent / "optimized" / self.prompt_path.name
        use_optimized = os.environ.get("BOSS_USE_OPTIMIZED_PROMPTS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        source_path = optimized_path if use_optimized and optimized_path.exists() else self.prompt_path
        return source_path.read_text(encoding="utf-8").strip()

    def _build_prompt(
        self,
        *,
        task: str,
        project_context: ProjectContext,
        tools: list[ToolDefinition],
        supplemental_context: str,
        task_contract: dict[str, Any] | None,
        execution_rules: list[str] | None,
        execution_spine: dict[str, Any] | None,
    ) -> str:
        return self.envelope_builder.build(
            role=self.role,
            task=task,
            project_context=project_context,
            tools=tools,
            supplemental_context=supplemental_context,
            task_contract=task_contract,
            execution_rules=execution_rules,
            execution_spine=execution_spine,
        ).render()
