from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, set_tracing_disabled

from boss.config import settings
from boss.control import build_agent_instructions, load_boss_control
from boss.execution import AUTO_ALLOWED_EXECUTION_TYPES, get_tool_metadata
from boss.guardrails.safety import safety_check
from boss.tools.mac import (
    get_clipboard,
    open_app,
    run_applescript,
    screenshot,
    search_files,
    send_notification,
    set_clipboard,
)
from boss.tools.memory import (
    get_project_details,
    list_known_projects,
    memory_stats,
    recall,
    remember,
    search_project_content,
)
from boss.tools.research import web_search

set_tracing_disabled(not settings.tracing_enabled)


@dataclass(frozen=True)
class WorkModePolicy:
    name: str
    agent_instructions: str
    mac_instructions: str
    research_instructions: str
    reasoning_instructions: str
    code_instructions: str
    allow_restricted_tools: bool = False
    allow_external_tools: bool = False
    allow_mcp_servers: bool = False


def _mode_policy(mode: str) -> WorkModePolicy:
    if mode == "ask":
        return WorkModePolicy(
            name="ask",
            agent_instructions=(
                "You are Boss in ask mode. Use only read and search capabilities. "
                "Do not perform edits, runs, notifications, clipboard writes, screenshots, memory writes, or web calls. "
                "If the user asks for a side-effecting action, explain what would be required instead of doing it."
            ),
            mac_instructions=(
                "You are a macOS read-only assistant in ask mode. "
                "Use only read/search capabilities and never change the system state."
            ),
            research_instructions=(
                "You are a read-only analyst in ask mode. Answer from available context only and do not use external web tools."
            ),
            reasoning_instructions=(
                "You are an ask-mode analyst. Reason clearly, but do not turn the answer into an execution plan unless requested."
            ),
            code_instructions=(
                "You are a read-only code assistant in ask mode. Inspect and explain code, but do not modify files or propose that changes were applied."
            ),
        )
    if mode == "plan":
        return WorkModePolicy(
            name="plan",
            agent_instructions=(
                "You are Boss in plan mode. Use only read and search capabilities. Do not execute side-effecting actions. "
                "Return a structured plan with these sections in order: Goal, Execution Plan, Risks, Validation. "
                "Execution Plan must be a numbered list. Be explicit about approvals or risky steps that would be required in agent mode."
            ),
            mac_instructions=(
                "You are a macOS planning assistant. Stay read-only and provide plans only, never actions."
            ),
            research_instructions=(
                "You are a planning analyst. Stay within available local context and do not use web tools."
            ),
            reasoning_instructions=(
                "You are a planning analyst. Produce a structured plan, risks, and validation steps without executing anything."
            ),
            code_instructions=(
                "You are a code planning assistant. Inspect the codebase and return a concrete execution plan, risks, and validation without changing anything."
            ),
        )
    if mode == "review":
        return WorkModePolicy(
            name="review",
            agent_instructions=(
                "You are Boss in review mode. This is read-only review. Use only read and search capabilities. "
                "Do not fix code, do not claim changes were made, and do not recommend auto-fixes without first stating findings."
            ),
            mac_instructions=(
                "You are a macOS reviewer. Stay read-only and do not perform side effects."
            ),
            research_instructions=(
                "You are a review analyst. Stay within available local context and do not use external web tools."
            ),
            reasoning_instructions=(
                "You are a review analyst. Lead with findings and reasoning, not with solution steps."
            ),
            code_instructions=(
                "You are a code reviewer. Stay read-only, lead with findings, and do not auto-fix code."
            ),
        )
    return WorkModePolicy(
        name="agent",
        agent_instructions=(
            "You are Boss in agent mode. Use the full governed tool surface when needed, but prefer minimal, justified actions."
        ),
        mac_instructions="You are a macOS automation specialist. Use tools and MCP servers when needed.",
        research_instructions=(
            "You are a research specialist. Use web search only when current or external information is genuinely required."
        ),
        reasoning_instructions="You are an expert analyst. Break down complex problems step by step.",
        code_instructions=(
            "You are an expert programmer. Write clean, correct code. "
            "Use 'list_known_projects', 'get_project_details', and 'search_project_content' to understand the user's projects."
        ),
        allow_restricted_tools=True,
        allow_external_tools=True,
        allow_mcp_servers=True,
    )


def _filter_tools(tools: list[object], *, policy: WorkModePolicy) -> list[object]:
    if policy.allow_restricted_tools and policy.allow_external_tools:
        return tools

    filtered: list[object] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        metadata = get_tool_metadata(name) if name else None
        if metadata is None:
            continue
        if metadata.execution_type in AUTO_ALLOWED_EXECUTION_TYPES:
            filtered.append(tool)
            continue
        if policy.allow_restricted_tools and metadata.execution_type.value in {"edit", "run"}:
            filtered.append(tool)
            continue
        if policy.allow_external_tools and metadata.execution_type.value == "external":
            filtered.append(tool)
    return filtered


def _tool_names(tools: list[object]) -> set[str]:
    return {getattr(tool, "name", "") for tool in tools if getattr(tool, "name", "")}


def _general_instructions(policy: WorkModePolicy, tools: list[object]) -> str:
    tool_names = _tool_names(tools)
    memory_lines = ["- Use 'recall' to look up what you know about the user"]
    if "remember" in tool_names:
        memory_lines.append("- Use 'remember' to store important facts the user shares")
    memory_lines.append("- Use 'list_known_projects' to see projects on this machine")
    memory_lines.append(
        "- Use 'search_project_content' when the user asks about local project internals, code structure, or entry points"
    )

    specialist_lines = [
        "- mac: local system inspection and macOS-specific work when allowed by the current mode",
        "- research: research-oriented reasoning, with web search only when the current mode allows it",
        "- reasoning: complex multi-step analysis requiring deep thought",
        "- code: software engineering, debugging, code generation, or code review depending on the current mode",
    ]

    return (
        "You are Boss, a helpful personal AI assistant. Answer clearly and concisely.\n"
        "You have access to a persistent memory system:\n"
        + "\n".join(memory_lines)
        + "\n\nExecution policy:\n"
        "- Prefer read and search tools before any modifying action\n"
        "- Use edit, run, or external tools only when the current mode allows them and they are genuinely necessary\n"
        "- State the intent clearly before restricted actions\n"
        "- Avoid chaining multiple restricted actions when one will do\n\n"
        "For MOST requests, answer directly. Hand off ONLY when a specialist is clearly needed:\n"
        + "\n".join(specialist_lines)
        + "\nBe concise. Be direct.\n\n"
        + policy.agent_instructions
    )


def build_entry_agent(
    *,
    active_mcp_servers: dict[str, object] | None = None,
    mode: str | None = None,
    workspace_root: Path | None = None,
) -> Agent:
    active_mcp_servers = active_mcp_servers or {}
    control = load_boss_control(workspace_root)
    resolved_mode = mode or control.config.default_mode
    policy = _mode_policy(resolved_mode)

    mac_tools = _filter_tools(
        [open_app, run_applescript, search_files, get_clipboard, set_clipboard, send_notification, screenshot],
        policy=policy,
    )
    research_tools = _filter_tools([web_search], policy=policy) if settings.cloud_api_key else []
    general_tools = _filter_tools(
        [remember, recall, list_known_projects, get_project_details, search_project_content, memory_stats],
        policy=policy,
    )
    code_tools = _filter_tools([recall, list_known_projects, get_project_details, search_project_content], policy=policy)

    mac_agent = Agent(
        name="mac",
        model=settings.mac_model,
        instructions=build_agent_instructions(
            policy.mac_instructions,
            agent_name="mac",
            mode=resolved_mode,
            workspace_root=control.root,
        ),
        tools=mac_tools,
        mcp_servers=[
            server
            for name, server in active_mcp_servers.items()
            if name in {"apple", "filesystem"}
        ] if policy.allow_mcp_servers else [],
    )

    research_agent = Agent(
        name="research",
        model=settings.research_model,
        instructions=build_agent_instructions(
            policy.research_instructions,
            agent_name="research",
            mode=resolved_mode,
            workspace_root=control.root,
        ),
        tools=research_tools,
    )

    reasoning_agent = Agent(
        name="reasoning",
        model=settings.reasoning_model,
        instructions=build_agent_instructions(
            policy.reasoning_instructions,
            agent_name="reasoning",
            mode=resolved_mode,
            workspace_root=control.root,
        ),
    )

    code_agent = Agent(
        name="code",
        model=settings.code_model,
        instructions=build_agent_instructions(
            policy.code_instructions,
            agent_name="code",
            mode=resolved_mode,
            workspace_root=control.root,
        ),
        tools=code_tools,
    )

    # General is the actual entry point. It answers directly when it can
    # and hands off to specialists only when a narrower toolset is useful.
    return Agent(
        name="general",
        model=settings.general_model,
        instructions=build_agent_instructions(
            _general_instructions(policy, general_tools),
            agent_name="general",
            mode=resolved_mode,
            workspace_root=control.root,
        ),
        tools=general_tools,
        handoffs=[mac_agent, research_agent, reasoning_agent, code_agent],
        input_guardrails=[safety_check],
        mcp_servers=[active_mcp_servers["memory"]] if policy.allow_mcp_servers and "memory" in active_mcp_servers else [],
    )


def build_review_agent(*, output_type: type[Any], workspace_root: Path | None = None) -> Agent:
    control = load_boss_control(workspace_root)
    policy = _mode_policy("review")
    review_tools = _filter_tools(
        [recall, list_known_projects, get_project_details, search_project_content, memory_stats],
        policy=policy,
    )
    instructions = build_agent_instructions(
        (
            "You are the dedicated Boss review workflow. Review only the provided local evidence bundle. "
            "Start with findings ordered by severity. Report only substantive bugs, regressions, unsafe behavior, or missing validation. "
            "Do not auto-fix code. Do not emit style-only nits unless they hide a real defect. "
            "Prefer diff evidence first, then indexed project context and local review docs when needed. "
            "Each finding must include severity, file path, evidence, risk, and a recommended fix."
        ),
        agent_name="code",
        mode="review",
        workspace_root=control.root,
    )
    return Agent(
        name="review_workflow",
        model=settings.code_model,
        instructions=instructions,
        tools=review_tools,
        output_type=output_type,
    )


entry_agent = build_entry_agent()