from __future__ import annotations

import pytest

from boss.models.openai_client import OpenAIModelClient
from boss.types import ToolDefinition


class _FakeFunctionCall:
    def __init__(self, name: str, call_id: str, arguments: str) -> None:
        self.type = "function_call"
        self.name = name
        self.call_id = call_id
        self.arguments = arguments


class _FakeResponse:
    def __init__(self, index: int) -> None:
        self.id = f"resp-{index}"
        self.output = [_FakeFunctionCall("read_file", f"call-{index}", '{"path":"README.md"}')]
        self.usage = None


class _FakeResponsesAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.counter = 0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.counter += 1
        return _FakeResponse(self.counter)


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponsesAPI()


def test_openai_model_client_bounds_tool_rounds(monkeypatch):
    client = OpenAIModelClient(model="gpt-5.4")
    fake_client = _FakeClient()
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)
    tool = ToolDefinition(
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=lambda args: {"content": "placeholder"},
    )

    with pytest.raises(RuntimeError, match="Exceeded tool-call round limit"):
        client.generate(
            prompt="Inspect the project.",
            tools=[tool],
            timeout_seconds=12,
            max_tool_rounds=2,
        )

    assert all(call.get("timeout") == 12 for call in fake_client.responses.calls)
    assert all(call.get("max_tool_calls") == 2 for call in fake_client.responses.calls)
