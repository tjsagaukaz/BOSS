from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from boss.types import ProjectContext, ToolDefinition


@dataclass
class ContextEnvelope:
    sections: list[tuple[str, str]]

    def render(self) -> str:
        return "\n\n".join(f"=== {title} ===\n{body}" for title, body in self.sections)


class ContextEnvelopeBuilder:
    MAX_IMPORTANT_FILES = 10
    MAX_RELEVANT_FILES = 5
    MAX_SOLUTIONS = 3
    MAX_SIMILAR_TASKS = 3
    MAX_GRAPH_INSIGHTS = 5
    MAX_TOOL_DEFINITIONS = 10
    MAX_TOOL_DESCRIPTION = 160
    MAX_SNIPPET_LENGTH = 220

    def build(
        self,
        *,
        role: str,
        task: str,
        project_context: ProjectContext,
        tools: list[ToolDefinition] | None = None,
        supplemental_context: str = "",
        task_contract: dict[str, Any] | None = None,
        execution_rules: list[str] | None = None,
        execution_spine: dict[str, Any] | None = None,
    ) -> ContextEnvelope:
        sections = [
            ("TASK CONTRACT", self._task_contract(role, task, project_context, task_contract, supplemental_context)),
            ("EXECUTION RULES", self._execution_rules(role, project_context, execution_rules)),
            ("PROJECT BRAIN", self._project_brain(project_context)),
            ("EXECUTION SPINE", self._execution_spine(execution_spine)),
            ("WORKSPACE STATE", self._workspace_state(project_context)),
            ("PROJECT INTELLIGENCE", self._project_intelligence(project_context)),
            ("ARCHITECTURE MAP", self._architecture_map(project_context)),
            ("RELEVANT FILES", self._relevant_files(project_context)),
            ("SOLUTION LIBRARY MATCHES", self._solution_matches(project_context)),
            ("STYLE PROFILE", self._style_profile(project_context)),
            ("TOOL CONTRACTS", self._tool_contracts(tools or [])),
        ]
        return ContextEnvelope(sections=sections)

    def _task_contract(
        self,
        role: str,
        task: str,
        project_context: ProjectContext,
        task_contract: dict[str, Any] | None,
        supplemental_context: str,
    ) -> str:
        contract = dict(task_contract or {})
        goal = str(contract.pop("goal", task)).strip() or task
        lines = [
            f"Role: {role}",
            f"Project: {project_context.name}",
            f"Goal: {goal}",
        ]

        active_file = project_context.active_file or "None"
        lines.append(f"Active File: {active_file}")

        if contract:
            for key, value in contract.items():
                label = str(key).replace("_", " ").title()
                lines.append(f"{label}: {self._render_value(value)}")

        if supplemental_context.strip():
            lines.append("Supplemental Context:")
            lines.append(supplemental_context.strip())

        return "\n".join(lines)

    def _project_brain(self, project_context: ProjectContext) -> str:
        brain = project_context.project_brain
        if brain is None:
            return "No active project brain recorded."
        recent_progress = "\n".join(f"- {item}" for item in brain.recent_progress[:4]) or "- None recorded"
        brain_rules = "\n".join(f"- {item}" for item in brain.brain_rules[:5]) or "- None recorded"
        milestones = "\n".join(f"- {item}" for item in brain.milestones[:4]) or "- None recorded"
        open_problems = "\n".join(f"- {item}" for item in brain.open_problems[:4]) or "- None recorded"
        next_priorities = "\n".join(f"- {item}" for item in brain.next_priorities[:4]) or "- None recorded"
        known_risks = "\n".join(f"- {item}" for item in brain.known_risks[:4]) or "- None recorded"
        architecture = ", ".join(brain.architecture[:6]) or "None recorded"
        return "\n".join(
            [
                f"Mission: {brain.mission or 'Not recorded'}",
                f"Current Focus: {brain.current_focus or 'Not recorded'}",
                f"Architecture: {architecture}",
                f"Brain Rules:\n{brain_rules}",
                f"Milestones:\n{milestones}",
                f"Recent Progress:\n{recent_progress}",
                f"Open Problems:\n{open_problems}",
                f"Next Priorities:\n{next_priorities}",
                f"Known Risks:\n{known_risks}",
            ]
        )

    def _execution_spine(self, execution_spine: dict[str, Any] | None) -> str:
        if not execution_spine:
            return "No execution spine provided. Follow the task contract directly."

        current = execution_spine.get("step") if isinstance(execution_spine.get("step"), dict) else {}
        lines = [
            f"Task ID: {execution_spine.get('task_id', 'unknown')}",
            f"Goal: {execution_spine.get('goal', '')}",
            f"Current Step: {execution_spine.get('current_step_number', 0)} of {execution_spine.get('total_steps', 0)}",
            f"Completed Steps: {execution_spine.get('completed_steps', 0)}",
            f"Steps Remaining: {execution_spine.get('steps_remaining', 0)}",
        ]
        if current:
            lines.extend(
                [
                    f"Step ID: {current.get('id', '')}",
                    f"Step Title: {current.get('title', '')}",
                    f"Step Goal: {current.get('goal', '')}",
                    f"Allowed Paths: {self._render_value(current.get('allowed_paths', []))}",
                    f"Expected Outputs: {self._render_value(current.get('expected_outputs', []))}",
                    f"Validation: {self._render_value(current.get('validation', []))}",
                    f"Validation Commands: {self._render_value(current.get('validation_commands', []))}",
                    f"Step Status: {current.get('status', 'pending')}",
                    f"Step Attempts: {current.get('attempts', 0)}",
                ]
            )
        return "\n".join(lines)

    def _execution_rules(
        self,
        role: str,
        project_context: ProjectContext,
        execution_rules: list[str] | None,
    ) -> str:
        rules = [
            "Follow the task contract exactly. Do not broaden scope without explicit evidence from the project context.",
            "Prefer existing architecture patterns, dependencies, and file locations over inventing new structure.",
            "Do not modify unrelated files or rewrite working code without a concrete reason tied to the task.",
            "Use the smallest relevant context set. Do not assume hidden repo state beyond the envelope.",
            "Do not claim success without validation evidence from tests, commands, or explicit audit reasoning.",
            "Read before write. When changing code, preserve public APIs unless the contract explicitly allows otherwise.",
        ]
        role_specific = {
            "architect": "Produce a concrete plan and constraints. Do not pretend implementation already happened.",
            "engineer": "Make minimal, direct changes. Validate the result before stopping.",
            "auditor": "Act as a critical reviewer. Prioritize bugs, regressions, missing tests, and unsafe assumptions.",
            "test": "Prioritize deterministic tests and direct coverage of the requested behavior.",
            "security": "Assume hostile input. Prioritize auth, secrets, validation, and trust-boundary issues.",
            "documentation": "Document actual behavior only. Do not describe unimplemented features as complete.",
            "conversation": "Answer naturally as TJ's trusted partner. Distinguish discussion from execution and suggest the next concrete BOSS action when useful.",
        }
        if role in role_specific:
            rules.append(role_specific[role])
        brain = project_context.project_brain
        if brain is not None and brain.brain_rules:
            rules.extend(f"Project Brain Rule: {rule}" for rule in brain.brain_rules[:5])
        if execution_rules:
            rules.extend(str(item).strip() for item in execution_rules if str(item).strip())
        return "\n".join(f"- {rule}" for rule in rules)

    def _workspace_state(self, project_context: ProjectContext) -> str:
        workspace = project_context.workspace_state
        open_files = ", ".join((workspace.open_files if workspace else project_context.recent_files)[:5]) or "None"
        recent_edits_source = workspace.recent_edits if workspace is not None else project_context.recent_changes
        recent_edits = "\n".join(
            f"- {item.get('file', 'unknown')}: {item.get('type', 'edit')} {item.get('summary', '')}".strip()
            for item in recent_edits_source[:5]
        ) or "- None recorded"
        last_terminal_command = workspace.last_terminal_command if workspace is not None else "None"
        last_test_results = workspace.last_test_results if workspace is not None else {}
        last_test_status = "No test run recorded."
        if last_test_results:
            failed_tests = last_test_results.get("failed_tests", [])
            failure_summary = str(last_test_results.get("failure_summary", "")).strip()
            if last_test_results.get("passed", False):
                last_test_status = "Tests passed."
            else:
                last_test_status = (
                    f"Tests failed. Failed tests: {', '.join(failed_tests[:5]) or 'unknown'}. "
                    f"{failure_summary}".strip()
                )
        last_git_diff = (workspace.last_git_diff if workspace is not None else "") or "No git diff recorded."
        last_git_diff = last_git_diff[:280]
        return "\n".join(
            [
                f"Active Project: {project_context.name}",
                f"Open Files: {open_files}",
                f"Recent Edits:\n{recent_edits}",
                f"Last Terminal Command: {last_terminal_command or 'None'}",
                f"Last Test Result: {last_test_status}",
                f"Last Git Diff: {last_git_diff}",
            ]
        )

    def _project_intelligence(self, project_context: ProjectContext) -> str:
        languages = ", ".join(
            f"{name} ({count})" for name, count in sorted(project_context.languages.items(), key=lambda item: (-item[1], item[0]))
        ) or "Unknown"
        important_files = ", ".join(project_context.important_files[: self.MAX_IMPORTANT_FILES]) or "None"
        recent_files = ", ".join(project_context.recent_files[: self.MAX_IMPORTANT_FILES]) or "None"
        recent_changes = "\n".join(
            f"- {item.get('file', 'unknown')}: {item.get('type', 'change')} {item.get('summary', '')}".strip()
            for item in project_context.recent_changes[:5]
        ) or "- None recorded"

        profile = project_context.project_profile
        if profile is None:
            profile_text = "No persistent project profile recorded."
        else:
            profile_text = (
                f"Description: {profile.description}\n"
                f"Primary Language: {profile.primary_language}\n"
                f"Frameworks: {', '.join(profile.frameworks) or 'None'}\n"
                f"Architecture: {profile.architecture or 'Unknown'}\n"
                f"Key Modules: {', '.join(profile.key_modules[:8]) or 'None'}"
            )

        return "\n".join(
            [
                f"Summary: {project_context.summary}",
                f"Languages: {languages}",
                f"File Count: {project_context.file_count}",
                f"Important Files: {important_files}",
                f"Recent Files: {recent_files}",
                f"Recent Changes:\n{recent_changes}",
                f"Project Profile:\n{profile_text}",
            ]
        )

    def _architecture_map(self, project_context: ProjectContext) -> str:
        project_map = project_context.project_map
        if project_map is None:
            project_map_text = "No indexed project map is available."
        else:
            project_map_text = (
                f"Overview: {project_map.overview}\n"
                f"Main Modules: {', '.join(project_map.main_modules[:10]) or 'None'}\n"
                f"Entry Points: {', '.join(project_map.entry_points[:10]) or 'None'}\n"
                f"Key Files: {', '.join(project_map.key_files[:10]) or 'None'}\n"
                f"Dependencies: {', '.join(project_map.dependencies[:15]) or 'None'}"
            )
        architecture_notes = "\n".join(f"- {item}" for item in project_context.architecture_notes[:5]) or "- None recorded"
        graph_insights = "\n".join(f"- {item}" for item in project_context.graph_insights[: self.MAX_GRAPH_INSIGHTS]) or "- None found"
        return "\n".join(
            [
                project_map_text,
                f"Architecture Notes:\n{architecture_notes}",
                f"Knowledge Graph Insights:\n{graph_insights}",
            ]
        )

    def _relevant_files(self, project_context: ProjectContext) -> str:
        entries: list[str] = []
        for file_entry in project_context.relevant_files[: self.MAX_RELEVANT_FILES]:
            entries.append(
                (
                    f"- {file_entry.file_path} [{file_entry.language}] "
                    f"Purpose: {file_entry.purpose} "
                    f"Summary: {file_entry.summary} "
                    f"Symbols: {', '.join(file_entry.symbols[:6]) or 'none'} "
                    f"Dependencies: {', '.join(file_entry.dependencies[:6]) or 'none'}"
                )
            )
        if not entries:
            for item in project_context.semantic_results[: self.MAX_RELEVANT_FILES]:
                metadata = item.get("metadata") or {}
                file_path = metadata.get("file_path", item.get("document_id", "unknown")) if isinstance(metadata, dict) else "unknown"
                entries.append(
                    f"- {file_path}: score={float(item.get('score', 0.0)):.3f} preview={str(item.get('text', ''))[:self.MAX_SNIPPET_LENGTH]}"
                )
        if not entries:
            for summary in project_context.code_summaries[: self.MAX_RELEVANT_FILES]:
                entries.append(f"- {summary.file_path}: {summary.summary}")
        return "\n".join(entries) or "- None found for this task."

    def _solution_matches(self, project_context: ProjectContext) -> str:
        solution_lines = [
            (
                f"- {entry.title}: {entry.description[:self.MAX_SNIPPET_LENGTH]} "
                f"Tags: {', '.join(entry.tags[:6]) or 'none'} "
                f"Projects: {', '.join(entry.projects[:4]) or 'none'}"
            )
            for entry in project_context.relevant_solutions[: self.MAX_SOLUTIONS]
        ]
        if not solution_lines:
            solution_lines = ["- No verified reusable solutions found."]

        similar_task_lines = [
            (
                f"- [{item.get('project_name', 'unknown')}] {item.get('task', '')} "
                f"status={item.get('status', '')} score={float(item.get('score', 0.0)):.3f} "
                f"result={str(item.get('final_result', ''))[:140]}"
            )
            for item in project_context.similar_tasks[: self.MAX_SIMILAR_TASKS]
        ] or ["- No similar tasks found."]

        return "\n".join(
            [
                "Solutions:",
                *solution_lines,
                "Similar Tasks:",
                *similar_task_lines,
            ]
        )

    def _style_profile(self, project_context: ProjectContext) -> str:
        style = project_context.style_profile
        if style is None:
            return "No coding style profile recorded."
        return "\n".join(
            [
                f"Indentation: {style.indentation}",
                f"Naming: {', '.join(style.naming_conventions) or 'Mixed'}",
                f"Structure: {style.code_structure or 'Unknown'}",
                f"Tests: {style.test_style or 'Unknown'}",
                f"Error Handling: {style.error_handling_style or 'Unknown'}",
                f"Notes: {'; '.join(style.notes[:4]) or 'None'}",
            ]
        )

    def _tool_contracts(self, tools: list[ToolDefinition]) -> str:
        rules = [
            "Use tools deliberately. Inspect code before changing it.",
            "Treat file-writing tools as scoped mutations. Do not rewrite unrelated files.",
            "Use terminal and test tools to validate work before declaring completion.",
            "Only use commit-related tooling after validation passes and the task contract is satisfied.",
        ]
        tool_lines = []
        for tool in tools[: self.MAX_TOOL_DEFINITIONS]:
            tool_lines.append(f"- {tool.name}: {tool.description[: self.MAX_TOOL_DESCRIPTION]}")
        if not tool_lines:
            tool_lines.append("- No tools were provided for this run.")
        return "\n".join(
            [
                *[f"- {rule}" for rule in rules],
                "Available Tools:",
                *tool_lines,
            ]
        )

    def _render_value(self, value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "None"
        if isinstance(value, dict):
            return "; ".join(f"{key}={self._render_value(item)}" for key, item in value.items()) or "None"
        return str(value)
