from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from boss.agents import build_entry_agent
from boss.config import settings
from boss.control import (
    boss_control_status_payload,
    is_path_allowed_for_agent,
    jobs_branch_behavior,
    load_boss_control,
    memory_auto_approve_enabled,
    memory_auto_approve_min_confidence,
    resolve_request_mode,
)
from boss.context.manager import SessionContextManager
from boss.memory.distillation import distill_latest_turn
from boss.execution import AUTO_ALLOWED_EXECUTION_TYPES, get_tool_metadata
from boss.execution import (
    PendingApproval,
    PendingStatus,
    load_expired_pending_run,
    load_pending_run,
    save_pending_run,
)
from boss.memory import knowledge as knowledge_module
from boss.memory.injection import build_memory_injection
from boss.memory.knowledge import KnowledgeStore
from boss.jobs import (
    BackgroundJobStatus,
    append_background_job_log,
    create_background_job,
    list_background_jobs,
    load_background_job,
    prepare_task_branch,
    recover_interrupted_background_jobs,
    tail_background_job_log,
    update_background_job,
)
from boss.review import (
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
    collect_review_material,
    list_review_history,
    load_review_record,
    normalize_review_record,
    review_capabilities,
    save_review_record,
)
from boss.runtime import git_status_payload, runtime_status_payload, workspace_root
from boss.memory.scanner import full_scan
from boss.persistence.history import SessionState, save_session_state


@contextmanager
def override_settings(**overrides):
    originals = {key: getattr(settings, key) for key in overrides}
    try:
        for key, value in overrides.items():
            object.__setattr__(settings, key, value)
        yield
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)


@contextmanager
def isolated_knowledge_store(db_path: Path):
    original_store = knowledge_module._store
    store = KnowledgeStore(db_path)
    knowledge_module._store = store
    try:
        yield store
    finally:
        store.close()
        knowledge_module._store = original_store


def import_api_module():
    existing = sys.modules.get("boss.api")
    if existing is not None:
        return existing
    with patch("boss.runtime.ensure_api_server_lock", return_value={"pid": os.getpid()}):
        return importlib.import_module("boss.api")


class RegressionHarnessTests(unittest.TestCase):
    def test_default_agent_mode_keeps_full_governed_behavior(self):
        entry_agent = build_entry_agent()

        tool_names = [tool.name for tool in entry_agent.tools]
        self.assertIn("remember", tool_names)

        research_agent = next(agent for agent in entry_agent.handoffs if agent.name == "research")
        self.assertIn("web_search", [tool.name for tool in research_agent.tools])

    def test_ask_mode_filters_side_effect_tools(self):
        entry_agent = build_entry_agent(mode="ask")

        self.assertNotIn("remember", [tool.name for tool in entry_agent.tools])
        self.assertIn("recall", [tool.name for tool in entry_agent.tools])

        for agent in [entry_agent, *entry_agent.handoffs]:
            for tool in agent.tools:
                metadata = get_tool_metadata(tool.name)
                self.assertIsNotNone(metadata)
                self.assertIn(metadata.execution_type, AUTO_ALLOWED_EXECUTION_TYPES)

    def test_plan_mode_is_read_only_and_plan_oriented(self):
        entry_agent = build_entry_agent(mode="plan")

        self.assertIn("Goal, Execution Plan, Risks, Validation", entry_agent.instructions)
        self.assertNotIn("remember", [tool.name for tool in entry_agent.tools])

        research_agent = next(agent for agent in entry_agent.handoffs if agent.name == "research")
        self.assertEqual([tool.name for tool in research_agent.tools], [])

    def test_review_mode_is_read_only_and_findings_first(self):
        entry_agent = build_entry_agent(mode="review")

        self.assertIn("Do not fix code", entry_agent.instructions)
        self.assertNotIn("remember", [tool.name for tool in entry_agent.tools])

        code_agent = next(agent for agent in entry_agent.handoffs if agent.name == "code")
        self.assertIn("code reviewer", code_agent.instructions.lower())
        for tool in [*entry_agent.tools, *code_agent.tools]:
            metadata = get_tool_metadata(tool.name)
            self.assertIsNotNone(metadata)
            self.assertIn(metadata.execution_type, AUTO_ALLOWED_EXECUTION_TYPES)

    def test_mode_resolution_defaults_to_agent_for_invalid_explicit_mode(self):
        self.assertEqual(resolve_request_mode("hello", explicit_mode="something-unknown"), "agent")
        self.assertEqual(resolve_request_mode("hello", explicit_mode="agent"), "agent")

    def test_explicit_mode_override_beats_review_keyword_auto_switch(self):
        self.assertEqual(resolve_request_mode("please review this diff", explicit_mode="plan"), "plan")

    def test_boss_control_default_mode_applies_without_keyword_or_explicit_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".boss").mkdir()
            (root / ".boss" / "rules").mkdir()
            (root / ".boss" / "config.toml").write_text(
                "[mode]\ndefault = \"ask\"\n",
                encoding="utf-8",
            )

            self.assertEqual(resolve_request_mode("hello there", workspace_root=root), "ask")

    def test_boss_control_loader_reads_repo_native_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "BOSS.md").write_text("Top-level Boss instructions", encoding="utf-8")
            (root / ".boss").mkdir()
            (root / ".boss" / "rules").mkdir()
            (root / ".boss" / "config.toml").write_text(
                "[mode]\ndefault = \"review\"\n\n[memory]\nauto_injection = true\n",
                encoding="utf-8",
            )
            (root / ".boss" / "review.md").write_text("Findings first review guide", encoding="utf-8")
            (root / ".boss" / "environment.json").write_text(
                '{"platform": "macOS", "constraints": ["local validation only"]}',
                encoding="utf-8",
            )
            (root / ".boss" / "rules" / "00-core.md").write_text(
                "+++\ntitle = \"Core\"\ntargets = [\"all\"]\nmodes = [\"default\", \"review\"]\nalways = true\n+++\n\nAlways be additive.",
                encoding="utf-8",
            )
            (root / ".bossignore").write_text("secret.txt\n", encoding="utf-8")
            (root / ".bossindexignore").write_text("ignored.py\n", encoding="utf-8")

            control = load_boss_control(root, refresh=True)
            self.assertTrue(control.is_configured())
            self.assertEqual(control.config.default_mode, "review")
            self.assertEqual(control.rules[0].title, "Core")
            self.assertIn("Top-level Boss instructions", control.boss_md)
            self.assertEqual(control.environment.get("platform"), "macOS")

            status = boss_control_status_payload(root)
            self.assertTrue(status["configured"])
            self.assertEqual(status["default_mode"], "review")
            self.assertTrue(status["files"]["BOSS.md"]["exists"])

    def test_review_mode_and_instructions_use_boss_control(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "BOSS.md").write_text("Use Boss-native review behavior.", encoding="utf-8")
            (root / ".boss").mkdir()
            (root / ".boss" / "rules").mkdir()
            (root / ".boss" / "config.toml").write_text(
                "[review]\nauto_activate = true\nauto_mode_keywords = [\"audit\"]\n",
                encoding="utf-8",
            )
            (root / ".boss" / "review.md").write_text("List findings before summaries.", encoding="utf-8")
            (root / ".boss" / "rules" / "00-core.md").write_text(
                "+++\ntitle = \"Core\"\ntargets = [\"all\"]\nmodes = [\"default\", \"review\"]\nalways = true\n+++\n\nAlways keep changes incremental.",
                encoding="utf-8",
            )
            (root / ".boss" / "rules" / "30-review-mode.md").write_text(
                "+++\ntitle = \"Review Mode\"\ntargets = [\"general\"]\nmodes = [\"review\"]\n+++\n\nStart with findings.",
                encoding="utf-8",
            )

            mode = resolve_request_mode("please audit this codebase", workspace_root=root)
            self.assertEqual(mode, "review")

            agent = build_entry_agent(mode=mode, workspace_root=root)
            self.assertIn("Use Boss-native review behavior.", agent.instructions)
            self.assertIn("List findings before summaries.", agent.instructions)
            self.assertIn("Start with findings.", agent.instructions)

    def test_review_findings_are_normalized_and_sorted_by_severity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "sample.py"
            file_path.write_text("def compute():\n    return 1\n", encoding="utf-8")

            material = collect_review_material(
                ReviewRequest(target="files", project_path=str(root), file_paths=(str(file_path),))
            )
            report = ReviewReport(
                findings=[
                    ReviewFinding(
                        severity="low",
                        file_path="sample.py",
                        evidence="Return value is hard-coded.",
                        risk="Low risk if callers already tolerate it.",
                        recommended_fix="Make the value configurable.",
                    ),
                    ReviewFinding(
                        severity="high",
                        file_path="sample.py",
                        evidence="No guard exists for invalid input.",
                        risk="High risk of runtime failure on bad input.",
                        recommended_fix="Validate arguments before use.",
                    ),
                ]
            )

            normalized = normalize_review_record(report, material)
            self.assertEqual([finding.severity for finding in normalized.findings], ["high", "low"])
            self.assertEqual(normalized.findings[0].file_path, "sample.py")
            self.assertTrue(normalized.summary)

    def test_review_git_working_tree_flow_prefers_diff_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tracked = root / "module.py"
            tracked.write_text("def compute():\n    return 1\n", encoding="utf-8")

            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Boss Tests"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "module.py"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)

            tracked.write_text("def compute(flag):\n    if flag:\n        return 2\n    return 1\n", encoding="utf-8")

            capabilities = review_capabilities(str(root))
            material = collect_review_material(ReviewRequest(target="working_tree", project_path=str(root)))

            self.assertTrue(capabilities["git_available"])
            self.assertEqual(material.target_kind, "working_tree")
            self.assertIn("module.py", material.changed_files)
            self.assertIn("return 2", material.diff_text)

    def test_review_non_git_auto_falls_back_to_project_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "knowledge.sqlite"
            with isolated_knowledge_store(db_path):
                store = knowledge_module.get_knowledge_store()
                store.upsert_project(
                    path=str(root),
                    name="review-fixture",
                    project_type="python",
                    metadata={
                        "stack": ["Python"],
                        "entry_points": ["main.py"],
                        "useful_commands": ["python main.py"],
                    },
                )

                capabilities = review_capabilities(str(root))
                material = collect_review_material(ReviewRequest(target="auto", project_path=str(root)))

                self.assertFalse(capabilities["git_available"])
                self.assertEqual(capabilities["default_target"], "project_summary")
                self.assertEqual(material.target_kind, "project_summary")
                self.assertTrue(material.project_summaries)

    def test_review_history_round_trip_persists_normalized_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "reviews"
            file_path = root / "module.py"
            file_path.write_text("def run():\n    return 1\n", encoding="utf-8")

            material = collect_review_material(
                ReviewRequest(target="files", project_path=str(root), file_paths=(str(file_path),))
            )
            report = ReviewReport(
                summary="One issue found.",
                residual_risk="No additional residual risk.",
                findings=[
                    ReviewFinding(
                        severity="medium",
                        file_path="module.py",
                        evidence="The return value is hard-coded.",
                        risk="Medium risk if callers expect a configurable result.",
                        recommended_fix="Read the value from configuration.",
                    )
                ],
            )
            record = normalize_review_record(report, material)

            with override_settings(review_history_dir=review_dir):
                save_review_record(record)
                loaded = load_review_record(record.review_id)
                history = list_review_history(limit=5)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.review_id, record.review_id)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].findings[0].file_path, "module.py")

    def test_memory_governance_config_reads_auto_approve_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".boss").mkdir()
            (root / ".boss" / "rules").mkdir()
            (root / ".boss" / "config.toml").write_text(
                "[memory]\nauto_approve = true\nauto_approve_min_confidence = 0.91\n",
                encoding="utf-8",
            )

            self.assertTrue(memory_auto_approve_enabled(root))
            self.assertAlmostEqual(memory_auto_approve_min_confidence(root), 0.91)

    def test_jobs_config_reads_branch_behavior(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".boss").mkdir()
            (root / ".boss" / "rules").mkdir()
            (root / ".boss" / "config.toml").write_text(
                "[jobs]\nbranch_behavior = \"create\"\ntakeover_cancels_background = false\n",
                encoding="utf-8",
            )

            self.assertEqual(jobs_branch_behavior(root), "create")

    def test_background_job_round_trip_and_log_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            logs_dir = root / "job-logs"

            with override_settings(jobs_dir=jobs_dir, job_logs_dir=logs_dir):
                record = create_background_job(
                    prompt="Inspect the latest local changes",
                    mode="agent",
                    session_id="session-job-1",
                    project_path=str(root),
                    initial_input_kind="prepared_input",
                    initial_input_payload=[{"role": "user", "content": "Inspect the latest local changes"}],
                    branch_mode="suggest",
                    branch_name="boss/inspect-latest-local-changes",
                    task_slug="inspect-latest-local-changes",
                    branch_status="suggested",
                    branch_message="Suggested task branch: boss/inspect-latest-local-changes",
                    branch_helper_path=str(root / "scripts" / "task_branch.sh"),
                )
                append_background_job_log(
                    record.job_id,
                    event_type="text",
                    message="Reviewing the changed files.",
                )

                loaded = load_background_job(record.job_id)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded.status, BackgroundJobStatus.QUEUED.value)

                listed = list_background_jobs(limit=10)
                self.assertEqual(len(listed), 1)
                tail = tail_background_job_log(record.job_id, limit=10)
                self.assertIn("Reviewing the changed files.", tail["text"])
                self.assertEqual(tail["entries"][-1]["type"], "text")

    def test_recover_interrupted_background_jobs_marks_running_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            logs_dir = root / "job-logs"

            with override_settings(jobs_dir=jobs_dir, job_logs_dir=logs_dir):
                record = create_background_job(
                    prompt="Long running task",
                    mode="agent",
                    session_id="session-job-2",
                    project_path=str(root),
                    initial_input_kind="prepared_input",
                    initial_input_payload=[{"role": "user", "content": "Long running task"}],
                )
                update_background_job(record.job_id, status=BackgroundJobStatus.RUNNING.value)

                recovered = recover_interrupted_background_jobs()
                self.assertEqual(recovered, 1)

                loaded = load_background_job(record.job_id)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded.status, BackgroundJobStatus.INTERRUPTED.value)
                self.assertIn("Resume to continue", loaded.error_message or "")

    def test_prepare_task_branch_suggests_branch_for_git_repo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Boss Tests"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=root, check=True, capture_output=True)
            (root / "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)

            branch = prepare_task_branch(
                prompt="Refactor the background job detail view",
                project_path=str(root),
                branch_mode="suggest",
            )

            self.assertEqual(branch["branch_status"], "suggested")
            self.assertEqual(branch["branch_name"], "boss/refactor-background-job-detail-view")

    def test_background_job_updates_preserve_resume_and_terminal_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            logs_dir = root / "job-logs"

            with override_settings(jobs_dir=jobs_dir, job_logs_dir=logs_dir):
                record = create_background_job(
                    prompt="Finish the checklist",
                    mode="agent",
                    session_id="session-job-3",
                    project_path=str(root),
                    initial_input_kind="prepared_input",
                    initial_input_payload=[{"role": "user", "content": "Finish the checklist"}],
                )
                waiting = update_background_job(
                    record.job_id,
                    status=BackgroundJobStatus.WAITING_PERMISSION.value,
                    pending_run_id="pending-job-3",
                    resume_count=1,
                )
                completed = update_background_job(
                    record.job_id,
                    status=BackgroundJobStatus.COMPLETED.value,
                    pending_run_id=None,
                    session_persisted=True,
                    finished_at="2026-04-08T00:00:00+00:00",
                )

                self.assertEqual(waiting.resume_count, 1)
                self.assertEqual(completed.status, BackgroundJobStatus.COMPLETED.value)
                self.assertTrue(completed.session_persisted)

    def test_takeover_clears_pending_run_for_waiting_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "jobs"
            logs_dir = root / "job-logs"
            pending_dir = root / "pending-runs"
            history_dir = root / "history"

            with override_settings(
                jobs_dir=jobs_dir,
                job_logs_dir=logs_dir,
                pending_runs_dir=pending_dir,
                history_dir=history_dir,
            ):
                for cancels_background in (True, False):
                    session_id = f"session-job-takeover-{int(cancels_background)}"
                    job = create_background_job(
                        prompt="Wait for approval",
                        mode="agent",
                        session_id=session_id,
                        project_path=str(root),
                        initial_input_kind="prepared_input",
                        initial_input_payload=[{"role": "user", "content": "Wait for approval"}],
                    )
                    approval = PendingApproval(
                        approval_id=f"approval-{job.job_id}",
                        tool_name="run_command",
                        title="Run command",
                        description="Needs approval",
                        execution_type="run",
                        scope_key=f"command:{job.job_id}",
                        scope_label="Terminal command",
                        requested_at=1_700_000_000.0,
                    )
                    run_id = save_pending_run(
                        session_id=session_id,
                        state={"job_id": job.job_id},
                        approvals=[approval],
                        run_id=f"run-{job.job_id}",
                    )
                    save_session_state(SessionState(session_id=session_id, recent_items=[], total_turns=0))
                    update_background_job(
                        job.job_id,
                        status=BackgroundJobStatus.WAITING_PERMISSION.value,
                        pending_run_id=run_id,
                    )

                    with self.subTest(cancels_background=cancels_background):
                        api = import_api_module()
                        if cancels_background:
                            payload = asyncio.run(api.takeover_job_endpoint(job.job_id))
                        else:
                            with patch("boss.api.jobs_takeover_cancels_background", return_value=False):
                                payload = asyncio.run(api.takeover_job_endpoint(job.job_id))

                        current = load_background_job(job.job_id)
                        self.assertIsNotNone(current)
                        assert current is not None
                        self.assertEqual(current.status, BackgroundJobStatus.TAKEN_OVER.value)
                        self.assertIsNone(current.pending_run_id)
                        self.assertEqual(payload["job"]["status"], BackgroundJobStatus.TAKEN_OVER.value)
                        self.assertIsNone(payload["job"]["pending_run_id"])
                        self.assertIsNone(load_pending_run(run_id))
                        self.assertFalse((pending_dir / f"{run_id}.json").exists())

    def test_git_status_payload_summarizes_clean_repo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tracked = root / "README.md"
            tracked.write_text("hello\n", encoding="utf-8")

            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Boss Tests"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "boss@example.com"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)

            git = git_status_payload(root)
            self.assertTrue(git["available"])
            self.assertTrue(git["is_repo"])
            self.assertTrue(git["clean"])
            self.assertIn("clean", git["summary"])

    def test_runtime_status_payload_reports_git_and_clean_lock_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_file = Path(temp_dir) / "api.lock"
            payload = {
                "pid": os.getpid(),
                "port": settings.api_port,
                "started_at": 1_700_000_000.0,
                "ready_at": 1_700_000_010.0,
                "status": "running",
                "workspace_path": str(workspace_root()),
                "current_working_directory": str(workspace_root()),
                "interpreter_path": sys.executable,
                "app_version": "test",
                "build_marker": "test-build",
            }
            lock_file.write_text(json.dumps(payload), encoding="utf-8")

            with override_settings(api_lock_file=lock_file), \
                 patch("boss.runtime._local_port_is_in_use", return_value=True), \
                 patch("boss.runtime._listeners_for_local_port", return_value=[os.getpid()]), \
                 patch("boss.runtime._process_snapshot", return_value={
                     "cwd": str(workspace_root()),
                     "executable": sys.executable,
                     "command": sys.executable,
                 }):
                status = runtime_status_payload()

            self.assertIn("git", status)
            self.assertIn("summary", status["git"])
            self.assertIn("boss_control", status)
            self.assertEqual(status["runtime_trust"]["warnings"], [])

    def test_bossignore_blocks_agent_access_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "BOSS.md").write_text("Boss", encoding="utf-8")
            (root / ".bossignore").write_text("secret.txt\n", encoding="utf-8")
            secret = root / "secret.txt"
            public = root / "notes.txt"
            secret.write_text("do not expose", encoding="utf-8")
            public.write_text("okay to read", encoding="utf-8")

            self.assertFalse(is_path_allowed_for_agent(secret))
            self.assertTrue(is_path_allowed_for_agent(public))

    def test_entry_agent_uses_general_entrypoint_and_expected_handoffs(self):
        entry_agent = build_entry_agent(active_mcp_servers={})

        self.assertEqual(entry_agent.name, "general")
        self.assertEqual([agent.name for agent in entry_agent.handoffs], ["mac", "research", "reasoning", "code"])

        tool_names = [tool.name for tool in entry_agent.tools]
        self.assertIn("remember", tool_names)
        self.assertIn("recall", tool_names)
        self.assertIn("search_project_content", tool_names)

    def test_memory_round_trip_and_injection_relevance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "knowledge.sqlite"
            with isolated_knowledge_store(db_path), override_settings(auto_memory_enabled=True):
                store = knowledge_module.get_knowledge_store()
                preference = store.upsert_durable_memory(
                    memory_kind="preference",
                    category="preference",
                    key="response_style",
                    value="Prefer concise technical answers with no fluff.",
                    tags=["style", "response"],
                    source="test",
                )
                store.upsert_durable_memory(
                    memory_kind="user_profile",
                    category="user",
                    key="editor",
                    value="VS Code on macOS",
                    tags=["editor"],
                    source="test",
                )

                listed = store.list_durable_memories(memory_kind="preference")
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0].key, "response_style")

                injection = build_memory_injection(
                    user_message="Keep the reply concise and technical for this change.",
                )
                self.assertTrue(any(result.key == "response_style" for result in injection.results))
                self.assertIn("Prefer concise technical answers", injection.text)

                self.assertTrue(store.delete_durable_memory(preference.id))

                after_delete = build_memory_injection(
                    user_message="Keep the reply concise and technical for this change.",
                )
                self.assertFalse(any(result.key == "response_style" for result in after_delete.results))

    def test_pending_memory_candidate_is_session_scoped_until_approved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "knowledge.sqlite"
            with isolated_knowledge_store(db_path), override_settings(auto_memory_enabled=True):
                store = knowledge_module.get_knowledge_store()
                candidate = store.queue_memory_candidate(
                    session_id="session-a",
                    memory_kind="preference",
                    category="preference",
                    key="reply_style",
                    value="Prefer terse daily summaries.",
                    source="test",
                )

                same_session = build_memory_injection(
                    user_message="Give me a terse daily summary.",
                    session_id="session-a",
                )
                other_session = build_memory_injection(
                    user_message="Give me a terse daily summary.",
                    session_id="session-b",
                )

                self.assertTrue(any(result.source_table == "memory_candidates" for result in same_session.results))
                self.assertFalse(any(result.source_table == "memory_candidates" for result in other_session.results))

                store.approve_memory_candidate(candidate.id)
                approved = build_memory_injection(
                    user_message="Give me a terse daily summary.",
                    session_id="session-b",
                )
                self.assertTrue(any(result.source_table == "durable_memories" for result in approved.results))

    def test_auto_distilled_memory_requires_approval_for_cross_session_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "knowledge.sqlite"
            with isolated_knowledge_store(db_path), override_settings(auto_memory_enabled=True):
                store = knowledge_module.get_knowledge_store()
                distill_latest_turn(
                    session_id="session-a",
                    session_summary="",
                    recent_items=[
                        {"role": "user", "type": "message", "content": "I prefer concise technical answers."},
                        {"role": "assistant", "type": "message", "content": "Understood."},
                    ],
                )

                pending = store.list_memory_candidates(status="pending")
                self.assertEqual(len(pending), 1)
                self.assertEqual(store.list_durable_memories(memory_kind="preference"), [])

                same_session = build_memory_injection(
                    user_message="Keep the response concise and technical.",
                    session_id="session-a",
                )
                self.assertTrue(any(result.source_table == "memory_candidates" for result in same_session.results))

                other_session = build_memory_injection(
                    user_message="Keep the response concise and technical.",
                    session_id="session-b",
                )
                self.assertFalse(any(result.key == "response_style" for result in other_session.results))

                approved = store.approve_memory_candidate(pending[0].id)
                self.assertEqual(approved.memory_kind, "preference")

                cross_session = build_memory_injection(
                    user_message="Keep the response concise and technical.",
                )
                self.assertTrue(any(result.source_table == "durable_memories" for result in cross_session.results))

    def test_full_scan_generates_project_summary_and_entry_points(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "sample_app"
            package_dir = project / "sample_app"
            package_dir.mkdir(parents=True)
            (project / "pyproject.toml").write_text(
                "[project]\nname = \"sample-app\"\ndescription = \"Scanner fixture\"\n",
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n",
                encoding="utf-8",
            )
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "api.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n",
                encoding="utf-8",
            )

            db_path = root / "knowledge.sqlite"
            with isolated_knowledge_store(db_path), override_settings(
                project_scan_roots=(root,),
                project_scan_discovery_depth=2,
                project_scan_max_files_per_project=100,
                project_scan_summary_file_limit=40,
            ):
                store = knowledge_module.get_knowledge_store()
                result = full_scan(store=store)

                self.assertEqual(result["projects_found"], 1)
                self.assertEqual(result["projects_updated"], 1)
                self.assertEqual(result["summaries_refreshed"], 1)
                self.assertGreaterEqual(result["files_indexed"], 3)

                projects = store.list_projects()
                self.assertEqual(len(projects), 1)
                metadata = projects[0].metadata
                self.assertIn("Python", metadata.get("stack", []))
                self.assertIn("FastAPI", metadata.get("stack", []))
                self.assertIn("main.py", metadata.get("entry_points", []))

                summaries = store.list_project_summary_notes(limit=5)
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0].note_key, "overview")
                self.assertIn("Likely entry points", summaries[0].body)

    def test_full_scan_respects_bossindexignore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "sample_app"
            package_dir = project / "sample_app"
            package_dir.mkdir(parents=True)
            (project / "pyproject.toml").write_text(
                "[project]\nname = \"sample-app\"\ndescription = \"Scanner fixture\"\n",
                encoding="utf-8",
            )
            (project / ".bossindexignore").write_text("ignored.py\n", encoding="utf-8")
            (project / "main.py").write_text("print('main')\n", encoding="utf-8")
            (project / "ignored.py").write_text("print('ignore me')\n", encoding="utf-8")
            (package_dir / "__init__.py").write_text("", encoding="utf-8")

            db_path = root / "knowledge.sqlite"
            with isolated_knowledge_store(db_path), override_settings(
                project_scan_roots=(root,),
                project_scan_discovery_depth=2,
                project_scan_max_files_per_project=100,
                project_scan_summary_file_limit=40,
            ):
                store = knowledge_module.get_knowledge_store()
                result = full_scan(store=store)

                self.assertEqual(result["projects_found"], 1)
                projects = store.list_projects()
                self.assertEqual(len(projects), 1)
                metadata = projects[0].metadata
                self.assertIn("boss_control", metadata)

                indexed_paths = store.get_project_file_index(projects[0].id)
                self.assertIn(str(project / "main.py"), indexed_paths)
                self.assertNotIn(str(project / "ignored.py"), indexed_paths)

    def test_pending_run_round_trip_and_expiry_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_dir = Path(temp_dir) / "pending_runs"
            requested_at = 1_700_000_000.0
            approval = PendingApproval(
                approval_id="approval-1",
                tool_name="run_applescript",
                title="Run AppleScript",
                description="Run a scripted action",
                execution_type="run",
                scope_key="applescript:any:test",
                scope_label="AppleScript",
                requested_at=requested_at,
            )

            with override_settings(pending_runs_dir=pending_dir):
                with patch("boss.execution.time.time", return_value=requested_at):
                    run_id = save_pending_run(
                        session_id="session-1",
                        state={"step": "waiting"},
                        approvals=[approval],
                        run_id="run-1",
                    )

                with patch("boss.execution.time.time", return_value=requested_at):
                    record = load_pending_run(run_id)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.status, PendingStatus.PENDING.value)
                self.assertEqual(len(record.approvals), 1)

                expired_at = requested_at + settings.pending_run_expiration_seconds + 5
                with patch("boss.execution.time.time", return_value=expired_at):
                    self.assertIsNone(load_pending_run(run_id))

                archived = load_expired_pending_run(run_id)
                self.assertIsNotNone(archived)
                assert archived is not None
                self.assertEqual(archived.status, PendingStatus.EXPIRED.value)
                self.assertEqual(archived.approvals[0].status, PendingStatus.EXPIRED.value)

    def test_preview_memory_injection_does_not_modify_session_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            db_path = root / "knowledge.sqlite"
            session_id = "preview-read-only"
            recent_items = [
                {"role": "developer", "type": "message", "content": "BOSS_CONTEXT:memory\nignore me"},
                {"role": "user", "type": "message", "content": "Turn one request"},
                {"role": "assistant", "type": "message", "content": "Turn one answer"},
                {"role": "user", "type": "message", "content": "Turn two request"},
                {"role": "assistant", "type": "message", "content": "Turn two answer"},
                {"role": "user", "type": "message", "content": "Turn three request"},
                {"role": "assistant", "type": "message", "content": "Turn three answer"},
            ]

            with isolated_knowledge_store(db_path), override_settings(
                history_dir=history_dir,
                session_summary_threshold=2,
                session_max_recent_turns=2,
                session_max_serialized_size=512,
                auto_memory_enabled=False,
            ):
                save_session_state(
                    SessionState(
                        session_id=session_id,
                        recent_items=recent_items,
                        total_turns=3,
                    )
                )
                session_path = history_dir / f"{session_id}.json"
                before = session_path.read_text(encoding="utf-8")

                manager = SessionContextManager()
                manager.preview_memory_injection(session_id, "Preview this memory context")

                after = session_path.read_text(encoding="utf-8")
                self.assertEqual(after, before)

    def test_preview_memory_injection_does_not_mutate_conversation_episodes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            db_path = root / "knowledge.sqlite"
            unchanged_timestamp = "2026-01-01T00:00:00+00:00"

            with isolated_knowledge_store(db_path), override_settings(
                history_dir=history_dir,
                auto_memory_enabled=False,
            ):
                store = knowledge_module.get_knowledge_store()
                store.store_conversation_episode(
                    session_id="preview-delete-guard",
                    title="Keep existing episode",
                    summary="Existing summary should survive preview.",
                    source="test",
                    created_at=unchanged_timestamp,
                    updated_at=unchanged_timestamp,
                    last_used_at=unchanged_timestamp,
                )
                store.store_conversation_episode(
                    session_id="preview-update-guard",
                    title="Existing summary",
                    summary="Episode should not be updated during preview.",
                    source="test",
                    created_at=unchanged_timestamp,
                    updated_at=unchanged_timestamp,
                    last_used_at=unchanged_timestamp,
                )

                save_session_state(
                    SessionState(
                        session_id="preview-delete-guard",
                        summary="",
                        recent_items=[{"role": "user", "type": "message", "content": "No summary here"}],
                        total_turns=1,
                    )
                )
                save_session_state(
                    SessionState(
                        session_id="preview-update-guard",
                        summary="New summary from session file",
                        recent_items=[{"role": "user", "type": "message", "content": "Keep this read-only"}],
                        total_turns=1,
                    )
                )
                save_session_state(
                    SessionState(
                        session_id="preview-create-guard",
                        summary="Would have created an episode before this fix",
                        recent_items=[{"role": "user", "type": "message", "content": "Do not create episode"}],
                        total_turns=1,
                    )
                )

                manager = SessionContextManager()
                manager.preview_memory_injection("preview-delete-guard", "Preview only")
                manager.preview_memory_injection("preview-update-guard", "Preview only")
                manager.preview_memory_injection("preview-create-guard", "Preview only")

                episodes = {episode.session_id: episode for episode in store.list_conversation_episodes(limit=10)}
                self.assertEqual(len(episodes), 2)
                self.assertIn("preview-delete-guard", episodes)
                self.assertEqual(
                    episodes["preview-delete-guard"].summary,
                    "Existing summary should survive preview.",
                )
                self.assertIn("preview-update-guard", episodes)
                self.assertEqual(
                    episodes["preview-update-guard"].summary,
                    "Episode should not be updated during preview.",
                )
                self.assertEqual(episodes["preview-update-guard"].updated_at, unchanged_timestamp)
                self.assertNotIn("preview-create-guard", episodes)

    def test_memory_overview_preview_does_not_touch_last_used_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            db_path = root / "knowledge.sqlite"
            unchanged_timestamp = "2026-01-01T00:00:00+00:00"

            with isolated_knowledge_store(db_path), override_settings(
                history_dir=history_dir,
                auto_memory_enabled=True,
            ):
                store = knowledge_module.get_knowledge_store()
                durable = store.upsert_durable_memory(
                    memory_kind="workflow",
                    category="workflow",
                    key="response_style",
                    value="Prefer concise technical answers.",
                    tags=["style"],
                    source="test",
                    created_at=unchanged_timestamp,
                    updated_at=unchanged_timestamp,
                    last_used_at=unchanged_timestamp,
                )
                candidate = store.queue_memory_candidate(
                    session_id="session-a",
                    memory_kind="preference",
                    category="preference",
                    key="summary_style",
                    value="Prefer terse daily summaries.",
                    source="test",
                )
                store._conn.execute(
                    "UPDATE memory_candidates SET last_used_at = ?, updated_at = ? WHERE id = ?",
                    (unchanged_timestamp, unchanged_timestamp, candidate.id),
                )
                store._conn.commit()

                save_session_state(
                    SessionState(
                        session_id="session-a",
                        recent_items=[{"role": "user", "type": "message", "content": "Preview message"}],
                        total_turns=1,
                    )
                )

                api = import_api_module()
                session_payload = api._memory_overview_payload(
                    session_id="session-a",
                    message="Use concise technical answers and terse daily summaries.",
                )
                stateless_payload = api._memory_overview_payload(
                    message="Use concise technical answers.",
                )

                self.assertGreaterEqual(len(session_payload["current_turn_memory"]["reasons"]), 1)
                self.assertGreaterEqual(len(stateless_payload["current_turn_memory"]["reasons"]), 1)

                refreshed_durable = store.get_durable_memory(durable.id)
                refreshed_candidate = store.get_memory_candidate(candidate.id)
                self.assertIsNotNone(refreshed_durable)
                self.assertIsNotNone(refreshed_candidate)
                assert refreshed_durable is not None
                assert refreshed_candidate is not None
                self.assertEqual(refreshed_durable.last_used_at, unchanged_timestamp)
                self.assertEqual(refreshed_candidate.last_used_at, unchanged_timestamp)

    def test_persist_result_still_compacts_and_syncs_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "history"
            db_path = root / "knowledge.sqlite"
            session_id = "session-compaction"
            run_history = [
                {"role": "developer", "type": "message", "content": "BOSS_CONTEXT:memory\nignore me"},
                {"role": "user", "type": "message", "content": "Turn one request"},
                {"role": "assistant", "type": "message", "content": "Turn one answer"},
                {"role": "user", "type": "message", "content": "Turn two request"},
                {"role": "assistant", "type": "message", "content": "Turn two answer"},
                {"role": "user", "type": "message", "content": "Turn three request"},
                {"role": "assistant", "type": "message", "content": "Turn three answer"},
                {"role": "user", "type": "message", "content": "Turn four request"},
                {"role": "assistant", "type": "message", "content": "Turn four answer"},
            ]

            with isolated_knowledge_store(db_path), override_settings(
                history_dir=history_dir,
                session_summary_threshold=2,
                session_max_recent_turns=2,
                session_max_serialized_size=512,
                auto_memory_enabled=False,
            ):
                manager = SessionContextManager()
                compacted = manager.persist_result(session_id, run_history)

                self.assertEqual(compacted.archived_turns, 2)
                self.assertEqual(sum(1 for item in compacted.recent_items if item.get("role") == "user"), 2)
                self.assertFalse(any(item.get("role") == "developer" for item in compacted.recent_items))
                self.assertIn("Turn one request", compacted.summary)
                self.assertTrue((history_dir / f"{session_id}.json").exists())

                store = knowledge_module.get_knowledge_store()
                episodes = store.list_conversation_episodes(limit=5)
                self.assertEqual(len(episodes), 1)
                self.assertEqual(episodes[0].session_id, session_id)
                self.assertEqual(episodes[0].summary, compacted.summary)


if __name__ == "__main__":
    unittest.main()