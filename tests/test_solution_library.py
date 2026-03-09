from __future__ import annotations

from boss.memory.embeddings import EmbeddingService
from boss.memory.solution_library import SolutionLibrary


def test_solution_library_verified_only_filters_entries(tmp_path):
    library = SolutionLibrary(tmp_path / "boss.db", EmbeddingService())

    library.add_solution(
        title="Verified auth middleware",
        description="A verified auth middleware solution.",
        code_snippet="def auth():\n    return True\n",
        tags=["auth"],
        projects=["demo"],
        metadata={"verified": True},
    )
    library.add_solution(
        title="Draft auth middleware",
        description="An unverified auth middleware draft.",
        code_snippet="def auth():\n    return False\n",
        tags=["auth"],
        projects=["demo"],
        metadata={"verified": False},
    )

    search_results = library.search("auth middleware", project_name="demo", limit=10, verified_only=True)
    list_results = library.list_solutions(project_name="demo", limit=10, verified_only=True)

    assert [entry.title for entry in search_results] == ["Verified auth middleware"]
    assert [entry.title for entry in list_results] == ["Verified auth middleware"]


def test_capture_task_solution_marks_verified_from_status_and_errors(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "auth.py").write_text("def auth():\n    return True\n", encoding="utf-8")
    library = SolutionLibrary(tmp_path / "boss.db", EmbeddingService())

    verified = library.capture_task_solution(
        project_name="demo",
        task="Implement auth",
        solution_text="Completed auth implementation",
        changed_files=["auth.py"],
        project_root=project_root,
        errors=[],
        metadata={"status": "completed"},
    )
    unverified = library.capture_task_solution(
        project_name="demo",
        task="Implement auth draft",
        solution_text="Draft auth implementation",
        changed_files=["auth.py"],
        project_root=project_root,
        errors=["tests failed"],
        metadata={"status": "completed"},
    )

    assert verified is not None
    assert unverified is not None
    assert verified.metadata["verified"] is True
    assert unverified.metadata["verified"] is False
