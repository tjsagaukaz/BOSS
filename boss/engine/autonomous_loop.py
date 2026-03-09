from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from boss.agents.architect_agent import ArchitectAgent
from boss.agents.auditor_agent import AuditorAgent
from boss.agents.engineer_agent import EngineerAgent
from boss.context.editor_state import EditorStateStore
from boss.context.project_indexer import ProjectIndexer
from boss.context.project_loader import ProjectLoader
from boss.dashboard.task_dashboard import TaskDashboard
from boss.ide.vscode_controller import VSCodeController
from boss.memory.task_history import TaskHistoryStore
from boss.plugins.plugin_manager import PluginManager
from boss.reliability import classify_failure_map
from boss.runtime import PlanningSpine, RunGraphExecutor, RunNode, StepRunner, spine_to_run_graph
from boss.tools import Toolbox
from boss.types import AgentResult, AutonomousBuildResult, PlanStepContract, ProjectContext, StepExecutionResult, StructuredPlan, ToolExecutionRecord
from boss.workspace import EditorListener, GitListener, TerminalListener, TestListener


class StopRequestedError(RuntimeError):
    pass


class AutonomousDevelopmentLoop:
    ENGINEER_TIMEOUT_SECONDS = 120
    ENGINEER_MAX_TOOL_ROUNDS = 8
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

    def __init__(
        self,
        root_dir: str | Path,
        project_loader: ProjectLoader,
        project_indexer: ProjectIndexer,
        architect: ArchitectAgent,
        engineer: EngineerAgent,
        auditor: AuditorAgent,
        embeddings,
        task_history: TaskHistoryStore,
        editor_state: EditorStateStore,
        plugin_manager: PluginManager,
        vscode_controller: VSCodeController,
        dashboard: TaskDashboard,
        console: Console | None = None,
        editor_listener: EditorListener | None = None,
        terminal_listener: TerminalListener | None = None,
        test_listener: TestListener | None = None,
        git_listener: GitListener | None = None,
        runtime_timeouts: dict[str, int] | None = None,
        full_access: bool = False,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.project_loader = project_loader
        self.project_indexer = project_indexer
        self.architect = architect
        self.engineer = engineer
        self.auditor = auditor
        self.embeddings = embeddings
        self.task_history = task_history
        self.editor_state = editor_state
        self.plugin_manager = plugin_manager
        self.vscode_controller = vscode_controller
        self.dashboard = dashboard
        self.console = console or Console()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.editor_listener = editor_listener
        self.terminal_listener = terminal_listener
        self.test_listener = test_listener
        self.git_listener = git_listener
        self.full_access = full_access
        self.runtime_timeouts = {
            "engineer_step": int((runtime_timeouts or {}).get("engineer_step", self.ENGINEER_TIMEOUT_SECONDS)),
            "test_step": int((runtime_timeouts or {}).get("test_step", 60)),
            "audit_step": int((runtime_timeouts or {}).get("audit_step", 45)),
        }
        self.step_runner = StepRunner(
            self.root_dir,
            default_timeout_seconds=self.runtime_timeouts["engineer_step"],
        )

    def run(
        self,
        project_name: str,
        task: str,
        auto_approve: bool = False,
        max_iterations: int = 10,
        commit_changes: bool = True,
        deep: bool = False,
        benchmark_mode: bool = False,
        current_task_callback: Callable[[int | None], None] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AutonomousBuildResult:
        started = time.perf_counter()
        task_id = self.task_history.create_task(project_name=project_name, task=task)
        if current_task_callback:
            current_task_callback(task_id)
        self._emit_timeline(
            event_callback,
            title="Autonomous task created",
            status="running",
            agent="architect",
            project_name=project_name,
            task_id=task_id,
            message=task,
        )

        step_results: list[StepExecutionResult] = []
        all_changed_files: set[str] = set()
        errors: list[str] = []
        plan = StructuredPlan(goal=task, steps=[], contracts=[], raw_text="")
        spine = PlanningSpine.from_plan(task_id=task_id, plan=plan)
        plan_result = None
        model_usage: list[dict[str, object]] = []
        project_context = self.project_loader.load_project(project_name, task_hint=task, auto_index=True)
        run_graph_payload: dict[str, object] = {"nodes": [], "completed": [], "failed": []}

        try:
            self._emit_activity(
                event_callback,
                agent="architect",
                status="planning",
                message=f"Generating execution plan for: {task}",
                project_name=project_name,
                task_id=task_id,
            )
            spine, plan_result = self._generate_plan(task_id=task_id, task=task, project_context=project_context)
            plan = spine.to_structured_plan()
            model_usage.append(self._agent_usage_payload("architect", plan_result))
            self._emit_activity(
                event_callback,
                agent="architect",
                status="completed",
                message=f"Plan created with {len(plan.steps)} step(s).",
                project_name=project_name,
                task_id=task_id,
                metadata={"steps": plan.steps},
            )
            self._emit_timeline(
                event_callback,
                title="Architect plan completed",
                status="completed",
                agent="architect",
                project_name=project_name,
                task_id=task_id,
                message=plan.goal,
                metadata={"steps": plan.steps},
            )
            run_graph = spine_to_run_graph(spine, retry_budget=0)
            run_graph_payload = run_graph.as_payload()
            self.task_history.set_plan(
                task_id,
                {
                    "goal": plan.goal,
                    "steps": plan.steps,
                    "contracts": [self._contract_payload(item) for item in plan.contracts],
                    "spine": spine.as_plan_payload(),
                    "run_graph": run_graph_payload,
                    "raw_text": plan.raw_text,
                },
            )
            with Live(self.dashboard.render_live(project_name, plan, step_results), console=self.console, refresh_per_second=4) as live:
                step_results, run_graph_payload, terminal_result = self._execute_run_graph(
                    task_id=task_id,
                    project_name=project_name,
                    goal=plan.goal,
                    plan=plan,
                    spine=spine,
                    auto_approve=auto_approve,
                    max_iterations=max_iterations,
                    commit_changes=commit_changes,
                    deep=deep,
                    benchmark_mode=benchmark_mode,
                    live=live,
                    event_callback=event_callback,
                )
                for step_result in step_results:
                    all_changed_files.update(step_result.changed_files)
                    model_usage.extend(step_result.model_usage)
                    errors.extend(step_result.errors)
                if terminal_result is not None:
                    final_result = f"Stopped on step {terminal_result.step_index + 1}: {terminal_result.step_title}"
                    runtime_seconds = time.perf_counter() - started
                    metadata = self._task_metadata(plan_result, step_results, run_graph_payload)
                    self.task_history.finalize_task(
                        task_id,
                        status=terminal_result.status,
                        files_changed=sorted(all_changed_files),
                        errors=errors,
                        final_result=final_result,
                        runtime_seconds=runtime_seconds,
                        model_usage=model_usage,
                        token_usage=self._aggregate_usage(model_usage),
                        estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                        metadata=metadata,
                    )
                    return AutonomousBuildResult(
                        task_id=task_id,
                        project_name=project_name,
                        goal=plan.goal,
                        status=terminal_result.status,
                        plan=plan,
                        runtime_seconds=runtime_seconds,
                        step_results=step_results,
                        final_result=final_result,
                        changed_files=sorted(all_changed_files),
                        errors=errors,
                        model_usage=model_usage,
                        token_usage=self._aggregate_usage(model_usage),
                        estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                        metadata=metadata,
                    )

            final_result = f"Completed {len(plan.steps)} step(s) for {plan.goal}."
            runtime_seconds = time.perf_counter() - started
            self._emit_activity(
                event_callback,
                agent="architect",
                status="idle",
                message="Idle",
                project_name=project_name,
                task_id=task_id,
            )
            self._emit_activity(
                event_callback,
                agent="engineer",
                status="idle",
                message="Idle",
                project_name=project_name,
                task_id=task_id,
            )
            self._emit_activity(
                event_callback,
                agent="test",
                status="idle",
                message="Idle",
                project_name=project_name,
                task_id=task_id,
            )
            self._emit_activity(
                event_callback,
                agent="auditor",
                status="idle",
                message="Idle",
                project_name=project_name,
                task_id=task_id,
            )
            self._emit_timeline(
                event_callback,
                title="Autonomous build completed",
                status="completed",
                agent="engineer",
                project_name=project_name,
                task_id=task_id,
                message=final_result,
            )
            self.task_history.finalize_task(
                task_id,
                status="completed",
                files_changed=sorted(all_changed_files),
                errors=[],
                final_result=final_result,
                runtime_seconds=runtime_seconds,
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
            return AutonomousBuildResult(
                task_id=task_id,
                project_name=project_name,
                goal=plan.goal,
                status="completed",
                plan=plan,
                runtime_seconds=runtime_seconds,
                step_results=step_results,
                final_result=final_result,
                changed_files=sorted(all_changed_files),
                errors=[],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
        except KeyboardInterrupt:
            self.task_history.request_stop(task_id)
            final_result = "Build interrupted by user."
            runtime_seconds = time.perf_counter() - started
            self._emit_timeline(
                event_callback,
                title="Autonomous build interrupted",
                status="stopped",
                agent="engineer",
                project_name=project_name,
                task_id=task_id,
                message=final_result,
            )
            self.task_history.finalize_task(
                task_id,
                status="stopped",
                files_changed=sorted(all_changed_files),
                errors=[final_result],
                final_result=final_result,
                runtime_seconds=runtime_seconds,
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
            return AutonomousBuildResult(
                task_id=task_id,
                project_name=project_name,
                goal=plan.goal or task,
                status="stopped",
                plan=plan,
                runtime_seconds=runtime_seconds,
                step_results=step_results,
                final_result=final_result,
                changed_files=sorted(all_changed_files),
                errors=[final_result],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
        except StopRequestedError as exc:
            final_result = str(exc)
            runtime_seconds = time.perf_counter() - started
            self._emit_timeline(
                event_callback,
                title="Autonomous build stopped",
                status="stopped",
                agent="engineer",
                project_name=project_name,
                task_id=task_id,
                message=final_result,
            )
            self.task_history.finalize_task(
                task_id,
                status="stopped",
                files_changed=sorted(all_changed_files),
                errors=[final_result],
                final_result=final_result,
                runtime_seconds=runtime_seconds,
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
            return AutonomousBuildResult(
                task_id=task_id,
                project_name=project_name,
                goal=plan.goal or task,
                status="stopped",
                plan=plan,
                runtime_seconds=runtime_seconds,
                step_results=step_results,
                final_result=final_result,
                changed_files=sorted(all_changed_files),
                errors=[final_result],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
        except Exception as exc:
            final_result = f"Build failed: {exc}"
            runtime_seconds = time.perf_counter() - started
            self._emit_activity(
                event_callback,
                agent="architect",
                status="failed",
                message=str(exc),
                project_name=project_name,
                task_id=task_id,
            )
            self._emit_timeline(
                event_callback,
                title="Autonomous build failed",
                status="failed",
                agent="architect",
                project_name=project_name,
                task_id=task_id,
                message=str(exc),
            )
            self.task_history.finalize_task(
                task_id,
                status="failed",
                files_changed=sorted(all_changed_files),
                errors=[str(exc)],
                final_result=final_result,
                runtime_seconds=runtime_seconds,
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
            return AutonomousBuildResult(
                task_id=task_id,
                project_name=project_name,
                goal=plan.goal or task,
                status="failed",
                plan=plan,
                runtime_seconds=runtime_seconds,
                step_results=step_results,
                final_result=final_result,
                changed_files=sorted(all_changed_files),
                errors=[str(exc)],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                metadata=self._task_metadata(plan_result, step_results, run_graph_payload),
            )
        finally:
            if current_task_callback:
                current_task_callback(None)

    def _execute_run_graph(
        self,
        *,
        task_id: int,
        project_name: str,
        goal: str,
        plan: StructuredPlan,
        spine: PlanningSpine,
        auto_approve: bool,
        max_iterations: int,
        commit_changes: bool,
        deep: bool,
        benchmark_mode: bool,
        live: Live,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[StepExecutionResult], dict[str, object], StepExecutionResult | None]:
        graph = spine_to_run_graph(spine, retry_budget=0)
        live_lock = threading.RLock()
        event_lock = threading.RLock()

        def safe_event_callback(payload: dict[str, Any]) -> None:
            with event_lock:
                if event_callback is not None:
                    event_callback(payload)

        def node_runner(node: RunNode, completed_steps: list[StepExecutionResult]) -> StepExecutionResult:
            self._raise_if_stop_requested(task_id)
            step_contract = self._step_contract(plan, node.step_index)
            step_title = plan.steps[node.step_index]
            self.task_history.start_step(task_id, node.step_index, step_title)
            result = self._execute_step(
                task_id=task_id,
                project_name=project_name,
                goal=goal,
                plan=plan,
                spine=spine,
                step_index=node.step_index,
                step_title=step_title,
                step_contract=step_contract,
                auto_approve=auto_approve,
                max_iterations=max_iterations,
                commit_changes=commit_changes,
                deep=deep,
                benchmark_mode=benchmark_mode,
                completed_steps=completed_steps,
                live=live,
                live_lock=live_lock,
                event_callback=safe_event_callback,
                run_node=node,
            )
            result.metadata = {
                **result.metadata,
                "run_graph_node_id": node.id,
                "run_graph_dependencies": list(node.dependencies),
                "run_graph_agent_role": node.agent_role,
            }
            return result

        executor = RunGraphExecutor(
            graph=graph,
            node_runner=node_runner,
            parallel=self._run_graph_parallel_enabled(),
            max_workers=max(1, min(4, len(graph.nodes))),
        )
        executor.run()
        results = executor.ordered_results()
        self._safe_live_update(live, live_lock, self.dashboard.render_live(project_name, plan, results))
        terminal_result = next((item for item in results if item.status != "completed"), None)
        return results, graph.as_payload(), terminal_result

    def _execute_step(
        self,
        task_id: int,
        project_name: str,
        goal: str,
        plan: StructuredPlan,
        spine: PlanningSpine,
        step_index: int,
        step_title: str,
        step_contract: PlanStepContract,
        auto_approve: bool,
        max_iterations: int,
        commit_changes: bool,
        deep: bool,
        benchmark_mode: bool,
        completed_steps: list[StepExecutionResult],
        live: Live,
        live_lock: threading.RLock | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        run_node: RunNode | None = None,
    ) -> StepExecutionResult:
        started = time.perf_counter()
        feedback = ""
        step_result = StepExecutionResult(
            step_index=step_index,
            step_title=step_title,
            status="running",
            iterations=0,
        )

        for iteration in range(1, max_iterations + 1):
            try:
                self._raise_if_stop_requested(task_id)
                spine.set_current_step(step_index)
                spine.mark_attempt(step_index)
                self._emit_activity(
                    event_callback,
                    agent="engineer",
                    status="running",
                    message=f"Step {step_index + 1}/{len(plan.steps)}: {step_title} (attempt {iteration})",
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Engineer step started" if iteration == 1 else "Engineer step retry started",
                    status="running",
                    agent="engineer",
                    project_name=project_name,
                    task_id=task_id,
                    message=step_title,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._safe_live_update(
                    live,
                    live_lock,
                    self.dashboard.render_live(
                        project_name,
                        plan,
                        completed_steps + [step_result],
                        active_message=f"Step {step_index + 1}/{len(plan.steps)} Engineer: writing code (attempt {iteration})",
                    )
                )
                project_context = self.project_loader.load_project(
                    project_name,
                    task_hint=(
                        f"{goal}\n{step_title}\n"
                        f"{self._serialize_contract(step_contract)}\n"
                        f"{self._serialize_spine(spine, step_index)}\n"
                        f"{feedback}"
                    ),
                    auto_index=False,
                )
                toolbox = self._toolbox(project_context, auto_approve=auto_approve)
                engineer_tools = toolbox.build_tool_definitions(
                    allow_write=True,
                    allow_terminal=True,
                    allow_commit=False,
                    allow_tests=True,
                )
                engineer_result = self._run_engineer_step_subprocess(
                    project_name=project_name,
                    goal=goal,
                    plan=plan,
                    spine=spine,
                    step_index=step_index,
                    step_contract=step_contract,
                    feedback=feedback,
                    auto_approve=auto_approve,
                    deep=deep,
                    benchmark_mode=benchmark_mode,
                    iteration=iteration,
                )
                changed_files = self._normalize_changed_files(project_context.root, engineer_result.tool_records)
                for path in changed_files:
                    step_result.changed_files.append(path)
                    self.editor_state.record_change(
                        project_name,
                        path,
                        change_type="agent_write",
                        summary=f"Updated during step: {step_title}",
                    )

                self._log_tool_records(engineer_result.tool_records)
                self.project_indexer.index_project(project_name=project_name, force=False)
                refreshed_context = self.project_loader.load_project(
                    project_name,
                    task_hint=(
                        f"{goal}\n{step_title}\n"
                        f"{self._serialize_contract(step_contract)}\n"
                        f"{self._serialize_spine(spine, step_index)}"
                    ),
                    auto_index=False,
                )
                local_validation_errors = spine.validate_step_outputs(
                    refreshed_context.root,
                    changed_files=changed_files,
                    step_index=step_index,
                )
                self._emit_activity(
                    event_callback,
                    agent="engineer",
                    status="completed",
                    message=f"Step {step_index + 1}: updated {len(changed_files)} file(s).",
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "changed_files": changed_files},
                )

                self._safe_live_update(
                    live,
                    live_lock,
                    self.dashboard.render_live(
                        project_name,
                        plan,
                        completed_steps + [step_result],
                        active_message=f"Step {step_index + 1}/{len(plan.steps)} Tests: running",
                    )
                )
                self._emit_activity(
                    event_callback,
                    agent="test",
                    status="running",
                    message=f"Running tests for step {step_index + 1}",
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Test run started",
                    status="running",
                    agent="test",
                    project_name=project_name,
                    task_id=task_id,
                    message=step_title,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                test_result = toolbox.terminal_tools.run_tests()
                self.logger.info("Task %s step %s tests: %s", task_id, step_index, json.dumps(test_result))
                self._emit_activity(
                    event_callback,
                    agent="test",
                    status="completed" if bool(test_result.get("passed", False)) else "failed",
                    message="Tests passed." if bool(test_result.get("passed", False)) else self._format_test_summary(test_result),
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Test run completed" if bool(test_result.get("passed", False)) else "Test run failed",
                    status="completed" if bool(test_result.get("passed", False)) else "failed",
                    agent="test",
                    project_name=project_name,
                    task_id=task_id,
                    message=self._format_test_summary(test_result),
                    metadata={"step_index": step_index, "attempt": iteration},
                )

                self._safe_live_update(
                    live,
                    live_lock,
                    self.dashboard.render_live(
                        project_name,
                        plan,
                        completed_steps + [step_result],
                        active_message=f"Step {step_index + 1}/{len(plan.steps)} Auditor: reviewing",
                    )
                )
                self._emit_activity(
                    event_callback,
                    agent="auditor",
                    status="reviewing",
                    message=f"Reviewing step {step_index + 1}: {step_title}",
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Auditor review started",
                    status="running",
                    agent="auditor",
                    project_name=project_name,
                    task_id=task_id,
                    message=step_title,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                audit_tools = self._toolbox(refreshed_context, auto_approve=True).build_tool_definitions(
                    allow_write=False,
                    allow_terminal=True,
                    allow_commit=False,
                    allow_tests=True,
                )
                audit_result = self.auditor.audit(
                    task=f"Review the implementation of step: {step_title}",
                    project_context=refreshed_context,
                    plan_text=self._serialize_plan(plan),
                    implementation_text=engineer_result.text,
                    changed_files=changed_files,
                    tools=audit_tools,
                    test_results=self._format_test_summary(test_result),
                    task_contract=self._auditor_task_contract(step_contract=step_contract, changed_files=changed_files),
                    execution_rules=self._step_execution_rules(step_contract),
                    execution_spine=spine.execution_payload(step_index),
                )
                self._emit_activity(
                    event_callback,
                    agent="auditor",
                    status="completed" if audit_result.passed else "failed",
                    message="Audit passed." if audit_result.passed else "Audit requested fixes.",
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Auditor review completed" if audit_result.passed else "Auditor requested fixes",
                    status="completed" if audit_result.passed else "failed",
                    agent="auditor",
                    project_name=project_name,
                    task_id=task_id,
                    message=audit_result.text[:240],
                    metadata={"step_index": step_index, "attempt": iteration},
                )
            except StopRequestedError:
                raise
            except Exception as exc:
                spine.mark_failed(step_index, [str(exc)])
                step_result.status = "failed"
                step_result.iterations = iteration
                step_result.runtime_seconds = time.perf_counter() - started
                step_result.errors = [str(exc)]
                step_result.metadata = {
                    **step_result.metadata,
                    "failure_category": self._classify_step_exception(str(exc)),
                    "run_graph_node_id": run_node.id if run_node is not None else step_contract.step_id,
                    "run_graph_dependencies": list(run_node.dependencies) if run_node is not None else list(step_contract.dependencies),
                    "run_graph_agent_role": (run_node.agent_role if run_node is not None else step_contract.agent_role),
                    "failure_map": classify_failure_map(
                        failure_category=self._classify_step_exception(str(exc)),
                        errors=step_result.errors,
                        tool_errors=step_result.tool_errors,
                        changed_files=step_result.changed_files,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        metadata=step_result.metadata,
                    ),
                }
                step_result.metadata["failure_map_primary"] = (
                    step_result.metadata["failure_map"][0] if step_result.metadata["failure_map"] else None
                )
                self.task_history.fail_step(
                    task_id=task_id,
                    step_index=step_index,
                    errors=step_result.errors,
                    iterations=iteration,
                    engineer_output=step_result.engineer_result,
                    test_output=step_result.test_result,
                    audit_output=step_result.audit_result,
                    runtime_seconds=step_result.runtime_seconds,
                    model_usage=step_result.model_usage,
                    token_usage=step_result.token_usage,
                    estimated_cost_usd=step_result.estimated_cost_usd,
                    tool_errors=step_result.tool_errors,
                    metadata=step_result.metadata,
                )
                self._emit_activity(
                    event_callback,
                    agent="engineer",
                    status="failed",
                    message=str(exc),
                    project_name=project_name,
                    task_id=task_id,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._emit_timeline(
                    event_callback,
                    title="Step failed",
                    status="failed",
                    agent="engineer",
                    project_name=project_name,
                    task_id=task_id,
                    message=str(exc),
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._safe_live_update(live, live_lock, self.dashboard.render_live(project_name, plan, completed_steps + [step_result]))
                return step_result

            step_result.iterations = iteration
            step_result.runtime_seconds = time.perf_counter() - started
            step_result.engineer_result = engineer_result.text
            step_result.test_result = test_result
            step_result.audit_result = audit_result.text
            step_result.changed_files = sorted(set(step_result.changed_files))
            step_result.model_usage = [
                self._agent_usage_payload("engineer", engineer_result),
                self._audit_usage_payload("auditor", audit_result),
            ]
            step_result.token_usage = self._aggregate_usage(step_result.model_usage)
            step_result.estimated_cost_usd = self._sum_cost(
                item.get("estimated_cost_usd") for item in step_result.model_usage
            )
            step_result.tool_errors = self._tool_errors(engineer_result.tool_records) + self._tool_errors(
                audit_result.tool_records
            )
            step_result.metadata = {
                "tests_found": bool(test_result.get("found", False)),
                "tests_passed": bool(test_result.get("passed", False)),
                "engineer_model": engineer_result.model,
                "auditor_model": audit_result.model,
                "step_contract": self._contract_payload(step_contract),
                "spine_step": spine.step_payload(step_index),
                "local_validation_errors": local_validation_errors,
                "run_graph_node_id": run_node.id if run_node is not None else step_contract.step_id,
                "run_graph_dependencies": list(run_node.dependencies) if run_node is not None else list(step_contract.dependencies),
                "run_graph_agent_role": (run_node.agent_role if run_node is not None else step_contract.agent_role),
            }

            errors: list[str] = []
            if local_validation_errors:
                errors.extend(local_validation_errors)
            if not bool(test_result.get("passed", False)):
                errors.append(self._format_test_summary(test_result))
            if not audit_result.passed:
                errors.append(audit_result.text)
            if step_result.tool_errors:
                errors.extend(step_result.tool_errors)

            failure_category = self._step_failure_category(
                local_validation_errors=local_validation_errors,
                test_result=test_result,
                audit_passed=audit_result.passed,
                tool_errors=step_result.tool_errors,
                changed_files=step_result.changed_files,
            )
            failure_map = classify_failure_map(
                failure_category=failure_category,
                errors=errors,
                tool_errors=step_result.tool_errors,
                changed_files=step_result.changed_files,
                tests_passed=bool(test_result.get("passed", False)) if test_result.get("found", False) else None,
                audit_passed=audit_result.passed,
                iteration=iteration,
                max_iterations=max_iterations,
                metadata=step_result.metadata,
            )
            step_result.metadata["failure_category"] = failure_category
            step_result.metadata["failure_map"] = failure_map
            step_result.metadata["failure_map_primary"] = failure_map[0] if failure_map else None

            self.task_history.record_step_attempt(
                task_id=task_id,
                step_index=step_index,
                engineer_output=engineer_result.text,
                test_output=test_result,
                audit_output=audit_result.text,
                files_changed=step_result.changed_files,
                errors=errors,
                iterations=iteration,
                runtime_seconds=step_result.runtime_seconds,
                model_usage=step_result.model_usage,
                token_usage=step_result.token_usage,
                estimated_cost_usd=step_result.estimated_cost_usd,
                tool_errors=step_result.tool_errors,
                metadata=step_result.metadata,
            )

            if not errors:
                spine.mark_completed(step_index)
                step_result.metadata["failure_category"] = None
                step_result.metadata["failure_map"] = []
                step_result.metadata["failure_map_primary"] = None
                commit_message = f"Implement {step_title}"
                if commit_changes:
                    if auto_approve:
                        try:
                            commit_result = toolbox.git_tools.git_commit(commit_message)
                            if isinstance(commit_result, dict) and commit_result.get("committed"):
                                step_result.commit_message = commit_message
                                step_result.metadata["commit_gate"] = {
                                    "status": "committed",
                                    "message": commit_message,
                                    "commit": str(commit_result.get("commit", "")),
                                }
                            else:
                                message = str(commit_result.get("message", "Commit skipped.")) if isinstance(commit_result, dict) else "Commit skipped."
                                self.logger.info("Commit skipped for step %s: %s", step_index, message)
                                step_result.commit_message = message
                                step_result.metadata["commit_gate"] = {
                                    "status": "skipped",
                                    "message": message,
                                }
                        except Exception as exc:
                            warning = f"Commit skipped: {exc}"
                            self.logger.warning(warning)
                            step_result.commit_message = warning
                            step_result.metadata["commit_gate"] = {
                                "status": "failed",
                                "message": warning,
                            }
                    else:
                        step_result.commit_message = commit_message
                        step_result.metadata["commit_gate"] = {
                            "status": "pending",
                            "message": commit_message,
                        }
                step_result.status = "completed"
                self.task_history.complete_step(
                    task_id=task_id,
                    step_index=step_index,
                    files_changed=step_result.changed_files,
                    commit_message=step_result.commit_message or "",
                    iterations=iteration,
                    engineer_output=engineer_result.text,
                    test_output=test_result,
                    audit_output=audit_result.text,
                    runtime_seconds=step_result.runtime_seconds,
                    model_usage=step_result.model_usage,
                    token_usage=step_result.token_usage,
                    estimated_cost_usd=step_result.estimated_cost_usd,
                    tool_errors=step_result.tool_errors,
                    metadata=step_result.metadata,
                )
                self._emit_timeline(
                    event_callback,
                    title="Step completed",
                    status="completed",
                    agent="engineer",
                    project_name=project_name,
                    task_id=task_id,
                    message=step_title,
                    metadata={"step_index": step_index, "attempt": iteration},
                )
                self._safe_live_update(live, live_lock, self.dashboard.render_live(project_name, plan, completed_steps + [step_result]))
                return step_result

            feedback = "\n\n".join(errors)
            spine.mark_failed(step_index, errors)
            step_result.errors = errors
            self.logger.info("Task %s step %s attempt %s failed", task_id, step_index, iteration)
            self._emit_timeline(
                event_callback,
                title="Step retry required",
                status="retrying",
                agent="auditor",
                project_name=project_name,
                task_id=task_id,
                message=feedback[:240],
                metadata={"step_index": step_index, "attempt": iteration},
            )

        step_result.status = "failed"
        step_result.runtime_seconds = time.perf_counter() - started
        failure_category = self._step_failure_category(
            local_validation_errors=step_result.metadata.get("local_validation_errors", []),
            test_result=step_result.test_result,
            audit_passed=False if step_result.audit_result else None,
            tool_errors=step_result.tool_errors,
            changed_files=step_result.changed_files,
        )
        failure_map = classify_failure_map(
            failure_category=failure_category,
            errors=step_result.errors or [f"Exceeded max iterations ({max_iterations})."],
            tool_errors=step_result.tool_errors,
            changed_files=step_result.changed_files,
            tests_passed=bool(step_result.test_result.get("passed", False)) if step_result.test_result else None,
            audit_passed=None,
            iteration=max_iterations,
            max_iterations=max_iterations,
            metadata=step_result.metadata,
        )
        step_result.metadata["failure_category"] = failure_category
        step_result.metadata["failure_map"] = failure_map
        step_result.metadata["failure_map_primary"] = failure_map[0] if failure_map else None
        self.task_history.fail_step(
            task_id=task_id,
            step_index=step_index,
            errors=step_result.errors or [f"Exceeded max iterations ({max_iterations})."],
            iterations=max_iterations,
            engineer_output=step_result.engineer_result,
            test_output=step_result.test_result,
            audit_output=step_result.audit_result,
            runtime_seconds=step_result.runtime_seconds,
            model_usage=step_result.model_usage,
            token_usage=step_result.token_usage,
            estimated_cost_usd=step_result.estimated_cost_usd,
            tool_errors=step_result.tool_errors,
            metadata=step_result.metadata,
        )
        if not step_result.errors:
            step_result.errors = [f"Exceeded max iterations ({max_iterations})."]
        self._emit_activity(
            event_callback,
            agent="engineer",
            status="failed",
            message=f"Exceeded max iterations for step {step_index + 1}.",
            project_name=project_name,
            task_id=task_id,
            metadata={"step_index": step_index, "attempt": max_iterations},
        )
        self._emit_timeline(
            event_callback,
            title="Step failed after max iterations",
            status="failed",
            agent="engineer",
            project_name=project_name,
            task_id=task_id,
            message=step_title,
            metadata={"step_index": step_index, "attempt": max_iterations},
        )
        self._safe_live_update(live, live_lock, self.dashboard.render_live(project_name, plan, completed_steps + [step_result]))
        return step_result

    def _generate_plan(self, task_id: int, task: str, project_context: ProjectContext) -> tuple[PlanningSpine, object]:
        tools = self._toolbox(project_context, auto_approve=True).build_tool_definitions(
            allow_write=False,
            allow_terminal=False,
            allow_commit=False,
            allow_tests=False,
        )
        prompt = (
            f"{task}\n\n"
            "Return a strict JSON object with keys 'goal' and 'steps'. "
            "The 'steps' value must be an array of 3-8 step objects. "
            "Each step object must contain: "
            "'id', 'title', 'objective', 'allowed_paths', 'expected_outputs', "
            "'required_artifacts', 'validation', 'done_when', and 'validation_commands'. "
            "Each step object may also include optional 'agent_role' and 'dependencies' keys. "
            "Use 'dependencies' only when ordering matters; independent steps should omit it. "
            "Use concise machine-checkable 'done_when' items when possible, for example: "
            "'file:src/auth.py exists', "
            "'file:src/auth.py not_empty', "
            "'file:src/auth.py contains create_access_token'. "
            "Use relative file paths in contracts. "
            "Do not include prose outside the JSON."
        )
        result = self.architect.plan(task=prompt, project_context=project_context, tools=tools)
        return self._build_planning_spine(task_id, task, result.text), result

    def _build_planning_spine(self, task_id: int | str, task: str, text: str) -> PlanningSpine:
        return PlanningSpine.from_text(task_id=task_id, fallback_goal=task, text=text)

    def _parse_structured_plan(self, task: str, text: str) -> StructuredPlan:
        return self._build_planning_spine("adhoc", task, text).to_structured_plan()

    def _extract_steps_from_text(self, text: str) -> list[str]:
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

    def _normalize_changed_files(self, project_root: Path, tool_records) -> list[str]:
        changed: list[str] = []
        for record in tool_records:
            if record.name not in {"write_file", "replace_code_block", "append_to_file"}:
                continue
            if not record.success or not isinstance(record.result, dict):
                continue
            path_value = record.result.get("path")
            if not path_value:
                continue
            path = Path(str(path_value))
            try:
                changed.append(str(path.relative_to(project_root)))
            except ValueError:
                changed.append(str(path))
        return sorted(set(changed))

    def _run_engineer_step_subprocess(
        self,
        *,
        project_name: str,
        goal: str,
        plan: StructuredPlan,
        spine: PlanningSpine,
        step_index: int,
        step_contract: PlanStepContract,
        feedback: str,
        auto_approve: bool,
        deep: bool,
        benchmark_mode: bool,
        iteration: int,
    ) -> AgentResult:
        payload = {
            "root_dir": str(self.root_dir),
            "project_name": project_name,
            "task_hint": (
                f"{goal}\n{step_contract.title}\n"
                f"{self._serialize_contract(step_contract)}\n"
                f"{self._serialize_spine(spine, step_index)}\n"
                f"{feedback}"
            ),
            "task": self._engineer_step_task(goal=goal, step_contract=step_contract),
            "plan_text": self._serialize_plan(plan),
            "audit_feedback": feedback,
            "task_contract": self._engineer_task_contract(goal=goal, step_contract=step_contract),
            "execution_rules": self._step_execution_rules(step_contract),
            "execution_spine": spine.execution_payload(step_index),
            "auto_approve": auto_approve,
            "auto_index": False,
            "allow_write": True,
            "allow_terminal": True,
            "allow_tests": True,
            "request_options": {
                "mode": "build",
                "deep": deep,
                "attempt": iteration,
                "complexity": self._contract_complexity(plan, step_contract),
                "step_index": step_index,
                "benchmark_mode": benchmark_mode,
                "timeout_seconds": self.runtime_timeouts["engineer_step"],
                "max_tool_rounds": self.ENGINEER_MAX_TOOL_ROUNDS,
            },
        }
        runner_result = self.step_runner.run_engineer_step(
            payload,
            timeout_seconds=self.runtime_timeouts["engineer_step"],
        )
        if runner_result.status != "completed":
            message = str(
                runner_result.payload.get("error")
                or runner_result.payload.get("traceback")
                or runner_result.stderr
                or "Engineer step subprocess failed."
            ).strip()
            raise RuntimeError(message)

        result_payload = runner_result.payload.get("result", {})
        tool_records = [self._deserialize_tool_record(item) for item in result_payload.get("tool_records", [])]
        return AgentResult(
            agent_name=str(result_payload.get("agent_name", "engineer")),
            provider=str(result_payload.get("provider", "unknown")),
            model=str(result_payload.get("model", "unknown")),
            text=str(result_payload.get("text", "")),
            duration_seconds=float(result_payload.get("duration_seconds", 0.0) or 0.0),
            usage=dict(result_payload.get("usage", {}) or {}),
            estimated_cost_usd=(
                float(result_payload["estimated_cost_usd"])
                if result_payload.get("estimated_cost_usd") is not None
                else None
            ),
            tool_records=tool_records,
        )

    def _deserialize_tool_record(self, payload: object) -> ToolExecutionRecord:
        if not isinstance(payload, dict):
            return ToolExecutionRecord(name="unknown", arguments={}, success=False, error="Invalid tool record payload.")
        return ToolExecutionRecord(
            name=str(payload.get("name", "unknown")),
            arguments=dict(payload.get("arguments", {}) or {}),
            success=bool(payload.get("success", False)),
            result=payload.get("result"),
            error=str(payload.get("error")) if payload.get("error") is not None else None,
            started_at=str(payload.get("started_at", "")),
        )

    def _log_tool_records(self, tool_records) -> None:
        for record in tool_records:
            payload = {
                "tool": record.name,
                "success": record.success,
                "arguments": record.arguments,
                "result": record.result,
                "error": record.error,
            }
            self.logger.info("Tool execution: %s", json.dumps(payload, default=str))

    def _toolbox(self, project_context: ProjectContext, auto_approve: bool) -> Toolbox:
        return Toolbox(
            workspace_root=self.root_dir,
            project_root=project_context.root,
            project_name=project_context.name,
            embeddings=self.embeddings,
            editor_state=self.editor_state,
            vscode_controller=self.vscode_controller,
            plugin_manager=self.plugin_manager,
            full_access=self.full_access,
            require_confirmation=False if self.full_access else not auto_approve,
            confirm_write=None if auto_approve else self._confirm_write,
            editor_listener=self.editor_listener,
            terminal_listener=self.terminal_listener,
            git_listener=self.git_listener,
            test_listener=self.test_listener,
        )

    def _confirm_write(self, path: Path, diff_preview: str, existed: bool) -> bool:
        title = f"Diff Preview: {path}"
        if diff_preview.strip():
            self.console.print(Panel(Syntax(diff_preview, "diff", word_wrap=False), title=title, expand=False))
        else:
            self.console.print(Panel("(No textual diff available)", title=title, expand=False))
        prompt = "Apply overwrite?" if existed else "Create file with these changes?"
        return Confirm.ask(prompt, default=False)

    def _serialize_plan(self, plan: StructuredPlan) -> str:
        return json.dumps(
            {
                "goal": plan.goal,
                "steps": [self._contract_payload(item) for item in plan.contracts] if plan.contracts else plan.steps,
            },
            indent=2,
        )

    def _serialize_spine(self, spine: PlanningSpine, step_index: int | None = None) -> str:
        payload = spine.current_step_payload() if step_index is None else spine.execution_payload(step_index)
        return json.dumps(payload, indent=2)

    def _format_test_summary(self, test_result: dict[str, object]) -> str:
        if not test_result.get("found", False):
            return str(test_result.get("message", "No tests detected."))
        results = test_result.get("results", [])
        lines = []
        for result in results if isinstance(results, list) else []:
            command = result.get("command", "")
            exit_code = result.get("exit_code", "")
            stdout = str(result.get("stdout", ""))[:400]
            stderr = str(result.get("stderr", ""))[:400]
            lines.append(f"{command} exit={exit_code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        return "\n\n".join(lines) if lines else str(test_result.get("message", "Tests ran."))

    def _raise_if_stop_requested(self, task_id: int) -> None:
        if self.task_history.is_stop_requested(task_id):
            raise StopRequestedError(f"Stop requested for task {task_id}.")

    def _agent_usage_payload(self, role: str, result) -> dict[str, object]:
        usage = dict(getattr(result, "usage", {}) or {})
        return {
            "role": role,
            "provider": result.provider,
            "model": result.model,
            "duration_seconds": float(getattr(result, "duration_seconds", 0.0) or 0.0),
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
            "estimated_cost_usd": getattr(result, "estimated_cost_usd", None),
        }

    def _audit_usage_payload(self, role: str, result) -> dict[str, object]:
        return self._agent_usage_payload(role, result)

    def _aggregate_usage(self, model_usage: list[dict[str, object]]) -> dict[str, int]:
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for item in model_usage:
            totals["input_tokens"] += int(item.get("input_tokens", 0))
            totals["output_tokens"] += int(item.get("output_tokens", 0))
            totals["total_tokens"] += int(item.get("total_tokens", 0))
        return totals

    def _sum_cost(self, values) -> float | None:
        total = 0.0
        seen = False
        for value in values:
            if value is None:
                continue
            total += float(value)
            seen = True
        return total if seen else None

    def _tool_errors(self, tool_records) -> list[str]:
        errors: list[str] = []
        for record in tool_records:
            if not record.success:
                errors.append(f"{record.name}: {record.error or 'tool execution failed'}")
        return errors

    def _parse_step_contract(self, raw_step: dict[str, object]) -> PlanStepContract:
        return PlanningSpine._parse_step_contract(raw_step, 0)

    def _default_step_contract(self, title: str) -> PlanStepContract:
        return PlanningSpine._default_step_contract(title, 0)

    def _step_contract(self, plan: StructuredPlan, step_index: int) -> PlanStepContract:
        if step_index < len(plan.contracts):
            return plan.contracts[step_index]
        title = plan.steps[step_index]
        return self._default_step_contract(title)

    def _contract_payload(self, contract: PlanStepContract) -> dict[str, object]:
        return {
            "id": contract.step_id,
            "title": contract.title,
            "objective": contract.objective,
            "agent_role": contract.agent_role,
            "dependencies": contract.dependencies,
            "allowed_paths": contract.allowed_paths,
            "expected_outputs": contract.expected_outputs,
            "required_artifacts": contract.required_artifacts,
            "validation": contract.validation,
            "done_when": contract.done_when,
            "validation_commands": contract.validation_commands,
            "notes": contract.notes,
        }

    def _serialize_contract(self, contract: PlanStepContract) -> str:
        return json.dumps(self._contract_payload(contract), indent=2)

    def _engineer_step_task(self, goal: str, step_contract: PlanStepContract) -> str:
        lines = [
            f"Goal: {goal}",
            f"Step ID: {step_contract.step_id or 'unknown'}",
            f"Current Step: {step_contract.title}",
            f"Objective: {step_contract.objective or step_contract.title}",
            f"Agent Role: {step_contract.agent_role or 'engineer'}",
            "Dependencies:",
        ]
        if step_contract.dependencies:
            lines.extend(f"- {item}" for item in step_contract.dependencies)
        else:
            lines.append("- None")
        lines.extend(
            [
            "Allowed Paths:",
            ]
        )
        if step_contract.allowed_paths:
            lines.extend(f"- {item}" for item in step_contract.allowed_paths)
        else:
            lines.append("- Use the smallest safe scope tied directly to the step.")
        lines.extend(
            [
                "Expected Outputs:",
            ]
        )
        expected_outputs = step_contract.expected_outputs or step_contract.required_artifacts
        if expected_outputs:
            lines.extend(f"- {item}" for item in expected_outputs)
        else:
            lines.append("- None specified")
        lines.extend(
            [
            "Required Artifacts:",
            ]
        )
        if step_contract.required_artifacts:
            lines.extend(f"- {item}" for item in step_contract.required_artifacts)
        else:
            lines.append("- None specified")
        lines.append("Validation:")
        if step_contract.validation:
            lines.extend(f"- {item}" for item in step_contract.validation)
        else:
            lines.append("- None specified")
        lines.append("Done When:")
        if step_contract.done_when:
            lines.extend(f"- {item}" for item in step_contract.done_when)
        else:
            lines.append("- Complete the step with final, non-placeholder artifacts.")
        lines.append("Validation Commands:")
        if step_contract.validation_commands:
            lines.extend(f"- {item}" for item in step_contract.validation_commands)
        else:
            lines.append("- None specified")
        lines.append("Implementation Rules:")
        lines.append("- Implement only the current step.")
        lines.append("- Do not modify files outside the allowed paths for the current step.")
        lines.append("- Do not create empty placeholder files.")
        lines.append("- If a new file is required, write the minimally valid final content in the same attempt.")
        lines.append("- If editing an existing file, call write_file with overwrite=true.")
        lines.append("- Use only plain commands without shell operators like &&, ||, ;, pipes, redirects, or subshells.")
        return "\n".join(lines)

    def _contract_complexity(self, plan: StructuredPlan, contract: PlanStepContract) -> str:
        score = 0
        if len(plan.steps) >= 5:
            score += 1
        if len(contract.allowed_paths) >= 2:
            score += 1
        if len(contract.expected_outputs) >= 2:
            score += 1
        if len(contract.required_artifacts) >= 2:
            score += 1
        if len(contract.validation) >= 2:
            score += 1
        if len(contract.done_when) >= 3:
            score += 1
        if len(contract.validation_commands) >= 2:
            score += 1
        if len(contract.title) + len(contract.objective) >= 220:
            score += 1
        return "high" if score >= 2 else "normal"

    def _validate_step_contract(self, project_root: Path, contract: PlanStepContract) -> list[str]:
        return PlanningSpine.validate_contract_outputs(project_root, contract)

    def _validate_done_when_rule(self, project_root: Path, rule: str) -> list[str]:
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

    def _artifact_file_path(self, artifact: str) -> str | None:
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

    def _string_list(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []

    def _step_failure_category(
        self,
        *,
        local_validation_errors: list[str],
        test_result: dict[str, object],
        audit_passed: bool | None,
        tool_errors: list[str],
        changed_files: list[str],
    ) -> str | None:
        if tool_errors:
            return "tool_error"
        if any("outside allowed paths" in error.lower() for error in local_validation_errors):
            return "scope_violation"
        if local_validation_errors and not changed_files:
            return "bad_plan"
        if local_validation_errors:
            return "validation_failure"
        if test_result and test_result.get("found", False) and not bool(test_result.get("passed", False)):
            return "test_failure"
        if audit_passed is False:
            return "validation_failure"
        return None

    def _classify_step_exception(self, message: str) -> str:
        lowered = message.lower()
        if any(token in lowered for token in ["authentication", "api key", "model_not_found", "does not have access"]):
            return "model_error"
        if "tool" in lowered:
            return "tool_error"
        if "dependency" in lowered or "module" in lowered or "import" in lowered:
            return "dependency_break"
        if "not found" in lowered:
            return "context_missing"
        return "execution_failure"

    def _failure_map_counts(self, step_results: list[StepExecutionResult]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in step_results:
            for label in step.metadata.get("failure_map", []):
                counts[label] = counts.get(label, 0) + 1
        return counts

    def _emit_event(self, event_callback: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
        if event_callback is None:
            return
        try:
            event_callback(payload)
        except Exception:
            self.logger.debug("Loop event callback failed.", exc_info=True)

    def _emit_activity(
        self,
        event_callback: Callable[[dict[str, Any]], None] | None,
        *,
        agent: str,
        status: str,
        message: str,
        project_name: str,
        task_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit_event(
            event_callback,
            {
                "kind": "activity",
                "agent": agent,
                "status": status,
                "message": message,
                "project_name": project_name,
                "task_id": task_id,
                "metadata": metadata or {},
            },
        )

    def _emit_timeline(
        self,
        event_callback: Callable[[dict[str, Any]], None] | None,
        *,
        title: str,
        status: str,
        agent: str,
        project_name: str,
        task_id: int,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit_event(
            event_callback,
            {
                "kind": "timeline",
                "title": title,
                "status": status,
                "agent": agent,
                "project_name": project_name,
                "task_id": task_id,
                "message": message,
                "metadata": metadata or {},
            },
        )

    def _engineer_task_contract(self, *, goal: str, step_contract: PlanStepContract) -> dict[str, object]:
        return {
            "goal": goal,
            "step_id": step_contract.step_id or "",
            "step_goal": step_contract.objective or step_contract.title,
            "agent_role": step_contract.agent_role,
            "dependencies": step_contract.dependencies,
            "allowed_paths": step_contract.allowed_paths,
            "expected_outputs": step_contract.expected_outputs or step_contract.required_artifacts,
            "validation": step_contract.validation or step_contract.done_when,
            "deliverable": "Implement only the current plan step and satisfy every current-step contract item.",
        }

    def _auditor_task_contract(self, *, step_contract: PlanStepContract, changed_files: list[str]) -> dict[str, object]:
        return {
            "goal": f"Audit step {step_contract.step_id or step_contract.title}",
            "step_id": step_contract.step_id or "",
            "step_goal": step_contract.objective or step_contract.title,
            "agent_role": step_contract.agent_role,
            "dependencies": step_contract.dependencies,
            "allowed_paths": step_contract.allowed_paths,
            "expected_outputs": step_contract.expected_outputs or step_contract.required_artifacts,
            "validation": step_contract.validation or step_contract.done_when,
            "changed_files": changed_files,
            "deliverable": "Validate that the implementation satisfies the current plan step without scope drift.",
        }

    def _step_execution_rules(self, contract: PlanStepContract) -> list[str]:
        rules = [
            "Advance only if the current step contract is satisfied.",
            "Treat the execution spine as binding runtime state, not optional guidance.",
        ]
        if contract.dependencies:
            rules.append(
                "Do not assume dependency steps are complete unless they appear as completed in the execution spine: "
                + ", ".join(contract.dependencies)
            )
        if contract.allowed_paths:
            rules.append(f"Do not modify files outside: {', '.join(contract.allowed_paths)}")
        outputs = contract.expected_outputs or contract.required_artifacts
        if outputs:
            rules.append(f"Produce these outputs before stopping: {', '.join(outputs)}")
        return rules

    def _task_metadata(
        self,
        plan_result,
        step_results: list[StepExecutionResult],
        run_graph_payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "plan_model": plan_result.model if plan_result is not None else None,
            "failure_map_counts": self._failure_map_counts(step_results),
            "spine_metrics": self._spine_metrics(step_results),
            "run_graph": run_graph_payload,
            "run_graph_parallel": self._run_graph_parallel_enabled(),
            "commit_gate": self._commit_gate_summary(step_results),
        }

    def _run_graph_parallel_enabled(self) -> bool:
        value = os.getenv("RUN_GRAPH_PARALLEL", "")
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _safe_live_update(self, live: Live, live_lock: threading.RLock | None, renderable) -> None:
        if live_lock is None:
            live.update(renderable)
            return
        with live_lock:
            live.update(renderable)

    def _spine_metrics(self, step_results: list[StepExecutionResult]) -> dict[str, object]:
        attempted = len(step_results)
        completed = sum(1 for item in step_results if item.status == "completed")
        failed = sum(1 for item in step_results if item.status == "failed")
        stopped = sum(1 for item in step_results if item.status == "stopped")
        iteration_values = [item.iterations for item in step_results if item.iterations]
        return {
            "step_attempted": attempted,
            "step_completed": completed,
            "step_failed": failed,
            "step_stopped": stopped,
            "step_success_rate": float(completed / attempted) if attempted else None,
            "avg_step_iterations": float(sum(iteration_values) / len(iteration_values)) if iteration_values else None,
        }

    def _commit_gate_summary(self, step_results: list[StepExecutionResult]) -> dict[str, object]:
        pending_steps: list[dict[str, object]] = []
        committed_steps: list[dict[str, object]] = []
        failed_steps: list[dict[str, object]] = []
        for item in step_results:
            gate = (item.metadata or {}).get("commit_gate", {})
            if not isinstance(gate, dict):
                continue
            payload = {
                "step_index": item.step_index,
                "step_title": item.step_title,
                "message": str(gate.get("message", "")),
            }
            status = str(gate.get("status", "")).strip().lower()
            if status == "pending":
                pending_steps.append(payload)
            elif status == "committed":
                payload["commit"] = str(gate.get("commit", ""))
                committed_steps.append(payload)
            elif status in {"failed", "skipped"}:
                failed_steps.append(payload)
        if pending_steps:
            status = "pending"
        elif failed_steps:
            status = "needs_attention"
        elif committed_steps:
            status = "committed"
        else:
            status = "none"
        return {
            "status": status,
            "pending_steps": pending_steps,
            "committed_steps": committed_steps,
            "failed_steps": failed_steps,
        }
