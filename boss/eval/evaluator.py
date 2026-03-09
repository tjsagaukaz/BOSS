from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss.artifacts import ArtifactStore
from boss.context.codebase_scanner import CodebaseScanner
from boss.eval.eval_store import EvaluationStore
from boss.eval.project_sandbox import ProjectSandbox, ProjectSandboxManager
from boss.eval.task_contracts import find_symbol_occurrences, load_task_suite
from boss.reliability import classify_failure_map
from boss.tools.terminal_tools import TerminalTools
from boss.types import EvalRunResult, EvalTaskResult, TaskContract, TaskSuite, ValidationOutcome, utc_now_iso


@dataclass
class ExecutionArtifact:
    status: str
    output_summary: str
    full_output: str
    files_changed: list[str]
    errors: list[str]
    model_usage: list[dict[str, Any]]
    token_usage: dict[str, int]
    estimated_cost_usd: float | None
    tool_errors: list[str]
    metadata: dict[str, Any]


@dataclass
class ExecutionEnvironment:
    python_bin: str
    requested_python_bin: str | None = None
    setup_commands: list[str] = None
    setup_results: list[dict[str, Any]] = None
    created_venv: bool = False
    venv_dir: str | None = None

    def __post_init__(self) -> None:
        if self.setup_commands is None:
            self.setup_commands = []
        if self.setup_results is None:
            self.setup_results = []

    def as_metadata(self) -> dict[str, Any]:
        return {
            "python_bin": self.python_bin,
            "requested_python_bin": self.requested_python_bin,
            "setup_commands": list(self.setup_commands),
            "setup_results": list(self.setup_results),
            "created_venv": self.created_venv,
            "venv_dir": self.venv_dir,
        }


class EvaluationHarness:
    def __init__(self, orchestrator, store: EvaluationStore, artifact_store: ArtifactStore | None = None) -> None:
        self.orchestrator = orchestrator
        self.store = store
        self.artifact_store = artifact_store
        self.sandbox_manager = ProjectSandboxManager(self.orchestrator.root_dir / "projects")

    def run_suite(
        self,
        suite_path: str | Path,
        project_name: str | None = None,
        stop_on_failure: bool | None = None,
    ) -> EvalRunResult:
        suite = load_task_suite(suite_path)
        return self.run_task_suite(
            suite=suite,
            project_name=project_name,
            stop_on_failure=stop_on_failure,
        )

    def run_task_suite(
        self,
        suite: TaskSuite,
        project_name: str | None = None,
        stop_on_failure: bool | None = None,
    ) -> EvalRunResult:
        target_project = project_name or suite.project_name or self.orchestrator.get_active_project_name()
        if not target_project:
            raise RuntimeError("No project specified for evaluation. Set an active project or define project_name in the suite.")

        run_id = self.store.create_run(
            suite_name=suite.name,
            suite_path=suite.path,
            project_name=target_project,
            total_tasks=len(suite.tasks),
            metadata={"suite_metadata": suite.metadata},
        )
        started_at = utc_now_iso()
        start = time.perf_counter()
        task_results: list[EvalTaskResult] = []

        effective_stop_on_failure = suite.stop_on_failure if stop_on_failure is None else stop_on_failure
        status = "passed"
        for task_index, contract in enumerate(suite.tasks):
            result = self._run_task(run_id, task_index, target_project, contract)
            task_results.append(result)
            self.store.record_task_result(run_id, result)
            if result.status != "passed":
                status = "failed"
                if effective_stop_on_failure:
                    break

        runtime_seconds = time.perf_counter() - start
        passed_tasks = sum(1 for item in task_results if item.status == "passed")
        failed_tasks = len(task_results) - passed_tasks
        total_estimated_cost = self._sum_cost(item.estimated_cost_usd for item in task_results)
        result = EvalRunResult(
            run_id=run_id,
            suite_name=suite.name,
            suite_path=suite.path,
            project_name=target_project,
            status=status,
            total_tasks=len(task_results),
            passed_tasks=passed_tasks,
            failed_tasks=failed_tasks,
            runtime_seconds=runtime_seconds,
            total_estimated_cost_usd=total_estimated_cost,
            tasks=task_results,
            metadata={"suite_metadata": suite.metadata},
            started_at=started_at,
            completed_at=utc_now_iso(),
        )
        if self.artifact_store is not None:
            result.metadata["artifact_path"] = self.artifact_store.write_evaluation_run_artifact(result)
        self.store.finalize_run(
            run_id=run_id,
            status=status,
            passed_tasks=passed_tasks,
            failed_tasks=failed_tasks,
            runtime_seconds=runtime_seconds,
            total_estimated_cost_usd=total_estimated_cost,
            metadata=result.metadata,
        )
        return result

    def _run_task(self, run_id: int, task_index: int, default_project_name: str, contract: TaskContract) -> EvalTaskResult:
        target_project = contract.project_name or default_project_name
        current_project = self.orchestrator.get_active_project_name()
        execution_project_name = target_project
        sandbox: ProjectSandbox | None = None
        benchmark_mode = bool((contract.metadata or {}).get("benchmark_mode", False))
        sandbox_mode = self._sandbox_mode(contract.mode, contract.sandbox_mode)
        if sandbox_mode != "none":
            sandbox = self.sandbox_manager.create_sandbox(target_project, contract.name, mode=sandbox_mode)
            execution_project_name = sandbox.sandbox_project_name
        if current_project != execution_project_name:
            if benchmark_mode:
                self.orchestrator.project_indexer.index_project(
                    project_name=execution_project_name,
                    force=False,
                    force_heuristic=True,
                )
                self.orchestrator._update_state(active_project=execution_project_name)
            else:
                self.orchestrator.set_active_project(execution_project_name)

        started_at = utc_now_iso()
        start = time.perf_counter()
        artifact: ExecutionArtifact | None = None
        environment: ExecutionEnvironment | None = None
        errors: list[str] = []
        source_project_root = (self.orchestrator.root_dir / "projects" / target_project).resolve()
        execution_project_root = (self.orchestrator.root_dir / "projects" / execution_project_name).resolve()
        try:
            environment = self._prepare_execution_environment(execution_project_name, contract)
            artifact = self._execute_contract(target_project, execution_project_name, contract, environment)
            validations = self._validate_contract(execution_project_name, contract, artifact, environment)
            status = "passed" if all(item.passed for item in validations) else "failed"
            failure_category = None if status == "passed" else self._classify_failure(contract, artifact, validations)
            failure_map = [] if status == "passed" else self._failure_map(
                failure_category=failure_category,
                contract=contract,
                artifact=artifact,
                validations=validations,
            )
            errors = list(artifact.errors)
            if status != "passed":
                errors.extend(item.message for item in validations if not item.passed)
        except Exception as exc:
            runtime_seconds = time.perf_counter() - start
            message = str(exc)
            failure_category = self._classify_exception(message)
            failure_map = classify_failure_map(
                failure_category=failure_category,
                errors=[message],
                metadata=contract.metadata,
            )
            result = EvalTaskResult(
                task_name=contract.name,
                description=contract.description,
                project_name=target_project,
                mode=contract.mode,
                status="failed",
                runtime_seconds=runtime_seconds,
                files_changed=[],
                errors=[message],
                failure_category=failure_category,
                output_summary=message,
                validations=[ValidationOutcome(name="execution_status", passed=False, message=message)],
                model_usage=[],
                token_usage={},
                estimated_cost_usd=None,
                metadata={
                    "contract": self._contract_payload(contract),
                    "source_project_name": target_project,
                    "execution_project_name": execution_project_name,
                    "sandboxed": sandbox is not None,
                    "sandbox_mode": sandbox.sandbox_mode if sandbox is not None else "none",
                    "sandbox_path": str(sandbox.sandbox_root) if sandbox is not None else None,
                    "sandbox_branch": sandbox.branch_name if sandbox is not None else None,
                    "sandbox_base_revision": sandbox.base_revision if sandbox is not None else None,
                    "execution_environment": environment.as_metadata() if environment is not None else {},
                    "failure_map": failure_map,
                    "failure_map_primary": failure_map[0] if failure_map else None,
                },
                started_at=started_at,
                completed_at=utc_now_iso(),
            )
            if self.artifact_store is not None:
                result.metadata["artifact_path"] = self.artifact_store.write_evaluation_task_artifact(
                    run_id=run_id,
                    task_index=task_index,
                    task_result=result,
                    contract_payload=self._contract_payload(contract),
                    full_output=message,
                    source_project_root=source_project_root,
                    execution_project_root=execution_project_root,
                )
            return result
        finally:
            restore_project = current_project or target_project
            if sandbox is not None and not contract.keep_sandbox:
                self.orchestrator.cleanup_project_artifacts(sandbox.sandbox_project_name, remove_directory=False)
                self.sandbox_manager.cleanup(sandbox)
            if restore_project != self.orchestrator.get_active_project_name():
                try:
                    self.orchestrator.set_active_project(restore_project)
                except Exception:
                    pass

        runtime_seconds = time.perf_counter() - start
        result = EvalTaskResult(
            task_name=contract.name,
            description=contract.description,
            project_name=target_project,
            mode=contract.mode,
            status=status,
            runtime_seconds=runtime_seconds,
            files_changed=artifact.files_changed,
            errors=list(dict.fromkeys(errors)),
            failure_category=failure_category,
            output_summary=artifact.output_summary,
            validations=validations,
            model_usage=artifact.model_usage,
            token_usage=artifact.token_usage,
            estimated_cost_usd=artifact.estimated_cost_usd,
            metadata={
                "contract": self._contract_payload(contract),
                "source_project_name": target_project,
                "execution_project_name": execution_project_name,
                "sandboxed": sandbox is not None,
                "sandbox_mode": sandbox.sandbox_mode if sandbox is not None else "none",
                "sandbox_path": str(sandbox.sandbox_root) if sandbox is not None else None,
                "sandbox_branch": sandbox.branch_name if sandbox is not None else None,
                "sandbox_base_revision": sandbox.base_revision if sandbox is not None else None,
                "execution_environment": environment.as_metadata() if environment is not None else {},
                "failure_map": failure_map,
                "failure_map_primary": failure_map[0] if failure_map else None,
                **artifact.metadata,
            },
            started_at=started_at,
            completed_at=utc_now_iso(),
        )
        if self.artifact_store is not None:
            result.metadata["artifact_path"] = self.artifact_store.write_evaluation_task_artifact(
                run_id=run_id,
                task_index=task_index,
                task_result=result,
                contract_payload=self._contract_payload(contract),
                full_output=artifact.full_output,
                source_project_root=source_project_root,
                execution_project_root=execution_project_root,
            )
        return result

    def _execute_contract(
        self,
        source_project_name: str,
        execution_project_name: str,
        contract: TaskContract,
        environment: ExecutionEnvironment,
    ) -> ExecutionArtifact:
        mode = contract.mode
        if mode == "plan":
            result = self.orchestrator.plan(contract.description)
            model_usage = [self._agent_usage_payload("architect", result)]
            return ExecutionArtifact(
                status="passed",
                output_summary=result.text[:6000],
                full_output=result.text,
                files_changed=[],
                errors=[],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                tool_errors=self._tool_errors(result.tool_records),
                metadata={},
            )

        if mode == "audit":
            result = self.orchestrator.audit(contract.description)
            model_usage = [self._audit_usage_payload("auditor", result)]
            return ExecutionArtifact(
                status="passed" if result.passed else "needs_followup",
                output_summary=result.text[:6000],
                full_output=result.text,
                files_changed=[],
                errors=[] if result.passed else [result.text],
                model_usage=model_usage,
                token_usage=self._aggregate_usage(model_usage),
                estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
                tool_errors=self._tool_errors(result.tool_records),
                metadata={},
            )

        if mode == "test":
            result = self.orchestrator.run_tests(project_name=execution_project_name, python_bin=environment.python_bin)
            outputs: list[str] = [str(result.get("message", ""))]
            for command_result in result.get("results", []):
                if not isinstance(command_result, dict):
                    continue
                command = str(command_result.get("command", ""))
                stdout = str(command_result.get("stdout", "")).strip()
                stderr = str(command_result.get("stderr", "")).strip()
                if command:
                    outputs.append(f"$ {command}")
                if stdout:
                    outputs.append(stdout)
                if stderr:
                    outputs.append(stderr)
            passed = bool(result.get("passed", False) or not result.get("found", False))
            return ExecutionArtifact(
                status="passed" if passed else "failed",
                output_summary=str(result.get("message", ""))[:6000],
                full_output="\n".join(item for item in outputs if item).strip(),
                files_changed=[],
                errors=[] if passed else [str(result.get("message", "Tests failed."))],
                model_usage=[],
                token_usage={},
                estimated_cost_usd=None,
                tool_errors=[],
                metadata={
                    "test_result": result,
                    "benchmark_metrics": {"tests_passed": 1.0 if bool(result.get("passed", False)) else 0.0},
                },
            )

        if mode == "build":
            result = self.orchestrator.build(
                contract.description,
                auto_approve=contract.auto_approve,
                max_iterations=contract.max_iterations,
                commit_changes=False,
                project_name=execution_project_name,
                store_knowledge=False,
                deep=bool(contract.metadata.get("deep", False)),
                benchmark_mode=bool(contract.metadata.get("benchmark_mode", False)),
            )
            return ExecutionArtifact(
                status=result.status,
                output_summary=result.final_result[:6000],
                full_output=result.final_result,
                files_changed=result.changed_files,
                errors=result.errors,
                model_usage=result.model_usage,
                token_usage=result.token_usage,
                estimated_cost_usd=result.estimated_cost_usd,
                tool_errors=[],
                metadata={
                    "task_id": result.task_id,
                    "runtime_seconds": result.runtime_seconds,
                    "step_results": [step.step_title for step in result.step_results],
                    "step_telemetry": [
                        {
                            "step_index": step.step_index,
                            "step_title": step.step_title,
                            "status": step.status,
                            "runtime_seconds": step.runtime_seconds,
                            "iterations": step.iterations,
                            "token_usage": step.token_usage,
                            "estimated_cost_usd": step.estimated_cost_usd,
                        }
                        for step in result.step_results
                    ],
                },
            )

        result = self.orchestrator.code(
            contract.description,
            auto_approve=contract.auto_approve,
            max_iterations=contract.max_iterations,
            project_name=execution_project_name,
            store_knowledge=False,
            deep=bool(contract.metadata.get("deep", False)),
            skip_planning=bool(contract.metadata.get("direct_engineer", False)),
            plan_override=str(contract.metadata.get("plan_override", "")).strip() or None,
            benchmark_mode=bool(contract.metadata.get("benchmark_mode", False)),
            skip_audit=bool(contract.metadata.get("skip_audit", False)),
        )
        model_usage = [
            self._agent_usage_payload("architect", result.plan),
            self._agent_usage_payload("engineer", result.implementation),
            self._audit_usage_payload("auditor", result.audit),
        ]
        full_output = "\n\n".join([result.plan.text, result.implementation.text, result.audit.text]).strip()
        tool_errors = []
        tool_errors.extend(self._tool_errors(result.plan.tool_records))
        tool_errors.extend(self._tool_errors(result.implementation.tool_records))
        tool_errors.extend(self._tool_errors(result.audit.tool_records))
        return ExecutionArtifact(
            status="passed" if result.audit.passed else "needs_followup",
            output_summary=result.implementation.text[:6000],
            full_output=full_output,
            files_changed=result.changed_files,
            errors=[] if result.audit.passed else [result.audit.text],
            model_usage=model_usage,
            token_usage=self._aggregate_usage(model_usage),
            estimated_cost_usd=self._sum_cost(item.get("estimated_cost_usd") for item in model_usage),
            tool_errors=tool_errors,
            metadata={"iterations": result.iterations},
        )

    def _validate_contract(
        self,
        execution_project_name: str,
        contract: TaskContract,
        artifact: ExecutionArtifact,
        environment: ExecutionEnvironment,
    ) -> list[ValidationOutcome]:
        validations: list[ValidationOutcome] = []
        validations.append(self._validate_execution_status(contract, artifact))
        project_root = (self.orchestrator.root_dir / "projects" / execution_project_name).resolve()
        benchmark_metrics = dict(artifact.metadata.get("benchmark_metrics", {}))

        if contract.allowed_paths:
            violations = [
                path for path in artifact.files_changed if not self._path_allowed(path, contract.allowed_paths)
            ]
            validations.append(
                ValidationOutcome(
                    name="allowed_paths",
                    passed=not violations,
                    message=(
                        "All changed files stayed within allowed paths."
                        if not violations
                        else f"Files changed outside allowed paths: {', '.join(violations)}"
                    ),
                )
            )

        if contract.expected_files:
            missing = [path for path in contract.expected_files if not (project_root / path).exists()]
            validations.append(
                ValidationOutcome(
                    name="expected_files",
                    passed=not missing,
                    message="All expected files exist." if not missing else f"Missing expected files: {', '.join(missing)}",
                )
            )

        if contract.expected_file_contains:
            content_failures: list[str] = []
            for file_path, snippets in contract.expected_file_contains.items():
                target = project_root / file_path
                if not target.exists():
                    content_failures.append(f"{file_path}: file does not exist")
                    continue
                content = target.read_text(encoding="utf-8", errors="replace")
                missing_snippets = [snippet for snippet in snippets if snippet not in content]
                if missing_snippets:
                    content_failures.append(f"{file_path}: missing {', '.join(missing_snippets)}")
            validations.append(
                ValidationOutcome(
                    name="expected_file_contains",
                    passed=not content_failures,
                    message=(
                        "Expected file contents found."
                        if not content_failures
                        else "File content validation failed: " + "; ".join(content_failures)
                    ),
                )
            )

        if contract.expected_imports:
            import_results: list[dict[str, Any]] = []
            import_failures: list[str] = []
            terminal = self._terminal_tools(project_root)
            for module_name in contract.expected_imports:
                formatted_command = self._import_check_command(module_name, environment=environment)
                result = terminal.run_terminal(command=formatted_command, timeout=120, workdir=str(project_root))
                passed = int(result.get("exit_code", 1)) == 0
                import_results.append(
                    {
                        "module": module_name,
                        "command": formatted_command,
                        "exit_code": int(result.get("exit_code", 1)),
                        "stdout": str(result.get("stdout", "")),
                        "stderr": str(result.get("stderr", "")),
                    }
                )
                if not passed:
                    import_failures.append(module_name)
            artifact.metadata["import_check_results"] = import_results
            validations.append(
                ValidationOutcome(
                    name="expected_imports",
                    passed=not import_failures,
                    message=(
                        "All expected imports resolved."
                        if not import_failures
                        else f"Import validation failed for: {', '.join(import_failures)}"
                    ),
                )
            )

        if contract.expected_symbols:
            missing_symbols: list[str] = []
            for symbol in contract.expected_symbols:
                if not find_symbol_occurrences(project_root, symbol):
                    missing_symbols.append(symbol)
            validations.append(
                ValidationOutcome(
                    name="expected_symbols",
                    passed=not missing_symbols,
                    message="All expected symbols found." if not missing_symbols else f"Missing expected symbols: {', '.join(missing_symbols)}",
                )
            )

        if contract.required_changed_files:
            missing_changes = [path for path in contract.required_changed_files if path not in artifact.files_changed]
            validations.append(
                ValidationOutcome(
                    name="required_changed_files",
                    passed=not missing_changes,
                    message="Required changed files were touched." if not missing_changes else f"Required files not changed: {', '.join(missing_changes)}",
                )
            )

        if contract.forbidden_changed_files:
            forbidden = [path for path in contract.forbidden_changed_files if path in artifact.files_changed]
            validations.append(
                ValidationOutcome(
                    name="forbidden_changed_files",
                    passed=not forbidden,
                    message="No forbidden files were modified." if not forbidden else f"Forbidden files changed: {', '.join(forbidden)}",
                )
            )

        if contract.expected_output_contains:
            missing_output = [
                item for item in contract.expected_output_contains if item.lower() not in artifact.full_output.lower()
            ]
            validations.append(
                ValidationOutcome(
                    name="expected_output_contains",
                    passed=not missing_output,
                    message="Expected output strings present." if not missing_output else f"Missing output snippets: {', '.join(missing_output)}",
                )
            )

        if contract.forbidden_output_contains:
            forbidden_output = [
                item for item in contract.forbidden_output_contains if item.lower() in artifact.full_output.lower()
            ]
            validations.append(
                ValidationOutcome(
                    name="forbidden_output_contains",
                    passed=not forbidden_output,
                    message=(
                        "Forbidden output strings were not present."
                        if not forbidden_output
                        else f"Forbidden output snippets detected: {', '.join(forbidden_output)}"
                    ),
                )
            )

        if contract.require_tests_passed:
            test_result = self.orchestrator.run_tests(
                project_name=execution_project_name,
                python_bin=environment.python_bin,
            )
            passed = bool(test_result.get("found", False)) and bool(test_result.get("passed", False))
            benchmark_metrics["tests_passed"] = 1.0 if passed else 0.0
            validations.append(
                ValidationOutcome(
                    name="require_tests_passed",
                    passed=passed,
                    message=test_result.get("message", "Tests did not run.") if isinstance(test_result, dict) else "Tests did not run.",
                )
            )

        if contract.validation_commands:
            terminal = self._terminal_tools(project_root)
            command_results: list[dict[str, Any]] = []
            for command in contract.validation_commands:
                formatted_command = self._format_command(command, environment=environment, project_root=project_root)
                command_start = time.perf_counter()
                result = terminal.run_terminal(command=formatted_command, timeout=1200, workdir=str(project_root))
                duration_seconds = time.perf_counter() - command_start
                passed = int(result.get("exit_code", 1)) == 0
                stdout = str(result.get("stdout", ""))
                stderr = str(result.get("stderr", ""))
                parsed_metrics = self._extract_metrics_from_output(stdout)
                benchmark_metrics.update(parsed_metrics)
                command_results.append(
                    {
                        "command": formatted_command,
                        "exit_code": int(result.get("exit_code", 1)),
                        "stdout": stdout,
                        "stderr": stderr,
                        "runtime_seconds": duration_seconds,
                        "metrics": parsed_metrics,
                    }
                )
                validations.append(
                    ValidationOutcome(
                        name=f"validation_command:{formatted_command}",
                        passed=passed,
                        message="Validation command passed." if passed else str(result.get("stderr") or result.get("stdout") or "Validation command failed."),
                    )
                )
            artifact.metadata["validation_command_results"] = command_results

        artifact.metadata["benchmark_metrics"] = benchmark_metrics
        if contract.metric_targets:
            validations.append(self._validate_metric_targets(contract.metric_targets, benchmark_metrics))

        return validations

    def _sandbox_mode(self, mode: str, requested_mode: str | None) -> str:
        if requested_mode:
            return requested_mode
        if mode in {"code", "build", "test"}:
            return "auto"
        return "none"

    def _prepare_execution_environment(self, execution_project_name: str, contract: TaskContract) -> ExecutionEnvironment:
        project_root = (self.orchestrator.root_dir / "projects" / execution_project_name).resolve()
        requested_python_bin = str((contract.metadata or {}).get("python_bin") or "").strip() or None
        base_python_bin = self._resolve_python_bin(requested_python_bin)
        create_venv = bool((contract.metadata or {}).get("setup_create_venv", False))
        raw_venv_dir_name = (contract.metadata or {}).get("setup_venv_dir")
        venv_dir_name = str(raw_venv_dir_name).strip() if raw_venv_dir_name is not None else ""
        venv_dir_name = venv_dir_name or ".boss_benchmark_venv"
        timeout = int((contract.metadata or {}).get("setup_timeout", 1200) or 1200)

        python_bin = base_python_bin
        created_venv = False
        venv_dir: Path | None = None
        if create_venv:
            venv_dir = project_root / venv_dir_name
            venv_python = venv_dir / "bin" / "python"
            if not venv_python.exists():
                result = subprocess.run(
                    [base_python_bin, "-m", "venv", venv_dir_name],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=min(timeout, 1200),
                    check=False,
                )
                if result.returncode != 0:
                    message = result.stderr.strip() or result.stdout.strip() or "Failed to create benchmark venv."
                    raise RuntimeError(f"Setup failed creating benchmark venv: {message}")
            python_bin = str(venv_python)
            created_venv = True

        environment = ExecutionEnvironment(
            python_bin=python_bin,
            requested_python_bin=requested_python_bin,
            created_venv=created_venv,
            venv_dir=str(venv_dir.resolve()) if venv_dir is not None else None,
        )

        setup_commands = self._as_list((contract.metadata or {}).get("setup_commands"))
        if not setup_commands:
            return environment

        terminal = self._terminal_tools(project_root)
        for command in setup_commands:
            formatted_command = self._format_command(command, environment=environment, project_root=project_root)
            result = terminal.run_terminal(command=formatted_command, timeout=timeout, workdir=str(project_root))
            environment.setup_commands.append(formatted_command)
            environment.setup_results.append(dict(result))
            if int(result.get("exit_code", 1)) != 0:
                message = str(result.get("stderr") or result.get("stdout") or "Setup command failed.").strip()
                raise RuntimeError(f"Setup failed: {formatted_command}: {message}")
        return environment

    def _resolve_python_bin(self, value: str | None) -> str:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in {"current", "default"}:
            return sys.executable
        candidate = Path(cleaned).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        executable = shutil.which(cleaned)
        if executable:
            return executable
        raise RuntimeError(f"Interpreter not found: {cleaned}")

    def _format_command(self, command: str, *, environment: ExecutionEnvironment, project_root: Path) -> str:
        payload = {
            "python_bin": environment.python_bin,
            "project_root": str(project_root),
            "venv_dir": environment.venv_dir or "",
        }
        return command.format(**payload)

    def _import_check_command(self, module_name: str, *, environment: ExecutionEnvironment) -> str:
        script = "\n".join(
            [
                "import importlib",
                "import pathlib",
                "import sys",
                "root = pathlib.Path.cwd()",
                "sys.path.insert(0, str(root))",
                "src = root / 'src'",
                "if src.exists():",
                "    sys.path.insert(0, str(src))",
                f"importlib.import_module({module_name!r})",
                "print('import_ok')",
            ]
        )
        return shlex.join([environment.python_bin, "-c", script])

    def _terminal_tools(self, project_root: Path):
        return TerminalTools(root=project_root)

    def _as_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []

    def _validate_execution_status(self, contract: TaskContract, artifact: ExecutionArtifact) -> ValidationOutcome:
        if contract.expected_status:
            passed = artifact.status == contract.expected_status
            return ValidationOutcome(
                name="execution_status",
                passed=passed,
                message=f"Expected status {contract.expected_status}, got {artifact.status}.",
            )
        passed = artifact.status in {"passed", "completed"}
        return ValidationOutcome(
            name="execution_status",
            passed=passed,
            message="Execution completed successfully." if passed else f"Execution ended with status {artifact.status}.",
        )

    def _validate_metric_targets(
        self,
        metric_targets: dict[str, dict[str, float]],
        benchmark_metrics: dict[str, float],
    ) -> ValidationOutcome:
        failures: list[str] = []
        for metric_name, comparisons in metric_targets.items():
            if metric_name not in benchmark_metrics:
                failures.append(f"{metric_name}: metric missing")
                continue
            value = float(benchmark_metrics[metric_name])
            for operator, target in comparisons.items():
                if operator == "eq" and value != target:
                    failures.append(f"{metric_name}: expected == {target}, got {value}")
                elif operator == "gt" and not value > target:
                    failures.append(f"{metric_name}: expected > {target}, got {value}")
                elif operator == "gte" and not value >= target:
                    failures.append(f"{metric_name}: expected >= {target}, got {value}")
                elif operator == "lt" and not value < target:
                    failures.append(f"{metric_name}: expected < {target}, got {value}")
                elif operator == "lte" and not value <= target:
                    failures.append(f"{metric_name}: expected <= {target}, got {value}")
        return ValidationOutcome(
            name="metric_targets",
            passed=not failures,
            message="All metric targets satisfied." if not failures else "; ".join(failures),
        )

    def _classify_failure(
        self,
        contract: TaskContract,
        artifact: ExecutionArtifact,
        validations: list[ValidationOutcome],
    ) -> str:
        failed_validations = [item for item in validations if not item.passed]
        failed_names = {item.name.split(":", 1)[0] for item in failed_validations}
        if "forbidden_changed_files" in failed_names or "allowed_paths" in failed_names:
            return "scope_violation"
        if "required_changed_files" in failed_names or "forbidden_output_contains" in failed_names:
            return "bad_plan"
        if "metric_targets" in failed_names:
            return "validation_failure"
        if "expected_imports" in failed_names:
            return "dependency_break"
        if "require_tests_passed" in failed_names or any(item.name.startswith("validation_command:") for item in failed_validations):
            return "test_failure"
        if artifact.tool_errors:
            return "tool_error"

        text = "\n".join(artifact.errors + artifact.tool_errors + [item.message for item in failed_validations]).lower()
        if any(token in text for token in ["authentication", "api key", "model_not_found", "does not have access"]):
            return "model_error"
        if "dependency" in text or "module" in text or "import" in text:
            return "dependency_break"
        if "not found" in text or "missing expected" in text or "missing expected symbols" in text:
            return "context_missing"
        if contract.mode == "plan":
            return "bad_plan"
        if "execution_status" in failed_names and not artifact.files_changed:
            return "bad_plan"
        return "validation_failure"

    def _failure_map(
        self,
        *,
        failure_category: str | None,
        contract: TaskContract,
        artifact: ExecutionArtifact,
        validations: list[ValidationOutcome],
    ) -> list[str]:
        failed_validation_names = {item.name.split(":", 1)[0] for item in validations if not item.passed}
        return classify_failure_map(
            failure_category=failure_category,
            failed_validation_names=failed_validation_names,
            errors=list(artifact.errors),
            tool_errors=list(artifact.tool_errors),
            changed_files=list(artifact.files_changed),
            tests_passed=self._tests_passed(artifact.metadata),
            audit_passed=artifact.status in {"passed", "completed"},
            metadata=contract.metadata,
        )

    def _classify_exception(self, message: str) -> str:
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

    def _agent_usage_payload(self, role: str, result) -> dict[str, Any]:
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

    def _audit_usage_payload(self, role: str, result) -> dict[str, Any]:
        return self._agent_usage_payload(role, result)

    def _aggregate_usage(self, model_usage: list[dict[str, Any]]) -> dict[str, int]:
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for item in model_usage:
            totals["input_tokens"] += int(item.get("input_tokens", 0))
            totals["output_tokens"] += int(item.get("output_tokens", 0))
            totals["total_tokens"] += int(item.get("total_tokens", 0))
        return totals

    def _tool_errors(self, tool_records) -> list[str]:
        errors: list[str] = []
        for record in tool_records:
            if not record.success:
                errors.append(f"{record.name}: {record.error or 'tool execution failed'}")
        return errors

    def _sum_cost(self, values) -> float | None:
        total = 0.0
        seen = False
        for value in values:
            if value is None:
                continue
            total += float(value)
            seen = True
        return total if seen else None

    def _path_allowed(self, path: str, allowed_paths: list[str]) -> bool:
        normalized = path.replace("\\", "/").lstrip("./")
        for allowed in allowed_paths:
            cleaned = allowed.replace("\\", "/").lstrip("./").rstrip("/")
            if not cleaned:
                continue
            if normalized == cleaned or normalized.startswith(f"{cleaned}/"):
                return True
        return False

    def _extract_metrics_from_output(self, stdout: str) -> dict[str, float]:
        metrics: dict[str, float] = {}
        cleaned = stdout.strip()
        if not cleaned:
            return metrics

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, bool):
                    metrics[str(key)] = 1.0 if value else 0.0
                elif isinstance(value, (int, float)):
                    metrics[str(key)] = float(value)

        for line in cleaned.splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z%_]*)\s*$", line)
            if not match:
                continue
            name = match.group(1)
            value = float(match.group(2))
            metrics[name] = value
        return metrics

    def _tests_passed(self, metadata: dict[str, Any]) -> bool | None:
        metrics = metadata.get("benchmark_metrics", {})
        if isinstance(metrics, dict) and "tests_passed" in metrics:
            return bool(metrics.get("tests_passed"))
        test_result = metadata.get("test_result")
        if isinstance(test_result, dict) and test_result.get("found") is not None:
            return bool(test_result.get("passed", False))
        return None

    def _contract_payload(self, contract: TaskContract) -> dict[str, Any]:
        return {
            "name": contract.name,
            "description": contract.description,
            "mode": contract.mode,
            "project_name": contract.project_name,
            "sandbox_mode": contract.sandbox_mode,
            "keep_sandbox": contract.keep_sandbox,
            "allowed_paths": contract.allowed_paths,
            "expected_files": contract.expected_files,
            "expected_file_contains": contract.expected_file_contains,
            "expected_imports": contract.expected_imports,
            "expected_symbols": contract.expected_symbols,
            "required_changed_files": contract.required_changed_files,
            "forbidden_changed_files": contract.forbidden_changed_files,
            "validation_commands": contract.validation_commands,
            "metric_targets": contract.metric_targets,
            "expected_output_contains": contract.expected_output_contains,
            "forbidden_output_contains": contract.forbidden_output_contains,
            "expected_status": contract.expected_status,
            "require_tests_passed": contract.require_tests_passed,
            "auto_approve": contract.auto_approve,
            "max_iterations": contract.max_iterations,
            "metadata": contract.metadata,
        }
