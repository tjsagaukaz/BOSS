from __future__ import annotations

from pathlib import Path

from boss.runtime import ContextEnvelopeBuilder
from boss.types import (
    CodeSummary,
    IndexedFile,
    MemoryEntry,
    ProjectBrain,
    ProjectContext,
    ProjectMap,
    ProjectMemoryProfile,
    SolutionEntry,
    StyleProfile,
    ToolDefinition,
    WorkspaceState,
)


def _project_context() -> ProjectContext:
    return ProjectContext(
        name="demo",
        root=Path("/tmp/demo"),
        summary="Demo API project",
        file_count=42,
        languages={"Python": 30, "Markdown": 2},
        important_files=["app.py", "auth.py", "tests/test_auth.py", "README.md"],
        architecture_notes=["Auth flows through auth.py before router dispatch."],
        memory_entries=[MemoryEntry(category="decision", content="Prefer pytest fixtures.", created_at="now")],
        code_summaries=[CodeSummary(file_path="auth.py", language="Python", summary="Auth helpers.", updated_at="now")],
        project_map=ProjectMap(
            name="demo",
            overview="Fast API-style project",
            languages={"Python": 30},
            main_modules=["api/", "auth/"],
            entry_points=["app.py"],
            key_files=["app.py", "auth.py"],
            dependencies=["fastapi", "pytest"],
        ),
        relevant_files=[
            IndexedFile(
                file_path=f"module_{index}.py",
                language="Python",
                content_hash=str(index),
                size=100,
                modified_at="now",
                summary=f"Summary {index}",
                purpose=f"Purpose {index}",
                symbols=[f"symbol_{index}"],
                dependencies=["dep"],
                updated_at="now",
            )
            for index in range(8)
        ],
        semantic_results=[],
        relevant_memories=[],
        active_file="auth.py",
        recent_files=["auth.py", "app.py"],
        recent_changes=[{"file": "auth.py", "type": "edit", "summary": "Added token helper"}],
        recent_searches=[],
        workspace_state=WorkspaceState(
            active_project="demo",
            open_files=["auth.py", "app.py"],
            recent_edits=[{"file": "auth.py", "type": "edit", "summary": "Added token helper"}],
            recent_terminal_commands=[{"command": "pytest", "exit_code": 1}],
            last_terminal_command="pytest",
            last_test_results={"passed": False, "failure_summary": "test_auth failed", "failed_tests": ["test_auth"]},
            last_git_diff="auth.py | 4 ++--",
            last_git_status={"summary": " M auth.py", "dirty": True},
            updated_at="now",
        ),
        project_profile=ProjectMemoryProfile(
            project_name="demo",
            description="Demo service",
            primary_language="Python",
            frameworks=["FastAPI"],
            architecture="layered api",
            key_modules=["auth", "api"],
        ),
        project_brain=ProjectBrain(
            project_name="demo",
            mission="Build a reliable API runtime",
            current_focus="Reliability hardening",
            architecture=["auth/", "api/", "app.py"],
            brain_rules=["Never use Flask for new APIs.", "Prefer FastAPI patterns for HTTP services."],
            recent_progress=["Deterministic evaluation added"],
            open_problems=["Parallel run graph not enabled"],
            next_priorities=["Enable controlled parallel graph execution"],
        ),
        style_profile=StyleProfile(
            project_name="demo",
            indentation="4 spaces",
            naming_conventions=["snake_case"],
            code_structure="small modules",
            test_style="pytest",
            error_handling_style="raise HTTP exceptions",
            notes=["type hints required"],
        ),
        relevant_solutions=[
            SolutionEntry(
                solution_id=1,
                title=f"Solution {index}",
                description="Reusable auth solution",
                code_snippet="def solve(): pass",
                tags=["auth"],
                projects=["demo"],
            )
            for index in range(5)
        ],
        similar_tasks=[
            {"project_name": "demo", "task": f"Task {index}", "status": "completed", "score": 0.9, "final_result": "ok"}
            for index in range(5)
        ],
        graph_insights=["auth.py depends on token validation helpers."],
        related_projects=[],
    )


def _tool(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=description, input_schema={"type": "object"}, handler=lambda _: None)


def test_context_envelope_uses_fixed_section_order():
    builder = ContextEnvelopeBuilder()
    envelope = builder.build(
        role="engineer",
        task="Add auth middleware",
        project_context=_project_context(),
        tools=[_tool("read_file", "Read a file"), _tool("write_file", "Write a file")],
        supplemental_context="Architect Plan:\nImplement auth middleware in auth.py",
        task_contract={"goal": "Add auth middleware", "allowed_paths": ["auth.py"]},
        execution_spine={
            "task_id": "42",
            "goal": "Add auth middleware",
            "current_step_number": 1,
            "total_steps": 2,
            "completed_steps": 0,
            "steps_remaining": 1,
            "step": {
                "id": "S1",
                "title": "Create middleware",
                "goal": "Create auth middleware",
                "allowed_paths": ["auth.py"],
                "expected_outputs": ["auth.py"],
                "validation": ["tests_pass"],
                "validation_commands": ["pytest tests/test_auth.py"],
                "status": "running",
                "attempts": 1,
            },
        },
    ).render()

    expected_headers = [
        "=== TASK CONTRACT ===",
        "=== EXECUTION RULES ===",
        "=== PROJECT BRAIN ===",
        "=== EXECUTION SPINE ===",
        "=== WORKSPACE STATE ===",
        "=== PROJECT INTELLIGENCE ===",
        "=== ARCHITECTURE MAP ===",
        "=== RELEVANT FILES ===",
        "=== SOLUTION LIBRARY MATCHES ===",
        "=== STYLE PROFILE ===",
        "=== TOOL CONTRACTS ===",
    ]
    positions = [envelope.index(header) for header in expected_headers]
    assert positions == sorted(positions)
    assert "Allowed Paths: auth.py" in envelope
    assert "Current Focus: Reliability hardening" in envelope
    assert "Brain Rules:\n- Never use Flask for new APIs." in envelope
    assert "Project Brain Rule: Never use Flask for new APIs." in envelope
    assert "Step ID: S1" in envelope
    assert "Last Terminal Command: pytest" in envelope
    assert "Architect Plan:" in envelope
    assert "Available Tools:" in envelope


def test_context_envelope_truncates_relevant_files_and_solution_matches():
    builder = ContextEnvelopeBuilder()
    envelope = builder.build(
        role="engineer",
        task="Implement auth",
        project_context=_project_context(),
        tools=[],
    ).render()

    assert "module_0.py" in envelope
    assert "module_4.py" in envelope
    assert "module_5.py" not in envelope
    assert "Solution 0" in envelope
    assert "Solution 2" in envelope
    assert "Solution 3" not in envelope
