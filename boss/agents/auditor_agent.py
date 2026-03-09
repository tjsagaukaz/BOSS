from __future__ import annotations

import re
from pathlib import Path

from boss.agents.base_agent import BaseAgent
from boss.router import ModelRouter
from boss.types import AuditIssue, AuditResult, ProjectContext, ToolDefinition


class AuditorAgent(BaseAgent):
    def __init__(self, router: ModelRouter, root_dir: str | Path) -> None:
        super().__init__(
            role="auditor",
            router=router,
            prompt_path=Path(root_dir) / "boss" / "prompts" / "auditor_prompt.txt",
        )

    def audit(
        self,
        task: str,
        project_context: ProjectContext,
        plan_text: str,
        implementation_text: str,
        changed_files: list[str],
        tools: list[ToolDefinition] | None = None,
        test_results: str = "",
        task_contract: dict[str, object] | None = None,
        execution_rules: list[str] | None = None,
        execution_spine: dict[str, object] | None = None,
    ) -> AuditResult:
        supplemental = (
            f"Architect Plan:\n{plan_text}\n\n"
            f"Engineer Summary:\n{implementation_text}\n\n"
            f"Changed Files:\n{', '.join(changed_files) if changed_files else 'No files reported'}"
        )
        if test_results.strip():
            supplemental += f"\n\nTest Results:\n{test_results.strip()}"
        merged_contract = {
            "goal": task,
            "deliverable": "Review the implementation critically and return pass/fail with concrete findings.",
            "changed_files": changed_files,
        }
        if task_contract:
            merged_contract.update(task_contract)
        agent_result = self.run(
            task=task,
            project_context=project_context,
            tools=tools,
            supplemental_context=supplemental,
            task_contract=merged_contract,
            execution_rules=execution_rules,
            execution_spine=execution_spine,
        )
        return self._parse_audit_result(agent_result)

    def _parse_audit_result(self, agent_result) -> AuditResult:
        status_match = re.search(r"AUDIT_STATUS:\s*(PASS|FAIL)", agent_result.text, re.IGNORECASE)
        passed = status_match.group(1).upper() == "PASS" if status_match else "PASS" in agent_result.text.upper()
        issues: list[AuditIssue] = []
        for line in agent_result.text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("-") or "|" not in stripped:
                continue
            payload = stripped[1:].strip()
            if payload.lower() == "none":
                continue
            parts = [part.strip() for part in payload.split("|", 2)]
            if len(parts) == 3:
                issues.append(AuditIssue(severity=parts[0], location=parts[1], description=parts[2]))
        return AuditResult(
            agent_name=agent_result.agent_name,
            provider=agent_result.provider,
            model=agent_result.model,
            text=agent_result.text,
            passed=passed,
            duration_seconds=agent_result.duration_seconds,
            usage=agent_result.usage,
            estimated_cost_usd=agent_result.estimated_cost_usd,
            issues=issues,
            tool_records=agent_result.tool_records,
        )
