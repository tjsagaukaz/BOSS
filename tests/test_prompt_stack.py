from __future__ import annotations

from types import SimpleNamespace

from boss.agents.base_agent import BaseAgent
from boss.evolution.prompt_optimizer import PromptOptimizer


class _FakeTaskAnalyzer:
    def __init__(self, summary_payload):
        self._summary_payload = summary_payload

    def summary(self, project_name: str | None = None):
        return dict(self._summary_payload)


class _FakeStyleProfileStore:
    def __init__(self, indentation: str = "4 spaces") -> None:
        self._profile = SimpleNamespace(
            indentation=indentation,
            naming_conventions=["snake_case"],
            test_style="pytest",
        )

    def get_effective_profile(self, project_name: str | None):
        return self._profile


def test_base_agent_prefers_base_prompt_unless_optimized_is_enabled(tmp_path, monkeypatch):
    prompt_path = tmp_path / "engineer_prompt.txt"
    prompt_path.write_text("base prompt", encoding="utf-8")
    optimized_dir = prompt_path.parent / "optimized"
    optimized_dir.mkdir()
    (optimized_dir / prompt_path.name).write_text("optimized prompt", encoding="utf-8")

    agent = BaseAgent(role="engineer", router=None, prompt_path=prompt_path)

    monkeypatch.delenv("BOSS_USE_OPTIMIZED_PROMPTS", raising=False)
    assert agent._load_system_prompt() == "base prompt"

    monkeypatch.setenv("BOSS_USE_OPTIMIZED_PROMPTS", "1")
    assert agent._load_system_prompt() == "optimized prompt"


def test_prompt_optimizer_ignores_raw_solution_fragments(tmp_path):
    prompts_dir = tmp_path / "boss" / "prompts"
    prompts_dir.mkdir(parents=True)
    for name in PromptOptimizer.ROLE_FILES.values():
        (prompts_dir / name).write_text("Base prompt", encoding="utf-8")

    optimizer = PromptOptimizer(
        root_dir=tmp_path,
        db_path=tmp_path / "data" / "memory.sqlite",
        task_analyzer=_FakeTaskAnalyzer(
            {
                "tasks_failed": 2,
                "tasks_completed": 1,
                "common_errors": ["test failure"],
                "frequent_solutions": [
                    "i want to create my next ios app. lets discuss some ideas. w",
                    "authentication flow",
                ],
            }
        ),
        style_profile=_FakeStyleProfileStore(),
    )

    instructions = optimizer._role_instructions(
        "engineer",
        summary={
            "tasks_failed": 2,
            "tasks_completed": 1,
            "common_errors": ["test failure"],
            "frequent_solutions": [
                "i want to create my next ios app. lets discuss some ideas. w",
                "authentication flow",
            ],
        },
        style=_FakeStyleProfileStore().get_effective_profile(None),
    )

    assert all("ios app" not in item.lower() for item in instructions)
    assert any("auth boundaries" in item.lower() for item in instructions)

    report = optimizer.optimize(project_name="boss", roles=["engineer"], write_files=False)
    optimized_path = report["optimizations"][0]["path"]
    prompt_text = optimizer._compose_prompt("Base prompt", instructions)
    assert "ios app" not in prompt_text.lower()
    assert optimized_path.endswith("engineer_prompt.txt")
