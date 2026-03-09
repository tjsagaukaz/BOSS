from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

try:  # pragma: no cover - dependency import is environment specific
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

from boss.types import ModelRunResult, ToolDefinition, ToolExecutionRecord


class OpenAIModelClient:
    provider = "openai"

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
                self.logger.warning("Streaming with tool use falls back to non-streaming execution for OpenAI.")
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
        response = client.responses.create(
            model=self.model,
            instructions=system_prompt or None,
            input=[{"role": "user", "content": prompt}],
            timeout=timeout,
            **self._response_options(max_tokens=max_tokens, temperature=temperature),
        )
        return ModelRunResult(
            text=getattr(response, "output_text", "") or self._extract_text_from_output(response),
            provider=self.provider,
            model=self.model,
            duration_seconds=time.perf_counter() - started,
            usage=self._extract_usage(response),
            raw=response,
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
        usage = self._empty_usage()
        response = client.responses.create(
            model=self.model,
            instructions=system_prompt or None,
            input=[{"role": "user", "content": prompt}],
            tools=[tool.as_openai_spec() for tool in tools],
            max_tool_calls=max_tool_rounds,
            timeout=timeout_seconds,
            **self._response_options(max_tokens=max_tokens, temperature=temperature),
        )
        self._merge_usage(usage, self._extract_usage(response))
        tool_records: list[ToolExecutionRecord] = []
        tool_rounds = 0
        while True:
            function_calls = self._extract_function_calls(response)
            if not function_calls:
                break
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError(f"Exceeded tool-call round limit ({max_tool_rounds}) for OpenAI execution.")

            tool_outputs = []
            for call in function_calls:
                tool = next((candidate for candidate in tools if candidate.name == call["name"]), None)
                if tool is None:
                    tool_records.append(
                        ToolExecutionRecord(
                            name=call["name"],
                            arguments=call["arguments"],
                            success=False,
                            error=f"Tool '{call['name']}' is not registered.",
                        )
                    )
                    tool_outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps({"error": f"Tool '{call['name']}' is not registered."}),
                        }
                    )
                    continue

                record = tool.invoke(call["arguments"])
                tool_records.append(record)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": self._tool_output_string(record),
                    }
                )

            response = client.responses.create(
                model=self.model,
                instructions=system_prompt or None,
                previous_response_id=getattr(response, "id", None),
                input=tool_outputs,
                tools=[tool.as_openai_spec() for tool in tools],
                max_tool_calls=max_tool_rounds,
                timeout=timeout_seconds,
                **self._response_options(max_tokens=max_tokens, temperature=temperature),
            )
            self._merge_usage(usage, self._extract_usage(response))

        return ModelRunResult(
            text=getattr(response, "output_text", "") or self._extract_text_from_output(response),
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
        stream = client.responses.create(
            model=self.model,
            instructions=system_prompt or None,
            input=[{"role": "user", "content": prompt}],
            stream=True,
            timeout=timeout_seconds,
            **self._response_options(max_tokens=max_tokens, temperature=temperature),
        )
        chunks: list[str] = []
        for event in stream:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    chunks.append(delta)
                    if on_text_delta:
                        on_text_delta(delta)
        return ModelRunResult(
            text="".join(chunks),
            provider=self.provider,
            model=self.model,
            usage={},
        )

    def _ensure_client(self) -> Any:
        if self._client is None:
            if OpenAI is None:
                raise RuntimeError(
                    "The openai package is not installed. Install dependencies from requirements.txt."
                )
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set.")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def _extract_function_calls(self, response: Any) -> list[dict[str, Any]]:
        output = getattr(response, "output", None) or []
        function_calls = []
        for item in output:
            item_type = getattr(item, "type", None)
            if item_type != "function_call":
                continue
            raw_arguments = getattr(item, "arguments", "{}")
            arguments = self._parse_arguments(raw_arguments)
            function_calls.append(
                {
                    "name": getattr(item, "name", ""),
                    "call_id": getattr(item, "call_id", ""),
                    "arguments": arguments,
                }
            )
        return function_calls

    def _extract_text_from_output(self, response: Any) -> str:
        output = getattr(response, "output", None) or []
        chunks: list[str] = []
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) in {"output_text", "text"}:
                    text = getattr(content, "text", "")
                    if text:
                        chunks.append(text)
        return "\n".join(chunks).strip()

    def _parse_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            try:
                return json.loads(raw_arguments)
            except json.JSONDecodeError:
                return {"raw_arguments": raw_arguments}
        return {"raw_arguments": raw_arguments}

    def _tool_output_string(self, record: ToolExecutionRecord) -> str:
        if record.success:
            return json.dumps(record.result, ensure_ascii=False)
        return json.dumps({"error": record.error or "Tool execution failed."}, ensure_ascii=False)

    def _extract_usage(self, response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        return {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    def _empty_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _merge_usage(self, target: dict[str, int], source: dict[str, int]) -> None:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            target[key] = int(target.get(key, 0)) + int(source.get(key, 0))

    def _response_options(self, max_tokens: int | None, temperature: float | None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "max_output_tokens": max_tokens or self.default_max_tokens,
        }
        if self._supports_temperature():
            options["temperature"] = temperature if temperature is not None else self.default_temperature
        return options

    def _supports_temperature(self) -> bool:
        return "-pro" not in self.model
