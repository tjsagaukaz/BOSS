from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from boss.types import ProjectBrain, ProjectMap, utc_now_iso


class ProjectBrainStore:
    MAX_ARCHITECTURE = 8
    MAX_BRAIN_RULES = 8
    MAX_MILESTONES = 10
    MAX_RECENT_PROGRESS = 8
    MAX_OPEN_PROBLEMS = 8
    MAX_NEXT_PRIORITIES = 6
    MAX_KNOWN_RISKS = 8
    MAX_RECENT_ARTIFACTS = 10
    DEFAULT_POLICY = {
        "update_mode": "confirm",
        "allow_sources": ["explicit_signal", "milestone", "manual", "eval_runs"],
        "auto_sources": ["eval_runs"],
        "require_confirmation": ["explicit_signal", "milestone"],
    }

    def __init__(self, root_dir: str | Path, task_history=None, evaluation_store=None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.task_history = task_history
        self.evaluation_store = evaluation_store
        self.data_dir = self.root_dir.parent
        self.proposals_dir = self.data_dir / "brain_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.history_root = self.root_dir / "_history"
        self.history_root.mkdir(parents=True, exist_ok=True)
        self.policy_path = self.data_dir / "brain_policy.yaml"
        self._ensure_policy_file()

    def load(
        self,
        project_name: str,
        *,
        summary: str = "",
        project_map: ProjectMap | None = None,
    ) -> ProjectBrain:
        path = self._brain_path(project_name)
        if path.exists():
            payload = self._read_payload(path)
            brain = self._brain_from_payload(project_name, payload, summary=summary, project_map=project_map)
            seeded_architecture = self._seed_architecture(project_map)
            if self._merge_unique(brain.architecture, seeded_architecture) != brain.architecture:
                brain.architecture = self._merge_unique(brain.architecture, seeded_architecture)[: self.MAX_ARCHITECTURE]
                brain.updated_at = utc_now_iso()
                self.save(brain)
            return brain

        brain = self._seed_brain(project_name, summary=summary, project_map=project_map)
        self.save(brain)
        return brain

    def save(self, brain: ProjectBrain) -> None:
        payload = {
            "project": brain.project_name,
            "mission": brain.mission,
            "focus": brain.current_focus,
            "architecture": brain.architecture,
            "brain_rules": brain.brain_rules,
            "milestones": brain.milestones,
            "recent_progress": brain.recent_progress,
            "open_problems": brain.open_problems,
            "next_priorities": brain.next_priorities,
            "known_risks": brain.known_risks,
            "recent_artifacts": brain.recent_artifacts,
            "updated_at": brain.updated_at or utc_now_iso(),
        }
        path = self._brain_path(brain.project_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._write_history_snapshot(brain.project_name, payload)

    def policy(self) -> dict[str, Any]:
        self._ensure_policy_file()
        if yaml is None:  # pragma: no cover
            return dict(self.DEFAULT_POLICY)
        try:
            raw = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
        except (OSError, AttributeError):
            raw = {}
        policy = dict(self.DEFAULT_POLICY)
        for key in ("allow_sources", "auto_sources", "require_confirmation"):
            value = raw.get(key, policy[key])
            policy[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else list(policy[key])
        mode = str(raw.get("update_mode", policy["update_mode"])).strip().lower()
        policy["update_mode"] = mode if mode in {"auto", "confirm", "manual"} else "confirm"
        return policy

    def summary_text(self, brain: ProjectBrain) -> str:
        recent_progress = "\n".join(f"- {item}" for item in brain.recent_progress[:3]) or "- None recorded"
        open_problems = "\n".join(f"- {item}" for item in brain.open_problems[:3]) or "- None recorded"
        next_priorities = "\n".join(f"- {item}" for item in self.effective_next_priorities(brain)[:3]) or "- None recorded"
        milestones = "\n".join(f"- {item}" for item in brain.milestones[:3]) or "- None recorded"
        risks = "\n".join(f"- {item}" for item in brain.known_risks[:3]) or "- None recorded"
        rules = "\n".join(f"- {item}" for item in brain.brain_rules[:3]) or "- None recorded"
        return "\n".join(
            [
                f"Project: {brain.project_name}",
                f"Mission: {brain.mission or 'Not recorded'}",
                f"Current Focus: {brain.current_focus or 'Not recorded'}",
                f"Brain Rules:\n{rules}",
                f"Milestones:\n{milestones}",
                f"Recent Progress:\n{recent_progress}",
                f"Open Problems:\n{open_problems}",
                f"Next Priorities:\n{next_priorities}",
                f"Known Risks:\n{risks}",
            ]
        )

    def effective_next_priorities(self, brain: ProjectBrain) -> list[str]:
        if brain.next_priorities:
            return brain.next_priorities[: self.MAX_NEXT_PRIORITIES]
        if brain.open_problems:
            return brain.open_problems[: self.MAX_NEXT_PRIORITIES]
        if brain.known_risks:
            return brain.known_risks[: self.MAX_NEXT_PRIORITIES]
        if brain.recent_progress:
            return [f"Continue from recent progress: {brain.recent_progress[0]}"]
        if brain.architecture:
            return [f"Advance {item}" for item in brain.architecture[:3]]
        return []

    def list_proposals(
        self,
        *,
        project_name: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for path in sorted(self.proposals_dir.glob("proposal_*.json"), reverse=True):
            payload = self._read_payload(path)
            if not payload:
                continue
            if project_name and str(payload.get("project", "")) != project_name:
                continue
            if status and str(payload.get("status", "")) != status:
                continue
            proposals.append(payload)
            if len(proposals) >= limit:
                break
        return proposals

    def approve_proposal(self, proposal_id: int) -> dict[str, Any]:
        payload = self._load_proposal(proposal_id)
        status = str(payload.get("status", ""))
        if status != "pending":
            raise ValueError(f"Proposal {proposal_id} is not pending.")
        project_name = str(payload.get("project", "")).strip()
        if not project_name:
            raise ValueError(f"Proposal {proposal_id} is missing a project.")
        brain = self.load(project_name)
        brain = self._apply_updates(brain, dict(payload.get("proposal", {})))
        brain.updated_at = utc_now_iso()
        self.save(brain)
        payload["status"] = "approved"
        payload["approved_at"] = utc_now_iso()
        payload["applied_at"] = payload["approved_at"]
        self._write_proposal(payload)
        return {
            "proposal_id": proposal_id,
            "project_name": project_name,
            "status": payload["status"],
            "brain": brain,
        }

    def list_rules(self, project_name: str, *, summary: str = "", project_map: ProjectMap | None = None) -> list[str]:
        brain = self.load(project_name, summary=summary, project_map=project_map)
        return list(brain.brain_rules)

    def add_rule(
        self,
        project_name: str,
        rule: str,
        *,
        summary: str = "",
        project_map: ProjectMap | None = None,
    ) -> ProjectBrain:
        normalized = self._normalize_rule_text(rule)
        if not normalized:
            raise ValueError("Brain rule is required.")
        result = self._propose_or_apply(
            project_name,
            source="manual",
            updates={"brain_rules_add": [normalized]},
            summary=f"Manual brain rule: {normalized}",
        )
        brain = result["brain"]
        if isinstance(brain, ProjectBrain):
            return brain
        return self.load(project_name, summary=summary, project_map=project_map)

    def remove_rule(
        self,
        project_name: str,
        rule: str,
        *,
        summary: str = "",
        project_map: ProjectMap | None = None,
    ) -> ProjectBrain:
        normalized = self._normalize_rule_text(rule)
        if not normalized:
            raise ValueError("Brain rule is required.")
        result = self._propose_or_apply(
            project_name,
            source="manual",
            updates={"brain_rules_remove": [normalized]},
            summary=f"Remove brain rule: {normalized}",
        )
        brain = result["brain"]
        if isinstance(brain, ProjectBrain):
            return brain
        return self.load(project_name, summary=summary, project_map=project_map)

    def reject_proposal(self, proposal_id: int) -> dict[str, Any]:
        payload = self._load_proposal(proposal_id)
        status = str(payload.get("status", ""))
        if status != "pending":
            raise ValueError(f"Proposal {proposal_id} is not pending.")
        payload["status"] = "rejected"
        payload["rejected_at"] = utc_now_iso()
        self._write_proposal(payload)
        return {
            "proposal_id": proposal_id,
            "project_name": str(payload.get("project", "")),
            "status": payload["status"],
            "summary": str(payload.get("summary", "")),
        }

    def reset(self, project_name: str, *, summary: str = "", project_map: ProjectMap | None = None) -> ProjectBrain:
        snapshots = self._history_snapshots(project_name)
        if len(snapshots) >= 2:
            target = snapshots[-2]
            brain = self._brain_from_payload(project_name, target, summary=summary, project_map=project_map)
        else:
            brain = self._seed_brain(project_name, summary=summary, project_map=project_map)
        brain.updated_at = utc_now_iso()
        self.save(brain)
        return brain

    def record_task_completion(
        self,
        project_name: str,
        *,
        task: str,
        status: str,
        changed_files: list[str] | None = None,
        errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_path: str | None = None,
    ) -> ProjectBrain:
        metadata = metadata or {}
        normalized_status = status.strip().lower()
        if not self._is_milestone_task(task, changed_files or [], metadata):
            return self.load(project_name)

        updates: dict[str, Any] = {}
        if normalized_status == "completed":
            summary = f"{task} completed"
            if changed_files:
                summary += f" ({len(changed_files)} file{'s' if len(changed_files) != 1 else ''} changed)"
            updates["recent_progress_add"] = [summary]
            updates["milestones_add"] = [task]
            updates["open_problems_remove_related_to"] = [task]
            updates["next_priorities_remove_related_to"] = [task]
        else:
            failure = f"{task} {normalized_status}"
            if errors:
                failure += f": {self._compact_text(errors[0], 140)}"
            updates["open_problems_add"] = [failure]
            updates["next_priorities_add"] = [task]
            updates["known_risks_add"] = [failure]
            if not metadata.get("current_focus"):
                updates["current_focus"] = f"Resolve: {task}"

        if artifact_path:
            updates["recent_artifact"] = {
                "kind": "task",
                "task": task,
                "status": normalized_status,
                "artifact_path": artifact_path,
                "timestamp": utc_now_iso(),
            }

        steps = [str(item).strip() for item in metadata.get("steps", []) if str(item).strip()]
        if normalized_status != "completed" and steps:
            updates["next_priorities_add"] = [*updates.get("next_priorities_add", []), *steps[:3]]

        result = self._propose_or_apply(
            project_name,
            source="milestone",
            updates=updates,
            summary=f"Task milestone: {task}",
        )
        return result["brain"]

    def record_evaluation(
        self,
        project_name: str,
        *,
        suite_name: str,
        status: str,
        passed_tasks: int,
        total_tasks: int,
        runtime_seconds: float,
        artifact_path: str | None = None,
        failure_map: dict[str, int] | None = None,
    ) -> ProjectBrain:
        normalized_status = status.strip().lower()
        updates: dict[str, Any] = {}
        if normalized_status in {"passed", "completed"}:
            updates["recent_progress_add"] = [
                f"Evaluation passed: {suite_name} ({passed_tasks}/{total_tasks} tasks, {runtime_seconds:.2f}s)"
            ]
        else:
            problem = f"Evaluation failed: {suite_name} ({passed_tasks}/{total_tasks} tasks)"
            if failure_map:
                top = sorted(failure_map.items(), key=lambda item: (-item[1], item[0]))[0]
                problem += f" [{top[0]}={top[1]}]"
            updates["open_problems_add"] = [problem]
            updates["next_priorities_add"] = [f"Resolve {suite_name} evaluation failures"]
            updates["known_risks_add"] = [problem]
            updates["current_focus"] = f"Resolve evaluation failures in {suite_name}"

        if artifact_path:
            updates["recent_artifact"] = {
                "kind": "evaluation",
                "task": suite_name,
                "status": normalized_status,
                "artifact_path": artifact_path,
                "timestamp": utc_now_iso(),
            }

        result = self._propose_or_apply(
            project_name,
            source="eval_runs",
            updates=updates,
            summary=f"Evaluation result: {suite_name}",
        )
        return result["brain"]

    def record_conversation_signal(self, project_name: str, message: str) -> bool:
        text = message.strip()
        if not text:
            return False

        updates: dict[str, Any] = {}
        focus = self._extract_signal(
            text,
            [
                r"\bwe(?:'re| are)? focusing on (?P<value>.+)",
                r"\bcurrent focus is (?P<value>.+)",
                r"\bfocus on (?P<value>.+)",
            ],
        )
        if focus:
            updates["current_focus"] = focus

        mission = self._extract_signal(
            text,
            [
                r"\bmission is (?P<value>.+)",
                r"\bgoal is (?P<value>.+)",
                r"\bwe are building (?P<value>.+)",
            ],
        )
        if mission:
            updates["mission"] = mission

        priority = self._extract_signal(
            text,
            [
                r"\bnext priorit(?:y|ies) (?:is|are) (?P<value>.+)",
                r"\bprioriti[sz]e (?P<value>.+)",
            ],
        )
        if priority:
            updates["next_priorities_add"] = [
                item.strip() for item in re.split(r"\s*(?:,| and )\s*", priority) if item.strip()
            ][:3]

        problem = self._extract_signal(
            text,
            [
                r"\bblocked on (?P<value>.+)",
                r"\bopen problem is (?P<value>.+)",
                r"\bproblem is (?P<value>.+)",
            ],
        )
        if problem:
            updates["open_problems_add"] = [problem]
            updates["known_risks_add"] = [problem]

        brain_rules = self._extract_brain_rules(text)
        if brain_rules:
            updates["brain_rules_add"] = brain_rules

        if not updates:
            return False

        result = self._propose_or_apply(
            project_name,
            source="explicit_signal",
            updates=updates,
            summary=f"Conversation signal: {self._compact_text(text, 100)}",
        )
        return result["status"] in {"applied", "pending"}

    def _propose_or_apply(
        self,
        project_name: str,
        *,
        source: str,
        updates: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        brain = self.load(project_name)
        if not updates:
            return {"status": "noop", "brain": brain, "proposal_id": None}

        decision = self._policy_decision(source)
        if decision == "ignored":
            return {"status": "ignored", "brain": brain, "proposal_id": None}
        if decision == "apply":
            brain = self._apply_updates(brain, updates)
            brain.updated_at = utc_now_iso()
            self.save(brain)
            return {"status": "applied", "brain": brain, "proposal_id": None}

        proposal_id = self._find_matching_pending_proposal(project_name, source, updates)
        if proposal_id is None:
            proposal_id = self._create_proposal(project_name, source, updates, summary)
        return {"status": "pending", "brain": brain, "proposal_id": proposal_id}

    def _apply_updates(self, brain: ProjectBrain, updates: dict[str, Any]) -> ProjectBrain:
        mission = str(updates.get("mission", "")).strip()
        if mission:
            brain.mission = mission
        current_focus = str(updates.get("current_focus", "")).strip()
        if current_focus:
            brain.current_focus = current_focus

        brain.architecture = self._merge_unique(
            [*brain.architecture, *[str(item) for item in updates.get("architecture_add", []) if str(item).strip()]],
            [],
        )[: self.MAX_ARCHITECTURE]
        brain.brain_rules = self._merge_unique(
            [*brain.brain_rules, *[self._normalize_rule_text(item) for item in updates.get("brain_rules_add", []) if self._normalize_rule_text(item)]],
            [],
        )[: self.MAX_BRAIN_RULES]
        brain.milestones = self._merge_unique(
            [*brain.milestones, *[str(item) for item in updates.get("milestones_add", []) if str(item).strip()]],
            [],
        )[: self.MAX_MILESTONES]

        for value in updates.get("recent_progress_add", []):
            brain.recent_progress = self._prepend_unique(brain.recent_progress, str(value), self.MAX_RECENT_PROGRESS)
        for value in updates.get("open_problems_add", []):
            brain.open_problems = self._prepend_unique(brain.open_problems, str(value), self.MAX_OPEN_PROBLEMS)
        for value in updates.get("next_priorities_add", []):
            brain.next_priorities = self._prepend_unique(brain.next_priorities, str(value), self.MAX_NEXT_PRIORITIES)
        for value in updates.get("known_risks_add", []):
            brain.known_risks = self._prepend_unique(brain.known_risks, str(value), self.MAX_KNOWN_RISKS)

        for reference in updates.get("open_problems_remove_related_to", []):
            brain.open_problems = self._remove_related(brain.open_problems, str(reference))
        for reference in updates.get("next_priorities_remove_related_to", []):
            brain.next_priorities = self._remove_related(brain.next_priorities, str(reference))
        for reference in updates.get("brain_rules_remove", []):
            normalized = self._normalize_rule_text(str(reference))
            if not normalized:
                continue
            brain.brain_rules = [item for item in brain.brain_rules if self._normalize_rule_text(item) != normalized]

        recent_artifact = updates.get("recent_artifact")
        if isinstance(recent_artifact, dict):
            brain.recent_artifacts = self._prepend_artifact(brain.recent_artifacts, recent_artifact)
        return brain

    def _policy_decision(self, source: str) -> str:
        policy = self.policy()
        if source == "manual":
            return "apply"
        if source not in policy["allow_sources"]:
            return "ignored"
        if source in policy["auto_sources"] or policy["update_mode"] == "auto":
            return "apply"
        if policy["update_mode"] == "manual":
            return "pending"
        if source in policy["require_confirmation"] or policy["update_mode"] == "confirm":
            return "pending"
        return "apply"

    def _create_proposal(self, project_name: str, source: str, updates: dict[str, Any], summary: str) -> int:
        proposal_id = self._next_proposal_id()
        payload = {
            "id": proposal_id,
            "project": project_name,
            "source": source,
            "summary": summary,
            "proposal": updates,
            "status": "pending",
            "created_at": utc_now_iso(),
        }
        self._write_proposal(payload)
        return proposal_id

    def _find_matching_pending_proposal(self, project_name: str, source: str, updates: dict[str, Any]) -> int | None:
        candidate = json.dumps(updates, sort_keys=True, default=str)
        for proposal in self.list_proposals(project_name=project_name, status="pending", limit=200):
            if str(proposal.get("source", "")) != source:
                continue
            if json.dumps(proposal.get("proposal", {}), sort_keys=True, default=str) == candidate:
                return int(proposal["id"])
        return None

    def _write_proposal(self, payload: dict[str, Any]) -> None:
        path = self.proposals_dir / f"proposal_{int(payload['id']):04d}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _load_proposal(self, proposal_id: int) -> dict[str, Any]:
        path = self.proposals_dir / f"proposal_{proposal_id:04d}.json"
        if not path.exists():
            raise FileNotFoundError(f"Brain proposal {proposal_id} was not found.")
        return self._read_payload(path)

    def _next_proposal_id(self) -> int:
        highest = 0
        for path in self.proposals_dir.glob("proposal_*.json"):
            match = re.search(r"proposal_(\d+)\.json$", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def _ensure_policy_file(self) -> None:
        if self.policy_path.exists():
            return
        if yaml is None:  # pragma: no cover
            self.policy_path.write_text(json.dumps(self.DEFAULT_POLICY, indent=2, sort_keys=True), encoding="utf-8")
            return
        self.policy_path.write_text(yaml.safe_dump(self.DEFAULT_POLICY, sort_keys=False), encoding="utf-8")

    def _write_history_snapshot(self, project_name: str, payload: dict[str, Any]) -> None:
        project_dir = self.history_root / self._project_safe_name(project_name)
        project_dir.mkdir(parents=True, exist_ok=True)
        latest = self._latest_history_payload(project_name)
        if latest == payload:
            return
        timestamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
        path = project_dir / f"{timestamp}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _latest_history_payload(self, project_name: str) -> dict[str, Any] | None:
        snapshots = self._history_paths(project_name)
        if not snapshots:
            return None
        return self._read_payload(snapshots[-1])

    def _history_snapshots(self, project_name: str) -> list[dict[str, Any]]:
        return [self._read_payload(path) for path in self._history_paths(project_name)]

    def _history_paths(self, project_name: str) -> list[Path]:
        project_dir = self.history_root / self._project_safe_name(project_name)
        if not project_dir.exists():
            return []
        return sorted(path for path in project_dir.glob("*.json") if path.is_file())

    def _brain_path(self, project_name: str) -> Path:
        safe = self._project_safe_name(project_name)
        return self.root_dir / f"{safe}.json"

    def _project_safe_name(self, project_name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", project_name.strip()).strip("_") or "project"

    def _read_payload(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _brain_from_payload(
        self,
        project_name: str,
        payload: dict[str, Any],
        *,
        summary: str = "",
        project_map: ProjectMap | None = None,
    ) -> ProjectBrain:
        return ProjectBrain(
            project_name=project_name,
            mission=str(payload.get("mission", "") or summary or ""),
            current_focus=str(payload.get("focus", payload.get("current_focus", "")) or ""),
            architecture=self._merge_unique(
                [str(item) for item in payload.get("architecture", []) if str(item).strip()],
                self._seed_architecture(project_map),
            )[: self.MAX_ARCHITECTURE],
            brain_rules=[
                self._normalize_rule_text(item)
                for item in (payload.get("brain_rules", payload.get("rules", [])) or [])
                if self._normalize_rule_text(item)
            ][: self.MAX_BRAIN_RULES],
            milestones=[str(item) for item in payload.get("milestones", []) if str(item).strip()][: self.MAX_MILESTONES],
            recent_progress=[str(item) for item in payload.get("recent_progress", []) if str(item).strip()][
                : self.MAX_RECENT_PROGRESS
            ],
            open_problems=[str(item) for item in payload.get("open_problems", []) if str(item).strip()][
                : self.MAX_OPEN_PROBLEMS
            ],
            next_priorities=[str(item) for item in payload.get("next_priorities", []) if str(item).strip()][
                : self.MAX_NEXT_PRIORITIES
            ],
            known_risks=[str(item) for item in payload.get("known_risks", []) if str(item).strip()][
                : self.MAX_KNOWN_RISKS
            ],
            recent_artifacts=[
                dict(item) for item in payload.get("recent_artifacts", []) if isinstance(item, dict)
            ][: self.MAX_RECENT_ARTIFACTS],
            updated_at=str(payload.get("updated_at", "") or utc_now_iso()),
        )

    def _seed_brain(self, project_name: str, *, summary: str = "", project_map: ProjectMap | None = None) -> ProjectBrain:
        recent_progress: list[str] = []
        open_problems: list[str] = []
        next_priorities: list[str] = []
        milestones: list[str] = []
        known_risks: list[str] = []

        if self.task_history is not None:
            for task in self.task_history.recent_tasks(project_name=project_name, limit=6):
                task_text = str(task.get("task", "")).strip()
                status = str(task.get("status", "")).strip().lower()
                if not task_text:
                    continue
                if status == "completed":
                    recent_progress = self._prepend_unique(recent_progress, task_text, self.MAX_RECENT_PROGRESS)
                    if self._is_milestone_text(task_text):
                        milestones = self._prepend_unique(milestones, task_text, self.MAX_MILESTONES)
                elif status in {"failed", "stopped", "aborted"}:
                    open_problems = self._prepend_unique(open_problems, task_text, self.MAX_OPEN_PROBLEMS)
                    next_priorities = self._prepend_unique(next_priorities, task_text, self.MAX_NEXT_PRIORITIES)
                    known_risks = self._prepend_unique(known_risks, task_text, self.MAX_KNOWN_RISKS)

        if self.evaluation_store is not None:
            eval_runs = [
                run for run in self.evaluation_store.recent_runs(limit=10) if str(run.get("project_name", "")) == project_name
            ]
            for run in eval_runs[:4]:
                suite_name = str(run.get("suite_name", "")).strip()
                status = str(run.get("status", "")).strip().lower()
                if not suite_name:
                    continue
                if status in {"passed", "completed"}:
                    recent_progress = self._prepend_unique(
                        recent_progress,
                        f"Evaluation passed: {suite_name}",
                        self.MAX_RECENT_PROGRESS,
                    )
                elif status not in {"skipped"}:
                    problem = f"Evaluation failed: {suite_name}"
                    open_problems = self._prepend_unique(open_problems, problem, self.MAX_OPEN_PROBLEMS)
                    next_priorities = self._prepend_unique(
                        next_priorities,
                        f"Resolve {suite_name} evaluation failures",
                        self.MAX_NEXT_PRIORITIES,
                    )
                    known_risks = self._prepend_unique(known_risks, problem, self.MAX_KNOWN_RISKS)

        current_focus = ""
        if open_problems:
            current_focus = open_problems[0]
        elif recent_progress:
            current_focus = f"Continue from {recent_progress[0]}"

        return ProjectBrain(
            project_name=project_name,
            mission=summary or (project_map.overview if project_map else f"Advance project {project_name}"),
            current_focus=current_focus,
            architecture=self._seed_architecture(project_map),
            brain_rules=[],
            milestones=milestones[: self.MAX_MILESTONES],
            recent_progress=recent_progress[: self.MAX_RECENT_PROGRESS],
            open_problems=open_problems[: self.MAX_OPEN_PROBLEMS],
            next_priorities=next_priorities[: self.MAX_NEXT_PRIORITIES],
            known_risks=known_risks[: self.MAX_KNOWN_RISKS],
            recent_artifacts=[],
            updated_at=utc_now_iso(),
        )

    def _seed_architecture(self, project_map: ProjectMap | None) -> list[str]:
        if project_map is None:
            return []
        candidates = list(project_map.main_modules[:5]) + list(project_map.entry_points[:2]) + list(project_map.key_files[:2])
        return self._merge_unique([], [str(item) for item in candidates if str(item).strip()])[: self.MAX_ARCHITECTURE]

    def _extract_signal(self, text: str, patterns: list[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = self._normalize_signal_value(match.group("value"))
            if value:
                return value
        return ""

    def _extract_brain_rules(self, text: str) -> list[str]:
        patterns = [
            (r"\bnever use (?P<value>.+)", "Never use {value}."),
            (r"\bdo not use (?P<value>.+)", "Do not use {value}."),
            (r"\bdon't use (?P<value>.+)", "Do not use {value}."),
            (r"\balways use (?P<value>.+)", "Always use {value}."),
            (r"\bprefer (?P<value>.+)", "Prefer {value}."),
            (r"\bavoid (?P<value>.+)", "Avoid {value}."),
        ]
        rules: list[str] = []
        for pattern, template in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = self._normalize_signal_value(match.group("value"))
            if not value:
                continue
            rules.append(self._normalize_rule_text(template.format(value=value)))
        return self._merge_unique([], [rule for rule in rules if rule])[:3]

    def _normalize_signal_value(self, value: str) -> str:
        cleaned = value.strip().strip(".!? ")
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        return cleaned[:180]

    def _normalize_rule_text(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value).strip())
        if not cleaned:
            return ""
        if cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned[:220]

    def _is_milestone_task(self, task: str, changed_files: list[str], metadata: dict[str, Any]) -> bool:
        if self._is_milestone_text(task):
            return True
        if len(changed_files) >= 2:
            return True
        steps = [str(item).strip() for item in metadata.get("steps", []) if str(item).strip()]
        return len(steps) >= 2

    def _is_milestone_text(self, text: str) -> bool:
        lowered = text.lower()
        keywords = ("implement", "build", "add", "enable", "introduce", "create", "complete", "stabilize", "ship")
        return any(keyword in lowered for keyword in keywords)

    def _prepend_unique(self, items: list[str], value: str, limit: int) -> list[str]:
        cleaned = self._compact_text(value, 180)
        if not cleaned:
            return list(items)
        result = [cleaned]
        for item in items:
            normalized_item = str(item).strip()
            if normalized_item and normalized_item.lower() != cleaned.lower():
                result.append(normalized_item)
        return result[:limit]

    def _prepend_artifact(self, items: list[dict[str, Any]], artifact: dict[str, Any]) -> list[dict[str, Any]]:
        artifact_path = str(artifact.get("artifact_path", ""))
        result = [artifact]
        for item in items:
            if str(item.get("artifact_path", "")) == artifact_path:
                continue
            result.append(dict(item))
        return result[: self.MAX_RECENT_ARTIFACTS]

    def _remove_related(self, items: list[str], reference: str) -> list[str]:
        reference_tokens = self._tokens(reference)
        if not reference_tokens:
            return list(items)
        filtered: list[str] = []
        for item in items:
            item_text = str(item).strip()
            if not item_text:
                continue
            if self._tokens(item_text) & reference_tokens:
                continue
            filtered.append(item_text)
        return filtered

    def _merge_unique(self, primary: list[str], secondary: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*primary, *secondary]:
            cleaned = str(item).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            merged.append(cleaned)
            seen.add(key)
        return merged

    def _tokens(self, value: str) -> set[str]:
        return {token for token in re.split(r"[^A-Za-z0-9]+", value.lower()) if len(token) >= 4}

    def _compact_text(self, value: str, limit: int) -> str:
        return re.sub(r"\s+", " ", value.strip())[:limit]
