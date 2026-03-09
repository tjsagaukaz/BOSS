from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

try:  # pragma: no cover - dependency import is environment specific
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

from boss.types import ModelRunResult, ToolDefinition, ToolExecutionRecord


class AnthropicModelClient:
    provider = "anthropic"

    def __init__(
        self,
        model: str,
        default_max_tokens: int = 4096,
        default_temperature: float = 0.2,
        default_timeout_seconds: float = 120.0,
        default_max_tool_rounds: int = 8,
    ) -> None:
        self.model = model
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature
        self.default_timeout_seconds = default_timeout_seconds
        self.default_max_tool_rounds = default_max_tool_rounds
        self.logger = logging.getLogger(self.__class__.__name__)
        self._client = None

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
        on_text_delta: Callable[[str], None] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float | None = None,
        max_tool_rounds: int | None = None,
    ) -> ModelRunResult:
        started = time.perf_counter()
        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        tool_round_limit = max(1, int(max_tool_rounds or self.default_max_tool_rounds))
        if tools:
            if stream:
                self.logger.warning("Streaming with tool use falls back to non-streaming execution for Anthropic.")
            result = self._generate_with_tools(
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout,
                max_tool_rounds=tool_round_limit,
            )
            result.duration_seconds = time.perf_counter() - started
            return result
        if stream:
            result = self._stream_text(
                prompt=prompt,
                system_prompt=system_prompt,
                on_text_delta=on_text_delta,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout,
            )
            result.duration_seconds = time.perf_counter() - started
            return result
        client = self._ensure_client()
        message = client.messages.create(
            model=self.model,
            system=system_prompt or None,
            max_tokens=max_tokens or self.default_max_tokens,
            temperature=temperature if temperature is not None else self.default_temperature,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        return ModelRunResult(
            text=self._collect_text(message),
            provider=self.provider,
            model=self.model,
            duration_seconds=time.perf_counter() - started,
            usage=self._extract_usage(message),
            raw=message,
        )

    def _generate_with_tools(
        self,
        prompt: str,
        system_prompt: str,
        tools: list[ToolDefinition],
        max_tokens: int | None,
        temperature: float | None,
        timeout_seconds: float,
        max_tool_rounds: int,
    ) -> ModelRunResult:
        client = self._ensure_client()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        tool_records: list[ToolExecutionRecord] = []
        usage = self._empty_usage()
        response = client.messages.create(
            model=self.model,
            system=system_prompt or None,
            max_tokens=max_tokens or self.default_max_tokens,
            temperature=temperature if temperature is not None else self.default_temperature,
            messages=messages,
            tools=[tool.as_anthropic_spec() for tool in tools],
            timeout=timeout_seconds,
        )
        self._merge_usage(usage, self._extract_usage(response))

        tool_rounds = 0
        while True:
            tool_blocks = [block for block in getattr(response, "content", []) if getattr(block, "type", None) == "tool_use"]
            if not tool_blocks:
                break
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError(f"Exceeded tool-call round limit ({max_tool_rounds}) for Anthropic execution.")

            assistant_content: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            for block in getattr(response, "content", []):
                block_type = getattr(block, "type", None)
                if block_type == "text":
                    assistant_content.append({"type": "text", "text": getattr(block, "text", "")})
                    continue
                if block_type != "tool_use":
                    continue

                tool_name = getattr(block, "name", "")
                tool_input = getattr(block, "input", {}) or {}
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": getattr(block, "id", ""),
                        "name": tool_name,
                        "input": tool_input,
                    }
                )
                tool = next((candidate for candidate in tools if candidate.name == tool_name), None)
                if tool is None:
                    record = ToolExecutionRecord(
                        name=tool_name,
                        arguments=tool_input,
                        success=False,
                        error=f"Tool '{tool_name}' is not registered.",
                    )
                else:
                    record = tool.invoke(tool_input)

                tool_records.append(record)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "id", ""),
                        "content": self._tool_output_string(record),
                        "is_error": not record.success,
                    }
                )

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            response = client.messages.create(
                model=self.model,
                system=system_prompt or None,
                max_tokens=max_tokens or self.default_max_tokens,
                temperature=temperature if temperature is not None else self.default_temperature,
                messages=messages,
                tools=[tool.as_anthropic_spec() for tool in tools],
                timeout=timeout_seconds,
            )
            self._merge_usage(usage, self._extract_usage(response))

        return ModelRunResult(
            text=self._collect_text(response),
            provider=self.provider,
            model=self.model,
            usage=usage,
            tool_records=tool_records,
            raw=response,
        )

    def _stream_text(
        self,
        prompt: str,
        system_prompt: str,
        on_text_delta: Callable[[str], None] | None,
        max_tokens: int | None,
        temperature: float | None,
        timeout_seconds: float,
    ) -> ModelRunResult:
        client = self._ensure_client()
        chunks: list[str] = []
        with client.messages.stream(
            model=self.model,
            system=system_prompt or None,
            max_tokens=max_tokens or self.default_max_tokens,
            temperature=temperature if temperature is not None else self.default_temperature,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_seconds,
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
                if on_text_delta:
                    on_text_delta(text)
        return ModelRunResult(text="".join(chunks), provider=self.provider, model=self.model, usage={})

    def _ensure_client(self) -> Any:
        if self._client is None:
            if anthropic is None:
                raise RuntimeError(
                    "The anthropic package is not installed. Install dependencies from requirements.txt."
                )
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set.")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def _collect_text(self, response: Any) -> str:
        parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def _tool_output_string(self, record: ToolExecutionRecord) -> str:
        if record.success:
            return json.dumps(record.result, ensure_ascii=False)
        return json.dumps({"error": record.error or "Tool execution failed."}, ensure_ascii=False)

    def _extract_usage(self, response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    def _empty_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _merge_usage(self, target: dict[str, int], source: dict[str, int]) -> None:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
