from __future__ import annotations

from pathlib import Path

from boss.agents.base_agent import BaseAgent
from boss.router import ModelRouter
from boss.types import AgentResult, ProjectContext, ToolDefinition


class ArchitectAgent(BaseAgent):
    def __init__(self, router: ModelRouter, root_dir: str | Path) -> None:
        super().__init__(
            role="architect",
            router=router,
            prompt_path=Path(root_dir) / "boss" / "prompts" / "architect_prompt.txt",
        )

    def plan(
        self,
        task: str,
        project_context: ProjectContext,
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResult:
        return self.run(
            task=task,
            project_context=project_context,
            tools=tools,
            task_contract={
                "goal": task,
                "deliverable": "Return a concrete implementation plan with clear steps and constraints.",
            },
        )
