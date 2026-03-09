from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from boss.types import PlanStepContract, StructuredPlan


@dataclass
class SpineStepState:
    index: int
    contract: PlanStepContract
    status: str = "pending"
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PlanningSpine:
    task_id: str
    goal: str
    steps: list[SpineStepState]
    current_step_index: int = 0
    raw_text: str = ""

    TEXT_ARTIFACT_EXTENSIONS = {
        ".md",
        ".txt",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".go",
        ".rs",
        ".swift",
        ".cpp",
        ".h",
        ".hpp",
        ".c",
    }

    @classmethod
    def from_text(cls, task_id: str | int, fallback_goal: str, text: str) -> "PlanningSpine":
        payload = text.strip()
        match = re.search(r"```json\s*(\{.*?\})\s*```", payload, re.DOTALL)
        if match:
            payload = match.group(1)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            goal = fallback_goal
            contracts = [cls._default_step_contract(title, index) for index, title in enumerate(cls._extract_steps_from_text(text))]
            return cls.from_plan(task_id=task_id, plan=StructuredPlan(goal=goal, steps=[item.title for item in contracts], contracts=contracts, raw_text=text))

        goal = str(data.get("goal", fallback_goal)).strip() or fallback_goal
        raw_steps = data.get("steps", [])
        contracts: list[PlanStepContract] = []
        if isinstance(raw_steps, list):
            for index, raw_step in enumerate(raw_steps):
                if isinstance(raw_step, dict):
                    contracts.append(cls._parse_step_contract(raw_step, index))
                    continue
                title = str(raw_step).strip()
                if title:
                    contracts.append(cls._default_step_contract(title, index))

        if not contracts:
            contracts = [cls._default_step_contract(title, index) for index, title in enumerate(cls._extract_steps_from_text(text))]

        return cls.from_plan(
            task_id=task_id,
            plan=StructuredPlan(
                goal=goal,
                steps=[contract.title for contract in contracts[:8]],
                contracts=contracts[:8],
                raw_text=text,
            ),
        )

    @classmethod
    def from_plan(cls, task_id: str | int, plan: StructuredPlan) -> "PlanningSpine":
        contracts = list(plan.contracts)
        if not contracts:
            contracts = [cls._default_step_contract(title, index) for index, title in enumerate(plan.steps)]
        steps = [SpineStepState(index=index, contract=contract) for index, contract in enumerate(contracts[:8])]
        return cls(
            task_id=str(task_id),
            goal=plan.goal,
            steps=steps,
            current_step_index=0,
            raw_text=plan.raw_text,
        )

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    def to_structured_plan(self) -> StructuredPlan:
        contracts = [item.contract for item in self.steps]
        return StructuredPlan(
            goal=self.goal,
            steps=[item.contract.title for item in self.steps],
            contracts=contracts,
            raw_text=self.raw_text,
        )

    def set_current_step(self, step_index: int) -> None:
        if not self.steps:
            self.current_step_index = 0
            return
        self.current_step_index = max(0, min(step_index, len(self.steps) - 1))

    def current_step(self) -> SpineStepState | None:
        if not self.steps:
            return None
        if self.current_step_index < 0 or self.current_step_index >= len(self.steps):
            return None
        return self.steps[self.current_step_index]

    def step_contract(self, step_index: int | None = None) -> PlanStepContract | None:
        target = self.current_step() if step_index is None else self.steps[step_index] if 0 <= step_index < len(self.steps) else None
        return None if target is None else target.contract

    def advance(self) -> None:
        if self.current_step_index < len(self.steps) - 1:
            self.current_step_index += 1

    def mark_attempt(self, step_index: int, errors: list[str] | None = None) -> None:
        if 0 <= step_index < len(self.steps):
            self.steps[step_index].attempts += 1
            self.steps[step_index].status = "running"
            if errors:
                self.steps[step_index].errors = list(errors)

    def mark_completed(self, step_index: int) -> None:
        if 0 <= step_index < len(self.steps):
            self.steps[step_index].status = "completed"
            self.steps[step_index].errors = []
            next_index = self._next_pending_index()
            if next_index is not None:
                self.current_step_index = next_index

    def mark_failed(self, step_index: int, errors: list[str] | None = None) -> None:
        if 0 <= step_index < len(self.steps):
            self.steps[step_index].status = "failed"
            self.steps[step_index].errors = list(errors or [])

    def current_step_payload(self) -> dict[str, Any]:
        return self.execution_payload(self.current_step_index)

    def execution_payload(self, step_index: int) -> dict[str, Any]:
        current = self.step_state(step_index)
        if current is None:
            return {
                "task_id": self.task_id,
                "goal": self.goal,
                "current_step_index": -1,
                "current_step_number": 0,
                "total_steps": 0,
                "completed_steps": 0,
                "steps_remaining": 0,
                "step": {},
            }
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "current_step_index": current.index,
            "current_step_number": current.index + 1,
            "total_steps": self.total_steps,
            "completed_steps": sum(1 for item in self.steps if item.status == "completed"),
            "steps_remaining": sum(1 for item in self.steps if item.status != "completed" and item.index != current.index),
            "step": self.step_payload(current.index),
        }

    def step_payload(self, step_index: int) -> dict[str, Any]:
        if step_index < 0 or step_index >= len(self.steps):
            return {}
        state = self.steps[step_index]
        contract = state.contract
        return {
            "index": state.index,
            "id": contract.step_id or f"S{state.index + 1}",
            "title": contract.title,
            "goal": contract.objective or contract.title,
            "agent_role": contract.agent_role,
            "dependencies": list(contract.dependencies),
            "allowed_paths": list(contract.allowed_paths),
            "expected_outputs": self._expected_outputs(contract),
            "required_artifacts": list(contract.required_artifacts),
            "validation": self._validation_items(contract),
            "validation_commands": list(contract.validation_commands),
            "notes": list(contract.notes),
            "status": state.status,
            "attempts": state.attempts,
        }

    def as_plan_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "current_step_index": self.current_step_index,
            "total_steps": self.total_steps,
            "steps": [self.step_payload(index) for index in range(self.total_steps)],
        }

    def step_state(self, step_index: int) -> SpineStepState | None:
        if step_index < 0 or step_index >= len(self.steps):
            return None
        return self.steps[step_index]

    def validate_step_outputs(
        self,
        project_root: Path,
        *,
        changed_files: list[str] | None = None,
        step_index: int | None = None,
    ) -> list[str]:
        contract = self.step_contract(step_index)
        if contract is None:
            return []
        return self.validate_contract_outputs(project_root, contract, changed_files=changed_files)

    @classmethod
    def validate_contract_outputs(
        cls,
        project_root: Path,
        contract: PlanStepContract,
        *,
        changed_files: list[str] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        if changed_files:
            errors.extend(cls._validate_allowed_paths(contract, changed_files))
        for output in cls._expected_outputs(contract):
            errors.extend(cls._validate_file_artifact(project_root, output, label="Expected output"))
        for artifact in contract.required_artifacts:
            if artifact in cls._expected_outputs(contract):
                continue
            errors.extend(cls._validate_file_artifact(project_root, artifact, label="Required artifact"))
        for rule in contract.done_when:
            errors.extend(cls._validate_done_when_rule(project_root, rule))
        return list(dict.fromkeys(errors))

    @classmethod
    def _validate_allowed_paths(cls, contract: PlanStepContract, changed_files: list[str]) -> list[str]:
        if not contract.allowed_paths:
            return []
        errors: list[str] = []
        for changed_file in changed_files:
            normalized = str(Path(changed_file)).strip().lstrip("./")
            if not normalized:
                continue
            if any(cls._path_allowed(normalized, allowed) for allowed in contract.allowed_paths):
                continue
            errors.append(
                f"Changed file outside allowed paths: {normalized} (allowed: {', '.join(contract.allowed_paths)})"
            )
        return errors

    @staticmethod
    def _path_allowed(changed_file: str, allowed_path: str) -> bool:
        cleaned_allowed = str(Path(allowed_path)).strip().lstrip("./")
        if not cleaned_allowed:
            return True
        if cleaned_allowed.endswith("/"):
            prefix = cleaned_allowed.rstrip("/")
            return changed_file == prefix or changed_file.startswith(f"{prefix}/")
        if "/" not in cleaned_allowed and "." not in Path(cleaned_allowed).name:
            return changed_file == cleaned_allowed or changed_file.startswith(f"{cleaned_allowed}/")
        return changed_file == cleaned_allowed or changed_file.startswith(f"{cleaned_allowed}/")

    @classmethod
    def _validate_file_artifact(cls, project_root: Path, artifact: str, *, label: str) -> list[str]:
        file_path = cls._artifact_file_path(artifact)
        if file_path is None:
            return []
        resolved = (project_root / file_path).resolve()
        if not resolved.exists():
            return [f"{label} missing: {file_path}"]
        if resolved.is_file() and resolved.suffix.lower() in cls.TEXT_ARTIFACT_EXTENSIONS:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return [f"{label} is empty: {file_path}"]
        return []

    @classmethod
    def _validate_done_when_rule(cls, project_root: Path, rule: str) -> list[str]:
        cleaned = rule.strip()
        if not cleaned.startswith("file:"):
            return []
        payload = cleaned[5:]
        if " contains " in payload:
            file_path, snippet = payload.split(" contains ", 1)
            file_path = file_path.strip()
            snippet = snippet.strip().strip("'\"")
            resolved = (project_root / file_path).resolve()
            if not resolved.exists():
                return [f"Done condition failed: {file_path} does not exist."]
            content = resolved.read_text(encoding="utf-8", errors="replace")
            if snippet not in content:
                return [f"Done condition failed: {file_path} does not contain '{snippet}'."]
            return []
        if payload.endswith(" exists"):
            file_path = payload[: -len(" exists")].strip()
            if not (project_root / file_path).exists():
                return [f"Done condition failed: {file_path} does not exist."]
            return []
        if payload.endswith(" not_empty"):
            file_path = payload[: -len(" not_empty")].strip()
            resolved = (project_root / file_path).resolve()
            if not resolved.exists():
                return [f"Done condition failed: {file_path} does not exist."]
            if resolved.is_file():
                content = resolved.read_text(encoding="utf-8", errors="replace")
                if not content.strip():
                    return [f"Done condition failed: {file_path} is empty."]
            return []
        return []

    @classmethod
    def _parse_step_contract(cls, raw_step: dict[str, object], index: int) -> PlanStepContract:
        title = str(raw_step.get("title", "")).strip()
        if not title:
            raise ValueError("Structured step is missing a title.")
        step_id = str(raw_step.get("id", "")).strip() or f"S{index + 1}"
        expected_outputs = cls._string_list(raw_step.get("expected_outputs"))
        required_artifacts = cls._string_list(raw_step.get("required_artifacts"))
        if not expected_outputs and required_artifacts:
            expected_outputs = list(required_artifacts)
        return PlanStepContract(
            title=title,
            step_id=step_id,
            objective=str(raw_step.get("objective", title)).strip() or title,
            agent_role=str(raw_step.get("agent_role") or raw_step.get("agent") or "engineer").strip() or "engineer",
            dependencies=cls._string_list(raw_step.get("dependencies") or raw_step.get("depends_on")),
            allowed_paths=cls._string_list(raw_step.get("allowed_paths")),
            expected_outputs=expected_outputs,
            required_artifacts=required_artifacts,
            validation=cls._string_list(raw_step.get("validation")),
            done_when=cls._string_list(raw_step.get("done_when")),
            validation_commands=cls._string_list(raw_step.get("validation_commands")),
            notes=cls._string_list(raw_step.get("notes")),
        )

    @classmethod
    def _default_step_contract(cls, title: str, index: int) -> PlanStepContract:
        return PlanStepContract(
            title=title,
            step_id=f"S{index + 1}",
            objective=title,
            agent_role="engineer",
            dependencies=[],
            allowed_paths=[],
            expected_outputs=[],
            required_artifacts=[],
            validation=[],
            done_when=[],
            validation_commands=[],
            notes=[],
        )

    @staticmethod
    def _extract_steps_from_text(text: str) -> list[str]:
        steps: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^(\d+\.|-)\s+", stripped):
                normalized = re.sub(r"^(\d+\.|-)\s+", "", stripped).strip()
                if normalized:
                    steps.append(normalized)
        if not steps:
            steps = [
                "Create or update the core implementation modules",
                "Wire the feature into routes, services, or entry points",
                "Add or update tests and validation",
            ]
        return steps

    @staticmethod
    def _artifact_file_path(artifact: str) -> str | None:
        cleaned = artifact.strip()
        if not cleaned:
            return None
        if cleaned.startswith("file:"):
            payload = cleaned[5:].strip()
            for suffix in (" exists", " not_empty"):
                if payload.endswith(suffix):
                    return payload[: -len(suffix)].strip()
            if " contains " in payload:
                return payload.split(" contains ", 1)[0].strip()
            return payload
        if "/" in cleaned or Path(cleaned).suffix:
            return cleaned
        return None

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []

    @staticmethod
    def _expected_outputs(contract: PlanStepContract) -> list[str]:
        outputs = list(contract.expected_outputs or [])
        if not outputs and contract.required_artifacts:
            outputs.extend(contract.required_artifacts)
        return list(dict.fromkeys(outputs))

    @staticmethod
    def _validation_items(contract: PlanStepContract) -> list[str]:
        items = list(contract.validation or [])
        if contract.done_when:
            items.extend(contract.done_when)
        if contract.validation_commands:
            items.extend(f"command:{item}" for item in contract.validation_commands)
        return list(dict.fromkeys(items))

    def _next_pending_index(self) -> int | None:
        for step in self.steps:
            if step.status != "completed":
                return step.index
        return None
