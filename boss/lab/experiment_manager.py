from __future__ import annotations

import difflib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from boss.lab.benchmark_runner import BenchmarkRunner
from boss.lab.lab_registry import LabRegistry
from boss.lab.result_analyzer import ResultAnalyzer
from boss.lab.variant_generator import VariantGenerator


class ExperimentManager:
    def __init__(
        self,
        *,
        root_dir: str | Path,
        orchestrator,
        registry: LabRegistry,
        variant_generator: VariantGenerator,
        benchmark_runner: BenchmarkRunner,
        result_analyzer: ResultAnalyzer,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.orchestrator = orchestrator
        self.registry = registry
        self.variant_generator = variant_generator
        self.benchmark_runner = benchmark_runner
        self.result_analyzer = result_analyzer

    def start_experiment(
        self,
        *,
        goal: str,
        project_name: str | None = None,
        variants: list[str] | None = None,
        benchmark_commands: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        primary_metric: str | None = None,
        metric_direction: str = "minimize",
        auto_approve: bool = True,
        max_iterations: int = 5,
        deep: bool = False,
    ) -> dict[str, Any]:
        target_project = project_name or self.orchestrator.get_active_project_name()
        if not target_project:
            raise RuntimeError("No active project selected. Use 'boss project <name>' first.")
        if str(target_project).startswith("__eval__"):
            raise RuntimeError(
                f"Active project '{target_project}' is a sandbox. Switch back to the source project before starting a lab experiment."
            )

        experiment_id = self._experiment_id(goal)
        benchmark_commands = list(benchmark_commands or [])
        allowed_paths = list(allowed_paths or [])

        self.registry.create_experiment(
            experiment_id=experiment_id,
            project_name=target_project,
            goal=goal,
            primary_metric=primary_metric,
            metric_direction=metric_direction,
            benchmark_commands=benchmark_commands,
            allowed_paths=allowed_paths,
            metadata={"deep": deep, "max_iterations": max_iterations},
        )

        definitions = self.variant_generator.generate(
            experiment_id=experiment_id,
            goal=goal,
            variants=variants,
            benchmark_commands=benchmark_commands,
            allowed_paths=allowed_paths,
            primary_metric=primary_metric,
            max_iterations=max_iterations,
        )

        results: list[dict[str, Any]] = []
        for definition in definitions:
            self.registry.add_variant(experiment_id, definition)
            result = self.benchmark_runner.run_variant(
                experiment_id=experiment_id,
                project_name=target_project,
                variant=definition,
                auto_approve=auto_approve,
                deep=deep,
            )
            self.registry.record_variant_result(
                definition.variant_id,
                status=result["status"],
                eval_run_id=result["eval_run_id"],
                runtime_seconds=result["runtime_seconds"],
                sandbox_project_name=result["sandbox_project_name"],
                sandbox_path=result["sandbox_path"],
                sandbox_mode=result["sandbox_mode"],
                branch_name=result["branch_name"],
                base_revision=result["base_revision"],
                changed_files=result["changed_files"],
                metrics=result["metrics"],
                output_summary=result["output_summary"],
                errors=result["errors"],
                metadata=result["metadata"],
            )
            results.append(result)

        recommendation = self.result_analyzer.analyze(
            primary_metric=primary_metric,
            metric_direction=metric_direction,
            variants=results,
        )
        status = "completed" if any(item.get("status") == "passed" for item in results) else "failed"
        self.registry.finalize_experiment(
            experiment_id,
            status=status,
            recommendation=recommendation,
            metadata={"variant_count": len(results)},
        )
        experiment = self.registry.experiment_with_variants(experiment_id)
        if experiment is None:
            raise RuntimeError(f"Experiment '{experiment_id}' did not persist correctly.")
        return experiment

    def list_experiments(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.registry.list_experiments(limit=limit)

    def experiment_results(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.registry.experiment_with_variants(experiment_id)
        if experiment is None:
            raise RuntimeError(f"Experiment '{experiment_id}' was not found.")
        return experiment

    def apply_variant(
        self,
        variant_id: str,
        *,
        auto_approve: bool = False,
        confirm_callback: Callable[[str], bool] | None = None,
        preview_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        variant = self.registry.variant_with_experiment(variant_id)
        if variant is None:
            raise RuntimeError(f"Variant '{variant_id}' was not found.")
        if variant.get("kind") != "candidate":
            raise RuntimeError("Only candidate variants can be applied.")
        sandbox_path = variant.get("sandbox_path")
        if not sandbox_path:
            raise RuntimeError(f"Variant '{variant_id}' has no preserved sandbox to apply from.")

        source_root = (self.root_dir / "projects" / str(variant["project_name"])).resolve()
        sandbox_root = Path(str(sandbox_path)).resolve()
        if not source_root.exists():
            raise RuntimeError(f"Source project '{variant['project_name']}' does not exist.")
        if not sandbox_root.exists():
            raise RuntimeError(f"Sandbox for variant '{variant_id}' no longer exists: {sandbox_root}")

        changed_files = [str(item) for item in variant.get("changed_files", []) if str(item).strip()]
        if not changed_files:
            raise RuntimeError(f"Variant '{variant_id}' has no recorded file changes to apply.")

        diff_preview = self._build_diff_preview(source_root, sandbox_root, changed_files)
        metrics = variant.get("metrics", {})
        metric_name = variant.get("primary_metric") or "runtime_seconds"
        metric_value = metrics.get(metric_name)
        preview_payload = {
            "applied": False,
            "variant_id": variant_id,
            "project_name": variant["project_name"],
            "metrics": metrics,
            "diff_preview": diff_preview,
            "applied_files": [],
            "skipped_files": [],
            "message": f"Previewing {variant_id} for project {variant['project_name']}.",
        }
        prompt = (
            f"Apply variant {variant_id} to project {variant['project_name']}? "
            f"Changed files: {len(changed_files)}. "
            f"{metric_name}: {metric_value if metric_value is not None else 'n/a'}."
        )
        if not auto_approve:
            if preview_callback is not None:
                preview_callback(preview_payload)
            if confirm_callback is None:
                raise RuntimeError("A confirmation callback is required unless auto_approve is enabled.")
            if not confirm_callback(prompt):
                return {
                    **preview_payload,
                    "message": "Apply cancelled.",
                }

        if variant.get("sandbox_mode") == "worktree" and variant.get("branch_name"):
            return self._apply_worktree_variant(
                variant=variant,
                source_root=source_root,
                sandbox_root=sandbox_root,
                diff_preview=diff_preview,
                metrics=metrics,
            )

        applied_files: list[str] = []
        skipped_files: list[str] = []
        for relative_path in changed_files:
            source_file = source_root / relative_path
            sandbox_file = sandbox_root / relative_path
            if not sandbox_file.exists():
                skipped_files.append(relative_path)
                continue
            source_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sandbox_file, source_file)
            applied_files.append(relative_path)

        self.registry.mark_variant_applied(
            variant_id,
            metadata={"applied_files": applied_files, "skipped_files": skipped_files},
        )
        return {
            "applied": True,
            "variant_id": variant_id,
            "project_name": variant["project_name"],
            "applied_files": applied_files,
            "skipped_files": skipped_files,
            "diff_preview": diff_preview,
            "metrics": metrics,
            "message": f"Applied {len(applied_files)} files from {variant_id}.",
        }

    def _apply_worktree_variant(
        self,
        *,
        variant: dict[str, Any],
        source_root: Path,
        sandbox_root: Path,
        diff_preview: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._git_is_clean(source_root):
            raise RuntimeError(f"Source repo '{source_root}' has uncommitted changes; refusing to cherry-pick into a dirty repo.")

        branch_name = str(variant.get("branch_name") or "").strip()
        base_revision = str(variant.get("base_revision") or "").strip()
        if not branch_name or not base_revision:
            raise RuntimeError("Worktree variant is missing branch metadata required for git apply.")

        commit_message = f"BOSS AEL apply {variant['variant_id']}"
        commit_revision = self._ensure_worktree_commit(
            sandbox_root=sandbox_root,
            branch_name=branch_name,
            base_revision=base_revision,
            commit_message=commit_message,
        )
        if commit_revision is None:
            raise RuntimeError(f"Variant '{variant['variant_id']}' has no committed changes to apply.")

        commit_revisions = self._git_stdout_lines(
            sandbox_root,
            ["rev-list", "--reverse", f"{base_revision}..{commit_revision}"],
        )
        if not commit_revisions:
            commit_revisions = [commit_revision]

        cherry_pick = subprocess.run(
            ["git", "cherry-pick", "-x", *commit_revisions],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if cherry_pick.returncode != 0:
            subprocess.run(
                ["git", "cherry-pick", "--abort"],
                cwd=source_root,
                capture_output=True,
                text=True,
                check=False,
            )
            message = cherry_pick.stderr.strip() or cherry_pick.stdout.strip() or "Cherry-pick failed."
            raise RuntimeError(f"Failed to apply worktree variant '{variant['variant_id']}': {message}")

        self.registry.mark_variant_applied(
            variant["variant_id"],
            metadata={
                "apply_method": "git_cherry_pick",
                "commit_revision": commit_revision,
                "commit_revisions": commit_revisions,
                "branch_name": branch_name,
            },
        )
        return {
            "applied": True,
            "variant_id": variant["variant_id"],
            "project_name": variant["project_name"],
            "applied_files": list(variant.get("changed_files", [])),
            "skipped_files": [],
            "diff_preview": diff_preview,
            "metrics": metrics,
            "apply_method": "git_cherry_pick",
            "commit_revision": commit_revision,
            "commit_revisions": commit_revisions,
            "message": f"Cherry-picked {len(commit_revisions)} commit(s) from {branch_name} ending at {commit_revision[:12]}.",
        }

    def _build_diff_preview(self, source_root: Path, sandbox_root: Path, changed_files: list[str], limit: int = 6) -> str:
        previews: list[str] = []
        for relative_path in changed_files[:limit]:
            source_file = source_root / relative_path
            sandbox_file = sandbox_root / relative_path
            before = self._read_text(source_file)
            after = self._read_text(sandbox_file)
            if before is None or after is None:
                previews.append(f"# {relative_path}\n(Binary or unreadable diff omitted)")
                continue
            diff = difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
            previews.append("\n".join(diff))
        if len(changed_files) > limit:
            previews.append(f"... {len(changed_files) - limit} more changed files omitted ...")
        return "\n\n".join(item for item in previews if item).strip()

    def _read_text(self, path: Path) -> str | None:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

    def _experiment_id(self, goal: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in goal).strip("-")
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        cleaned = cleaned[:40] or "experiment"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{cleaned}-{timestamp}"

    def _git_is_clean(self, root: Path) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and not result.stdout.strip()

    def _ensure_worktree_commit(
        self,
        *,
        sandbox_root: Path,
        branch_name: str,
        base_revision: str,
        commit_message: str,
    ) -> str | None:
        head_revision = self._git_stdout(sandbox_root, ["rev-parse", "HEAD"])
        if head_revision is None:
            raise RuntimeError(f"Unable to resolve HEAD for worktree '{sandbox_root}'.")

        if self._git_has_changes(sandbox_root):
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=sandbox_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if add_result.returncode != 0:
                raise RuntimeError(add_result.stderr.strip() or add_result.stdout.strip() or "Failed to stage worktree changes.")
            commit_result = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=BOSS",
                    "-c",
                    "user.email=boss@local",
                    "commit",
                    "-m",
                    commit_message,
                ],
                cwd=sandbox_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_result.returncode != 0:
                raise RuntimeError(commit_result.stderr.strip() or commit_result.stdout.strip() or "Failed to commit worktree changes.")
            head_revision = self._git_stdout(sandbox_root, ["rev-parse", "HEAD"])

        if head_revision == base_revision:
            return None
        merge_base = self._git_stdout(sandbox_root, ["merge-base", branch_name, base_revision])
        if merge_base is None:
            return head_revision
        return head_revision

    def _git_has_changes(self, root: Path) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Unable to inspect git status for {root}.")
        return bool(result.stdout.strip())

    def _git_stdout(self, root: Path, args: list[str]) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        return output or None

    def _git_stdout_lines(self, root: Path, args: list[str]) -> list[str]:
        output = self._git_stdout(root, args)
        if not output:
            return []
        return [line.strip() for line in output.splitlines() if line.strip()]
