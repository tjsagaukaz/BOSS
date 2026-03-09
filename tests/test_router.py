from __future__ import annotations

from boss.configuration import BOSSConfig, ModelConfig
from boss.router import ModelRouter


def test_router_uses_engineer_escalation_model_for_deep_requests():
    router = ModelRouter(
        BOSSConfig(
            models={
                "engineer": ModelConfig(
                    provider="openai",
                    model="gpt-5.4",
                    extra={"escalation_model": "gpt-5.4-pro", "escalation_min_attempt": 2},
                )
            },
            routing={},
            embeddings={},
        )
    )

    cfg = router.config_for_request(
        role="engineer",
        prompt="Implement a complex auth flow",
        request_options={"deep": True, "attempt": 1},
    )

    assert cfg.model == "gpt-5.4-pro"


def test_router_escalates_engineer_after_retry():
    router = ModelRouter(
        BOSSConfig(
            models={
                "engineer": ModelConfig(
                    provider="openai",
                    model="gpt-5.4",
                    extra={"escalation_model": "gpt-5.4-pro", "escalation_min_attempt": 2},
                )
            },
            routing={},
            embeddings={},
        )
    )

    cfg = router.config_for_request(
        role="engineer",
        prompt="Implement auth",
        request_options={"attempt": 2},
    )

    assert cfg.model == "gpt-5.4-pro"


def test_router_uses_architect_for_conversation_discussion():
    router = ModelRouter(
        BOSSConfig(
            models={
                "architect": ModelConfig(provider="anthropic", model="claude-opus-4-6"),
                "engineer": ModelConfig(provider="openai", model="gpt-5.4"),
                "conversation": ModelConfig(
                    provider="openai",
                    model="gpt-5.4",
                    extra={"discussion_role": "architect", "planning_role": "architect", "execution_role": "engineer"},
                ),
            },
            routing={},
            embeddings={},
        )
    )

    cfg = router.config_for_request(
        role="conversation",
        prompt="What should we build next?",
        request_options={"mode": "conversation", "conversation_type": "discussion"},
    )

    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-4-6"


def test_router_uses_engineer_for_conversation_execution():
    router = ModelRouter(
        BOSSConfig(
            models={
                "architect": ModelConfig(provider="anthropic", model="claude-opus-4-6"),
                "engineer": ModelConfig(provider="openai", model="gpt-5.4"),
                "conversation": ModelConfig(
                    provider="openai",
                    model="gpt-5.4",
                    extra={"discussion_role": "architect", "planning_role": "architect", "execution_role": "engineer"},
                ),
            },
            routing={},
            embeddings={},
        )
    )

    cfg = router.config_for_request(
        role="conversation",
        prompt="Build auth middleware",
        request_options={"mode": "conversation", "conversation_type": "execution"},
    )

    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.4"
