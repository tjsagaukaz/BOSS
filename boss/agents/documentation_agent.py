from __future__ import annotations

import re
from pathlib import Path

from boss.agents.base_agent import BaseAgent
from boss.router import ModelRouter
from boss.types import AgentResult, ProjectContext, ToolDefinition


class DocumentationAgent(BaseAgent):
    def __init__(self, router: ModelRouter, root_dir: str | Path) -> None:
        super().__init__(
            role="documentation",
            router=router,
            prompt_path=Path(root_dir) / "boss" / "prompts" / "documentation_prompt.txt",
        )

    def document(
        self,
        task: str,
        project_context: ProjectContext,
        plan_text: str,
        implementation_text: str = "",
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResult:
        supplemental = f"Architect Plan:\n{plan_text}"
        if implementation_text.strip():
            supplemental += f"\n\nImplementation Notes:\n{implementation_text.strip()}"
        return self.run(
            task=task,
            project_context=project_context,
            tools=tools,
            supplemental_context=supplemental,
            task_contract={
                "goal": task,
                "deliverable": "Produce documentation that matches the implemented behavior and developer workflow.",
            },
        )

    def ready(self, result: AgentResult) -> bool:
        match = re.search(r"DOC_STATUS:\s*(READY|NEEDS_UPDATE)", result.text, re.IGNORECASE)
        return match.group(1).upper() == "READY" if match else False
