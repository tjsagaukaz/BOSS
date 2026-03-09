from __future__ import annotations

from pathlib import Path

from boss.agents.base_agent import BaseAgent
from boss.router import ModelRouter
from boss.types import AgentResult, ProjectContext, ToolDefinition


class EngineerAgent(BaseAgent):
    def __init__(self, router: ModelRouter, root_dir: str | Path) -> None:
        super().__init__(
            role="engineer",
            router=router,
            prompt_path=Path(root_dir) / "boss" / "prompts" / "engineer_prompt.txt",
        )

    def implement(
        self,
        task: str,
        project_context: ProjectContext,
        plan_text: str,
        tools: list[ToolDefinition] | None = None,
        audit_feedback: str = "",
        request_options: dict[str, object] | None = None,
        task_contract: dict[str, object] | None = None,
        execution_rules: list[str] | None = None,
        execution_spine: dict[str, object] | None = None,
    ) -> AgentResult:
        supplemental = f"Architect Plan:\n{plan_text}"
        if audit_feedback.strip():
            supplemental += f"\n\nAudit Feedback To Address:\n{audit_feedback.strip()}"
        merged_contract = {
            "goal": task,
            "deliverable": "Implement the requested change in code and keep scope tight.",
            "plan_dependency": "Follow the architect plan unless the project context proves it is unsafe.",
        }
        if task_contract:
            merged_contract.update(task_contract)
        return self.run(
            task=task,
            project_context=project_context,
            tools=tools,
            supplemental_context=supplemental,
            request_options=request_options,
            task_contract=merged_contract,
            execution_rules=execution_rules,
            execution_spine=execution_spine,
        )
