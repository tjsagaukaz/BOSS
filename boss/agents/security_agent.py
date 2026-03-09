from __future__ import annotations

import re
from pathlib import Path

from boss.agents.base_agent import BaseAgent
from boss.router import ModelRouter
from boss.types import AgentResult, ProjectContext, ToolDefinition


class SecurityAgent(BaseAgent):
    def __init__(self, router: ModelRouter, root_dir: str | Path) -> None:
        super().__init__(
            role="security",
            router=router,
            prompt_path=Path(root_dir) / "boss" / "prompts" / "security_prompt.txt",
        )

    def review(
        self,
        task: str,
        project_context: ProjectContext,
        plan_text: str,
        implementation_text: str = "",
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResult:
        supplemental = f"Architect Plan:\n{plan_text}"
        if implementation_text.strip():
            supplemental += f"\n\nEngineer Summary:\n{implementation_text.strip()}"
        return self.run(
            task=task,
            project_context=project_context,
            tools=tools,
            supplemental_context=supplemental,
            task_contract={
                "goal": task,
                "deliverable": "Review security-sensitive changes and identify vulnerabilities or unsafe assumptions.",
            },
        )

    def passed(self, result: AgentResult) -> bool:
        match = re.search(r"SECURITY_STATUS:\s*(PASS|FAIL)", result.text, re.IGNORECASE)
        return match.group(1).upper() == "PASS" if match else False
