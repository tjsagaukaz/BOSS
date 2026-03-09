from __future__ import annotations

import json
import logging
import shlex
import time
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table

from boss.orchestrator import BOSSOrchestrator

app = typer.Typer(help="BOSS: Builder Orchestration System for Sagau")
lab_app = typer.Typer(help="Autonomous Engineering Lab")
runs_app = typer.Typer(help="Run and artifact operations")
brain_app = typer.Typer(help="Active project brain operations")
roots_app = typer.Typer(help="Workspace roots registry")
mcp_app = typer.Typer(help="MCP connector registry")
console = Console()
app.add_typer(lab_app, name="lab")
app.add_typer(runs_app, name="run")
app.add_typer(brain_app, name="brain")
app.add_typer(roots_app, name="roots")
app.add_typer(mcp_app, name="mcp")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def configure_logging(verbose: bool = False) -> None:
    logs_dir = repo_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(logs_dir / "boss.log", encoding="utf-8"),
    ]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def build_orchestrator(verbose: bool = False) -> BOSSOrchestrator:
    load_dotenv(repo_root() / ".env", override=True)
    configure_logging(verbose=verbose)
    return BOSSOrchestrator(repo_root(), console=console)


def confirm_overwrite(path: Path) -> bool:
    return Confirm.ask(f"Overwrite {path}?", default=False)


def confirm_action(prompt: str) -> bool:
    return Confirm.ask(prompt, default=False)


def render_status(orchestrator: BOSSOrchestrator) -> None:
    status = orchestrator.status()
    active_project = status["active_project"] or "Workspace"
    models = status["models"] or "Not configured"
    indexed_at = status.get("indexed_at") or "Not indexed"
    current_task = status.get("current_task") or "None"
    current_task_status = status.get("current_task_status") or "Idle"
    active_file = status.get("active_file") or "None"
    console.print("[bold cyan]BOSS AI Terminal[/bold cyan]")
    console.print(f"Active Project: {active_project}")
    console.print(f"Models: {models}")
    if status.get("workspace_root"):
        console.print(f"Workspace Root: {status['workspace_root']}")
    console.print(f"Indexed: {indexed_at}")
    console.print(f"Task: {current_task} [{current_task_status}]")
    console.print(f"Editor: {active_file}")
    if status.get("project_mission"):
        console.print(f"Mission: {status['project_mission']}")
    if status.get("project_focus"):
        console.print(f"Focus: {status['project_focus']}")
    if status.get("next_priority"):
        console.print(f"Next Priority: {status['next_priority']}")
    if status.get("pending_brain_proposals"):
        console.print(f"Pending Brain Proposals: {status['pending_brain_proposals']}")


def render_plan(text: str) -> None:
    console.print(Panel(text, title="Architect Plan", expand=False))


def render_code_result(result) -> None:
    console.print(Panel(result.plan.text, title="Architect Plan", expand=False))
    console.print(Panel(result.implementation.text, title="Engineer Result", expand=False))
    console.print(Panel(result.audit.text, title="Audit Result", expand=False))
    if result.changed_files:
        console.print(f"Changed Files: {', '.join(result.changed_files)}")
    console.print(f"Iterations: {result.iterations}")


def render_audit_result(result) -> None:
    console.print(Panel(result.text, title="Audit Result", expand=False))


def render_index_result(result) -> None:
    console.print(Panel(result.project_map.overview, title=f"Index: {result.project_name}", expand=False))
    console.print(
        f"Files: total={result.total_files} indexed={result.indexed_files} "
        f"changed={result.changed_files} skipped={result.skipped_files} removed={result.removed_files}"
    )


def render_search_results(query: str, results) -> None:
    table = Table(title=f"Semantic Search: {query}")
    table.add_column("Score", justify="right")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Preview")

    for item in results:
        metadata = item.get("metadata", {})
        file_path = metadata.get("file_path", "-") if isinstance(metadata, dict) else "-"
        preview = str(item.get("text", "")).replace("\n", " ")[:120]
        table.add_row(f"{item.get('score', 0):.3f}", str(item.get("kind", "")), str(file_path), preview)

    if not results:
        table.add_row("0.000", "-", "-", "No matches found")

    console.print(table)


def render_diff_bundle(bundle) -> None:
    files = bundle.get("files", []) if isinstance(bundle, dict) else []
    if not files:
        console.print("No diff files found.")
        return
    for file_payload in files:
        path = str(file_payload.get("path", "unknown"))
        status = str(file_payload.get("status", "modified"))
        diff_text = str(file_payload.get("diff", "") or "")
        console.print(Panel(f"{path} [{status}]", title="Diff File", expand=False))
        if diff_text.strip():
            console.print(Syntax(diff_text, "diff", theme="ansi_dark", line_numbers=False))
        else:
            console.print("No diff text recorded for this file.")


def render_project_map(project_map) -> None:
    console.print(Panel(project_map.overview, title=f"Project: {project_map.name}", expand=False))
    languages = ", ".join(
        f"{name} ({count})" for name, count in sorted(project_map.languages.items(), key=lambda item: (-item[1], item[0]))
    ) or "None"
    console.print(f"Languages: {languages}")
    console.print("Modules:")
    if project_map.main_modules:
        for module in project_map.main_modules[:12]:
            console.print(f"- {module}")
    else:
        console.print("- None")
    console.print("Key files:")
    if project_map.key_files:
        for file_path in project_map.key_files[:12]:
            console.print(f"- {file_path}")
    else:
        console.print("- None")
    if project_map.entry_points:
        console.print("Entry points:")
        for entry in project_map.entry_points[:12]:
            console.print(f"- {entry}")
    if project_map.dependencies:
        console.print("Dependencies:")
        for dependency in project_map.dependencies[:15]:
            console.print(f"- {dependency}")


def render_research(report) -> None:
    console.print(Panel(report.summary, title=f"Research: {report.query}", expand=False))
    if report.sources:
        table = Table(title="Research Sources")
        table.add_column("Citation")
        table.add_column("Type")
        table.add_column("Title")
        table.add_column("Location")
        for source in report.sources[:12]:
            table.add_row(
                str(source.citation),
                str(source.source_type),
                str(source.title),
                str(source.url or source.file_path),
            )
        console.print(table)


def render_roots(snapshot: dict) -> None:
    table = Table(title="Workspace Roots")
    table.add_column("Name")
    table.add_column("Mode")
    table.add_column("Enabled")
    table.add_column("Path")
    for root in snapshot.get("roots", []):
        table.add_row(
            str(root.get("name", "")),
            str(root.get("mode", "")),
            "yes" if root.get("enabled") else "no",
            str(root.get("path", "")),
        )
    console.print(table)


def render_portfolio(snapshot: dict) -> None:
    table = Table(title="Portfolio")
    table.add_column("Project")
    table.add_column("Focus")
    table.add_column("Next Priority")
    table.add_column("Root")
    for project in snapshot.get("projects", []):
        table.add_row(
            str(project.get("display_name", "")),
            str(project.get("focus", "")),
            str(project.get("next_priority", "")),
            str(project.get("root", "")),
        )
    console.print(table)


def render_permissions(snapshot: dict) -> None:
    console.print(Panel(json.dumps(snapshot, indent=2), title="Permissions", expand=False))


def render_mcp(snapshot: dict) -> None:
    table = Table(title="MCP Connectors")
    table.add_column("Name")
    table.add_column("Transport")
    table.add_column("Healthy")
    table.add_column("Target")
    table.add_column("Capabilities")
    for connector in snapshot.get("connectors", []):
        table.add_row(
            str(connector.get("name", "")),
            str(connector.get("transport", "")),
            "yes" if connector.get("healthy") else "no",
            str(connector.get("target", "")),
            ", ".join(connector.get("capabilities", [])),
        )
    console.print(table)


def render_build_result(result) -> None:
    console.print(Panel(result.final_result, title=f"Build: {result.status}", expand=False))
    console.print(f"Task ID: {result.task_id}")
    console.print(f"Goal: {result.goal}")
    shipping = dict((getattr(result, "metadata", {}) or {}).get("shipping", {}) or {})
    if shipping:
        console.print(f"Shipping: {shipping.get('status', 'ready')}")
        if shipping.get("message"):
            console.print(f"Ship Note: {shipping['message']}")
    if result.changed_files:
        console.print(f"Changed Files: {', '.join(result.changed_files)}")
    if result.errors:
        console.print("Errors:")
        for error in result.errors:
            console.print(f"- {error}")


def render_task_status(task) -> None:
    if task is None:
        console.print("No task history available.")
        return

    console.print(Panel(task.get("task", ""), title=f"Task #{task.get('id')} [{task.get('status')}]", expand=False))
    plan = task.get("plan") or {}
    if plan.get("steps"):
        console.print(f"Plan Steps: {len(plan['steps'])}")

    table = Table(title="Task Progress")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Iterations", justify="right")
    table.add_column("Files")
    table.add_column("Failure Map")
    for step in task.get("steps", []):
        table.add_row(
            f"{step.get('step_index', 0) + 1}. {step.get('title', '')}",
            str(step.get("status", "")),
            str(step.get("iterations", 0)),
            ", ".join(step.get("files_changed", [])[:3]),
            str((step.get("metadata", {}) or {}).get("failure_map_primary") or ""),
        )
    if task.get("steps"):
        console.print(table)
    failure_counts = (task.get("metadata", {}) or {}).get("failure_map_counts", {}) or {}
    if failure_counts:
        ordered = ", ".join(
            f"{name}={count}" for name, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))
        )
        console.print(f"Failure Map Counts: {ordered}")
    if task.get("final_result"):
        console.print(f"Result: {task['final_result']}")
    if task.get("errors"):
        console.print("Errors:")
        for error in task["errors"]:
            console.print(f"- {error}")


def build_task_status_renderable(task) -> Panel:
    if task is None:
        return Panel("No task history available.", title="Loop Status", expand=False)

    lines = [
        f"Goal: {task.get('task', '')}",
        f"Status: {task.get('status', '')}",
    ]
    total_steps = int(task.get("total_steps") or len(task.get("steps", [])) or 0)
    completed = sum(1 for step in task.get("steps", []) if str(step.get("status", "")).lower() == "completed")
    if total_steps:
        lines.append(f"Iteration: {completed}/{total_steps} step(s) completed")
    current_step = next((step for step in task.get("steps", []) if str(step.get("status", "")).lower() == "running"), None)
    if current_step:
        lines.append(f"Current: {current_step.get('title', '')}")
    if task.get("files_changed"):
        lines.append(f"Files: {', '.join(task.get('files_changed', [])[:5])}")
    if task.get("errors"):
        lines.append(f"Latest issue: {task['errors'][-1]}")
    return Panel("\n".join(lines), title=f"Loop #{task.get('id')} [{task.get('status')}]", expand=False)


def render_tools(tools) -> None:
    table = Table(title="Available Tools")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Capabilities")
    table.add_column("Plugin")
    for tool in tools:
        table.add_row(
            str(tool.get("name", "")),
            str(tool.get("category", "")),
            ", ".join(tool.get("capabilities", [])),
            str(tool.get("plugin") or ""),
        )
    console.print(table)


def render_plugins(plugins) -> None:
    table = Table(title="Plugins")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Tools")
    for plugin in plugins:
        table.add_row(
            str(plugin.get("name", "")),
            str(plugin.get("description", "")),
            ", ".join(plugin.get("tools", [])),
        )
    console.print(table)


def render_swarm_agents(agents) -> None:
    table = Table(title="Swarm Agents")
    table.add_column("Agent")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Task")
    for agent in agents:
        table.add_row(
            str(agent.get("agent_name", "")),
            str(agent.get("role", "")),
            str(agent.get("status", "")),
            str(agent.get("current_task", "")),
        )
    if not agents:
        table.add_row("No agents", "", "", "")
    console.print(table)


def render_swarm_tasks(snapshot) -> None:
    runs = snapshot.get("runs", [])
    tasks = snapshot.get("tasks", [])
    stats = snapshot.get("stats", {})

    if runs:
        runs_table = Table(title="Swarm Runs")
        runs_table.add_column("Run ID")
        runs_table.add_column("Project")
        runs_table.add_column("Status")
        runs_table.add_column("Goal")
        for run in runs:
            runs_table.add_row(
                str(run.get("run_id", "")),
                str(run.get("project_name", "")),
                str(run.get("status", "")),
                str(run.get("goal", ""))[:70],
            )
        console.print(runs_table)
    else:
        console.print("No swarm runs.")

    tasks_table = Table(title="Swarm Tasks")
    tasks_table.add_column("Task ID")
    tasks_table.add_column("Run ID")
    tasks_table.add_column("Agent")
    tasks_table.add_column("Status")
    tasks_table.add_column("Title")
    for task in tasks:
        tasks_table.add_row(
            str(task.get("task_id", "")),
            str(task.get("run_id", "")),
            str(task.get("agent_type", "")),
            str(task.get("status", "")),
            str(task.get("title", "")),
        )
    if not tasks:
        tasks_table.add_row("-", "-", "-", "-", "No swarm tasks queued.")
    console.print(tasks_table)
    if stats:
        console.print("Queue Stats: " + ", ".join(f"{key}={value}" for key, value in sorted(stats.items())))


def render_solutions(solutions) -> None:
    table = Table(title="Reusable Solutions")
    table.add_column("Title")
    table.add_column("Projects")
    table.add_column("Tags")
    table.add_column("Summary")
    for solution in solutions:
        table.add_row(
            str(solution.title),
            ", ".join(solution.projects[:4]),
            ", ".join(solution.tags[:6]),
            str(solution.description[:120]),
        )
    if not solutions:
        table.add_row("No solutions recorded", "", "", "")
    console.print(table)


def render_memory_snapshot(snapshot) -> None:
    profile = snapshot.get("project_profile")
    style = snapshot.get("style_profile")
    graph_insights = snapshot.get("graph_insights", [])
    recent_tasks = snapshot.get("recent_tasks", [])
    related_projects = snapshot.get("related_projects", [])
    solutions = snapshot.get("solutions", [])

    if profile is not None:
        console.print(Panel(profile.description, title=f"Project Memory: {profile.project_name}", expand=False))
        console.print(f"Primary Language: {profile.primary_language}")
        console.print(f"Frameworks: {', '.join(profile.frameworks) or 'None'}")
        console.print(f"Architecture: {profile.architecture}")
        console.print(f"Key Modules: {', '.join(profile.key_modules) or 'None'}")
        console.print(f"Patterns: {', '.join(profile.coding_patterns) or 'None'}")
        console.print(f"Related Projects: {', '.join(profile.related_projects) or 'None'}")
    else:
        console.print("No project memory recorded.")

    if style is not None:
        console.print(
            Panel(
                (
                    f"Indentation: {style.indentation}\n"
                    f"Naming: {', '.join(style.naming_conventions) or 'Mixed'}\n"
                    f"Structure: {style.code_structure}\n"
                    f"Tests: {style.test_style}\n"
                    f"Error Handling: {style.error_handling_style}"
                ),
                title=f"Style Profile: {style.project_name}",
                expand=False,
            )
        )

    if graph_insights:
        console.print("Graph Insights:")
        for insight in graph_insights[:10]:
            console.print(f"- {insight}")

    if related_projects:
        console.print("Related Projects:")
        for item in related_projects[:8]:
            console.print(
                f"- {item.get('project_name', 'unknown')} "
                f"(shared: {', '.join(item.get('shared_nodes', [])[:5]) or 'n/a'})"
            )

    if recent_tasks:
        table = Table(title="Recent Tasks")
        table.add_column("Task")
        table.add_column("Status")
        table.add_column("Files")
        for task in recent_tasks[:8]:
            table.add_row(
                str(task.get("task", ""))[:80],
                str(task.get("status", "")),
                ", ".join(task.get("files_changed", [])[:3]),
            )
        console.print(table)

    render_solutions(solutions[:8])


def render_brain_snapshot(snapshot) -> None:
    brain = snapshot.get("brain")
    if brain is None:
        console.print("No project brain recorded.")
        return
    console.print(
        Panel(
            (
                f"Project: {brain.project_name}\n"
                f"Mission: {brain.mission or 'Not recorded'}\n"
                f"Focus: {brain.current_focus or 'Not recorded'}\n"
                f"Pending Proposals: {snapshot.get('pending_proposals', 0)}"
            ),
            title="Active Project Brain",
            expand=False,
        )
    )
    console.print(f"Policy: {snapshot.get('policy', {}).get('update_mode', 'unknown')}")
    if brain.milestones:
        console.print("Milestones:")
        for item in brain.milestones[:8]:
            console.print(f"- {item}")
    if brain.open_problems:
        console.print("Open Problems:")
        for item in brain.open_problems[:8]:
            console.print(f"- {item}")
    if brain.next_priorities:
        console.print("Next Priorities:")
        for item in brain.next_priorities[:8]:
            console.print(f"- {item}")
    if brain.known_risks:
        console.print("Known Risks:")
        for item in brain.known_risks[:8]:
            console.print(f"- {item}")
    if getattr(brain, "brain_rules", None):
        console.print("Brain Rules:")
        for item in brain.brain_rules[:8]:
            console.print(f"- {item}")


def render_brain_rules(snapshot) -> None:
    project_name = str(snapshot.get("project_name", ""))
    rules = list(snapshot.get("rules", []) or [])
    console.print(
        Panel(
            (
                f"Project: {project_name}\n"
                f"Rules: {len(rules)}\n"
                f"Pending Proposals: {snapshot.get('pending_proposals', 0)}"
            ),
            title="Brain Rules",
            expand=False,
        )
    )
    if not rules:
        console.print("- No explicit brain rules recorded.")
        return
    for index, item in enumerate(rules, start=1):
        console.print(f"{index}. {item}")


def render_next_recommendations(project_name: str, items) -> None:
    table = Table(title=f"Next Recommended Tasks: {project_name}")
    table.add_column("Task")
    table.add_column("Reason")
    table.add_column("Source")
    for item in items:
        table.add_row(
            str(item.get("title", "")),
            str(item.get("reason", "")),
            str(item.get("source", "")),
        )
    if not items:
        table.add_row("No recommendations", "", "")
    console.print(table)


def render_roadmap(report: dict[str, object]) -> None:
    console.print(
        Panel(
            (
                f"Project: {report.get('project_name', '')}\n"
                f"Mission: {report.get('mission', '')}\n"
                f"Focus: {report.get('focus', '')}\n"
                f"Pending Proposals: {report.get('pending_proposals', 0)}"
            ),
            title="Roadmap",
            expand=False,
        )
    )
    sections = [
        ("Completed", report.get("completed", [])),
        ("In Progress", report.get("in_progress", [])),
        ("Future", report.get("future", [])),
    ]
    for title, items in sections:
        console.print(f"{title}:")
        if items:
            for item in items:
                console.print(f"- {item}")
        else:
            console.print("- None")


def render_risks(project_name: str, risks) -> None:
    table = Table(title=f"Risk Analysis: {project_name}")
    table.add_column("Severity")
    table.add_column("Risk")
    table.add_column("Reason")
    table.add_column("Source")
    for item in risks:
        table.add_row(
            str(item.get("severity", "")),
            str(item.get("title", "")),
            str(item.get("reason", "")),
            str(item.get("source", "")),
        )
    if not risks:
        table.add_row("LOW", "No significant risks detected", "", "")
    console.print(table)


def render_brain_proposals(proposals) -> None:
    table = Table(title="Brain Proposals")
    table.add_column("ID")
    table.add_column("Project")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Summary")
    for proposal in proposals:
        table.add_row(
            str(proposal.get("id", "")),
            str(proposal.get("project", "")),
            str(proposal.get("source", "")),
            str(proposal.get("status", "")),
            str(proposal.get("summary", ""))[:100],
        )
    if not proposals:
        table.add_row("-", "", "", "", "No proposals found.")
    console.print(table)


def render_brain_approval(result: dict[str, object]) -> None:
    console.print(
        Panel(
            (
                f"Proposal: {result.get('proposal_id', '')}\n"
                f"Project: {result.get('project_name', '')}\n"
                f"Status: {result.get('status', '')}"
            ),
            title="Brain Proposal Approval",
            expand=False,
        )
    )
    brain = result.get("brain")
    if brain is not None:
        render_brain_snapshot(
            {
                "brain": brain,
                "pending_proposals": result.get("pending_proposals", 0),
                "policy": result.get("policy", {}),
            }
        )


def render_brain_action_result(result: dict[str, object], title: str) -> None:
    console.print(
        Panel(
            (
                f"Project: {result.get('project_name', '')}\n"
                f"Status: {result.get('status', '')}\n"
                f"Pending Proposals: {result.get('pending_proposals', 0)}"
            ),
            title=title,
            expand=False,
        )
    )
    brain = result.get("brain")
    if brain is not None:
        render_brain_snapshot(
            {
                "brain": brain,
                "pending_proposals": result.get("pending_proposals", 0),
                "policy": result.get("policy", {}),
            }
        )


def render_knowledge_graph(graph) -> None:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    related_projects = graph.get("related_projects", [])

    console.print(f"Nodes: {len(nodes)}")
    console.print(f"Edges: {len(edges)}")
    if graph.get("insights"):
        console.print("Insights:")
        for insight in graph["insights"][:12]:
            console.print(f"- {insight}")

    node_table = Table(title="Knowledge Nodes")
    node_table.add_column("Type")
    node_table.add_column("Name")
    node_table.add_column("Project")
    for node in nodes[:20]:
        node_table.add_row(str(node.node_type), str(node.name), str(node.project_name or "global"))
    if not nodes:
        node_table.add_row("-", "No nodes recorded", "-")
    console.print(node_table)

    edge_table = Table(title="Knowledge Edges")
    edge_table.add_column("Source")
    edge_table.add_column("Relationship")
    edge_table.add_column("Target")
    node_lookup = {node.node_id: node for node in nodes}
    for edge in edges[:25]:
        source = node_lookup.get(edge.source_node_id)
        target = node_lookup.get(edge.target_node_id)
        edge_table.add_row(
            source.name if source else str(edge.source_node_id),
            edge.relationship,
            target.name if target else str(edge.target_node_id),
        )
    if not edges:
        edge_table.add_row("-", "No edges recorded", "-")
    console.print(edge_table)

    if related_projects:
        console.print("Related Projects:")
        for item in related_projects[:8]:
            console.print(
                f"- {item.get('project_name', 'unknown')} "
                f"(shared_count={item.get('shared_count', 0)})"
            )


def render_test_result(result) -> None:
    title = "Tests Passed" if result.get("passed", False) else "Tests Failed"
    console.print(Panel(str(result.get("message", "")), title=title, expand=False))
    commands = result.get("commands", [])
    if commands:
        console.print("Commands:")
        for command in commands:
            console.print(f"- {command}")
    for item in result.get("results", [])[:6]:
        console.print(
            Panel(
                f"exit={item.get('exit_code')}\nSTDOUT:\n{str(item.get('stdout', ''))[:500]}\n\nSTDERR:\n{str(item.get('stderr', ''))[:500]}",
                title=str(item.get("command", "")),
                expand=False,
            )
        )


def render_model_catalog(catalog) -> None:
    configured = Table(title="Configured Models")
    configured.add_column("Role")
    configured.add_column("Provider")
    configured.add_column("Model")
    for item in catalog.get("configured_models", []):
        configured.add_row(str(item.get("role", "")), str(item.get("provider", "")), str(item.get("model", "")))
    if not catalog.get("configured_models"):
        configured.add_row("-", "-", "No configured API models")
    console.print(configured)

    local_models = Table(title="Local Models")
    local_models.add_column("Backend")
    local_models.add_column("Model")
    local_models.add_column("Selected")
    local_models.add_column("Endpoint")
    for item in catalog.get("local_models", []):
        local_models.add_row(
            str(item.get("backend", "")),
            str(item.get("model", "")),
            "yes" if item.get("selected") else "no",
            str(item.get("endpoint", "")),
        )
    if not catalog.get("local_models"):
        local_models.add_row("-", "No local models discovered", "-", "-")
    console.print(local_models)

    performance = Table(title="Model Performance")
    performance.add_column("Role")
    performance.add_column("Provider")
    performance.add_column("Model")
    performance.add_column("Runs", justify="right")
    performance.add_column("Avg Seconds", justify="right")
    performance.add_column("Success", justify="right")
    for item in catalog.get("performance", [])[:12]:
        performance.add_row(
            str(item.get("role", "")),
            str(item.get("provider", "")),
            str(item.get("model", "")),
            str(item.get("run_count", 0)),
            f"{float(item.get('avg_duration_seconds', 0.0)):.2f}",
            f"{float(item.get('success_rate', 0.0)) * 100:.0f}%",
        )
    if not catalog.get("performance"):
        performance.add_row("-", "-", "No model metrics yet", "0", "0.00", "0%")
    console.print(performance)


def render_evolution_report(report) -> None:
    metrics = report.get("metrics", report)
    console.print(
        Panel(
            (
                f"Project: {metrics.get('project_name') or 'all projects'}\n"
                f"Tasks Completed: {metrics.get('tasks_completed', 0)}\n"
                f"Tasks Failed: {metrics.get('tasks_failed', 0)}\n"
                f"Solutions Learned: {metrics.get('solutions_learned', 0)}\n"
                f"Prompt Improvements: {metrics.get('prompt_improvements', 0)}\n"
                f"Plugins Generated: {metrics.get('plugins_generated', 0)}"
            ),
            title="BOSS Evolution Metrics",
            expand=False,
        )
    )
    if metrics.get("common_errors"):
        console.print("Common Errors:")
        for item in metrics["common_errors"][:8]:
            console.print(f"- {item}")
    if metrics.get("frequent_solutions"):
        console.print("Frequent Solutions:")
        for item in metrics["frequent_solutions"][:8]:
            console.print(f"- {item}")
    optimizations = report.get("prompt_optimizations") or report.get("improvement", {}).get("prompt_optimizations", [])
    if optimizations:
        table = Table(title="Prompt Optimizations")
        table.add_column("Role")
        table.add_column("Version")
        table.add_column("Path")
        for item in optimizations[:12]:
            table.add_row(str(item.get("role", "")), str(item.get("version", "")), str(item.get("path", "")))
        console.print(table)
    plugin = report.get("plugin")
    if plugin:
        spec = plugin.get("spec", {})
        console.print(
            Panel(
                f"{plugin.get('message', '')}\nPlugin: {spec.get('plugin_name', '')}\nPath: {spec.get('path', '')}",
                title="Plugin Generator",
                expand=False,
            )
        )


def render_chat_response(response) -> None:
    project_name = response.get("project_name") or "workspace"
    intent = response.get("intent") or "conversation"
    mode = response.get("mode") or "chat"
    console.print(
        Panel(
            str(response.get("reply", "")),
            title=f"Chat · {intent} · {mode} · {project_name}",
            expand=False,
        )
    )
    actions = response.get("actions") or []
    if actions:
        table = Table(title="Suggested Actions")
        table.add_column("Label")
        table.add_column("Intent")
        table.add_column("Execute")
        table.add_column("Auto Approve")
        table.add_column("Message")
        for item in actions:
            table.add_row(
                str(item.get("label", "")),
                str(item.get("intent", "")),
                "yes" if item.get("execute") else "no",
                "yes" if item.get("auto_approve") else "no",
                str(item.get("message", ""))[:80],
            )
        console.print(table)
    if response.get("result") is not None:
        console.print(Panel(json.dumps(response["result"], indent=2, default=str), title="Result", expand=False))


def render_voice_result(result) -> None:
    console.print(Panel(str(result.get("transcript", "")), title="Voice Transcript", expand=False))
    console.print(f"Command: {result.get('command', '')}")


def render_eval_run(result) -> None:
    estimated_cost = (
        f"${result.total_estimated_cost_usd:.4f}"
        if result.total_estimated_cost_usd is not None
        else "n/a"
    )
    failure_counts: dict[str, int] = {}
    for task in result.tasks:
        for label in (task.metadata or {}).get("failure_map", []):
            key = str(label)
            failure_counts[key] = failure_counts.get(key, 0) + 1
    console.print(
        Panel(
            (
                f"Run ID: {result.run_id}\n"
                f"Suite: {result.suite_name}\n"
                f"Project: {result.project_name}\n"
                f"Status: {result.status}\n"
                f"Tasks Passed: {result.passed_tasks}/{result.total_tasks}\n"
                f"Runtime: {result.runtime_seconds:.2f}s\n"
                f"Estimated Cost: {estimated_cost}"
            ),
            title="Evaluation Run",
            expand=False,
        )
    )

    table = Table(title="Evaluation Tasks")
    table.add_column("Task")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Runtime", justify="right")
    table.add_column("Failure")
    table.add_column("Failure Map")
    table.add_column("Files")
    for task in result.tasks:
        table.add_row(
            str(task.task_name),
            str(task.mode),
            str(task.status),
            f"{task.runtime_seconds:.2f}s",
            str(task.failure_category or ""),
            str((task.metadata or {}).get("failure_map_primary") or ""),
            ", ".join(task.files_changed[:3]) or "-",
        )
    if not result.tasks:
        table.add_row("No tasks", "-", "-", "0.00s", "-", "-", "-")
    console.print(table)

    if failure_counts:
        ordered = ", ".join(
            f"{name}={count}" for name, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))
        )
        console.print(f"Failure Map Counts: {ordered}")

    failures = [(task.task_name, [item.message for item in task.validations if not item.passed]) for task in result.tasks]
    failures = [(task_name, messages) for task_name, messages in failures if messages]
    if failures:
        console.print("Validation Failures:")
        for task_name, messages in failures:
            console.print(f"- {task_name}: {messages[0]}")


def render_benchmark_result(result: dict[str, object]) -> None:
    suite_rate = result.get("suite_run_success_rate")
    readiness_rate = result.get("suite_readiness_rate")
    task_rate = result.get("task_success_rate")
    median_runtime = result.get("median_runtime_seconds")
    avg_runtime = result.get("avg_runtime_seconds")
    task_variance = result.get("task_variance")
    console.print(
        Panel(
            (
                f"Benchmark: {result.get('name', '')}\n"
                f"Description: {result.get('description', '')}\n"
                f"Executed Suites: {result.get('executed_suite_runs', 0)}/{result.get('total_suite_runs', 0)} "
                f"({f'{float(readiness_rate) * 100:.0f}%' if readiness_rate is not None else 'n/a'})\n"
                f"Suite Runs Passed: {result.get('passed_suite_runs', 0)}/{result.get('executed_suite_runs', 0)} "
                f"({f'{float(suite_rate) * 100:.0f}%' if suite_rate is not None else 'n/a'})\n"
                f"Skipped Suites: {result.get('skipped_suite_runs', 0)}\n"
                f"Tasks Passed: {result.get('passed_tasks', 0)}/{result.get('total_tasks', 0)} "
                f"({f'{float(task_rate) * 100:.0f}%' if task_rate is not None else 'n/a'})\n"
                f"Median Runtime: {f'{float(median_runtime):.2f}s' if median_runtime is not None else 'n/a'}\n"
                f"Avg Runtime: {f'{float(avg_runtime):.2f}s' if avg_runtime is not None else 'n/a'}\n"
                f"Task Variance: {f'{float(task_variance):.3f}' if task_variance is not None else 'n/a'}\n"
                f"Stability: {result.get('stability', 'unknown')}"
            ),
            title="Benchmark Result",
            expand=False,
        )
    )

    suites = list(result.get("suites", []))
    table = Table(title="Benchmark Suites")
    table.add_column("Suite")
    table.add_column("Project")
    table.add_column("Status")
    table.add_column("Runs", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Task Success", justify="right")
    table.add_column("Avg Runtime", justify="right")
    table.add_column("Avg Iter", justify="right")
    table.add_column("Variance", justify="right")
    table.add_column("Stability")
    table.add_column("Failed Tasks", justify="right")
    table.add_column("Notes")
    for item in suites:
        success_rate = item.get("task_success_rate")
        table.add_row(
            str(item.get("suite_name", "")),
            str(item.get("project_name", "")),
            str(item.get("status", "")),
            str(item.get("suite_runs", 0)),
            str(item.get("skipped_suite_runs", 0)),
            f"{float(success_rate) * 100:.0f}%" if success_rate is not None else "n/a",
            f"{float(item.get('avg_runtime_seconds')):.2f}s" if item.get("avg_runtime_seconds") is not None else "n/a",
            f"{float(item.get('avg_iterations')):.2f}" if item.get("avg_iterations") is not None else "n/a",
            f"{float(item.get('task_variance')):.3f}" if item.get("task_variance") is not None else "n/a",
            str(item.get("stability", "unknown")),
            str(item.get("failed_tasks", 0)),
            str(item.get("skip_reason") or ""),
        )
    if not suites:
        table.add_row("No suites", "", "", "0", "0", "n/a", "n/a", "n/a", "n/a", "unknown", "0", "")
    console.print(table)

    failure_categories = result.get("failure_categories", {}) or {}
    if failure_categories:
        console.print(
            "Failure Categories: "
            + ", ".join(f"{name}={count}" for name, count in sorted(failure_categories.items(), key=lambda item: (-item[1], item[0])))
        )
    failure_map = result.get("failure_map", {}) or {}
    if failure_map:
        console.print(
            "Failure Map: "
            + ", ".join(f"{name}={count}" for name, count in sorted(failure_map.items(), key=lambda item: (-item[1], item[0])))
        )


def render_benchmark_sync(result: dict[str, object]) -> None:
    console.print(
        Panel(
            (
                f"Catalog: {result.get('name', '')}\n"
                f"Description: {result.get('description', '')}\n"
                f"Repos: {len(result.get('results', []))}"
            ),
            title="Benchmark Repo Sync",
            expand=False,
        )
    )
    table = Table(title="External Benchmark Repos")
    table.add_column("Name")
    table.add_column("Project")
    table.add_column("Status")
    table.add_column("Path")
    for item in result.get("results", []):
        table.add_row(
            str(item.get("name", "")),
            str(item.get("project_name", "")),
            str(item.get("status", "")),
            str(item.get("path", "")),
        )
    if not result.get("results"):
        table.add_row("No repos", "", "", "")
    console.print(table)


def render_artifact_index(entries) -> None:
    table = Table(title="Artifact Index")
    table.add_column("Kind")
    table.add_column("ID")
    table.add_column("Project")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Timestamp")
    table.add_column("Artifact Path")
    for item in entries:
        identifier = item.get("run_id")
        if identifier is None:
            identifier = item.get("task_id")
        table.add_row(
            str(item.get("kind", "")),
            str(identifier or ""),
            str(item.get("project_name", "")),
            str(item.get("task_name", ""))[:80],
            str(item.get("status", "")),
            str(item.get("timestamp", "")),
            str(item.get("artifact_path", "")),
        )
    if not entries:
        table.add_row("No artifacts", "", "", "", "", "", "")
    console.print(table)


def render_health(snapshot) -> None:
    status = str(snapshot.get("status", "unknown")).upper()
    success_rate = snapshot.get("autonomous_success_rate")
    median_retries = snapshot.get("median_retries_per_task")
    console.print(
        Panel(
            (
                f"Project: {snapshot.get('project_name') or 'all projects'}\n"
                f"Autonomous success rate (last 100 tasks): "
                f"{f'{float(success_rate) * 100:.0f}%' if success_rate is not None else 'n/a'}\n"
                f"Median retries per task: {f'{float(median_retries):.1f}' if median_retries is not None else 'n/a'}\n"
                f"Run graph deadlocks: {snapshot.get('run_graph_deadlocks', 0)}\n"
                f"Step timeouts: {snapshot.get('step_timeouts', 0)}\n"
                f"Recent eval failures: {snapshot.get('recent_eval_failures', 0)}\n"
                f"Stale tasks detected: {snapshot.get('stale_tasks_detected', 0)}\n"
                f"Artifact store size: {snapshot.get('artifact_store_size', 0)} runs\n"
                f"Workspace watchers: {snapshot.get('workspace_watchers', 'unknown')}\n\n"
                f"Status: {status}"
            ),
            title="BOSS System Health",
            expand=False,
        )
    )
    reasons = snapshot.get("status_reasons") or []
    if reasons:
        console.print("Notes:")
        for item in reasons[:5]:
            console.print(f"- {item}")


def render_metrics(snapshot) -> None:
    run_graph = snapshot.get("run_graph", {}) or {}
    avg_nodes = run_graph.get("avg_nodes_per_run")
    console.print(
        Panel(
            (
                f"Project: {snapshot.get('project_name') or 'all projects'}\n"
                f"Task runs recorded: {snapshot.get('task_runs_recorded', 0)}\n"
                f"Eval runs recorded: {snapshot.get('eval_runs_recorded', 0)}\n"
                f"Artifacts stored: {snapshot.get('artifacts_stored', 0)}\n"
                f"Benchmarks executed: {snapshot.get('benchmarks_executed', 0)}\n"
                f"Experiments executed: {snapshot.get('experiments_executed', 0)}"
            ),
            title="BOSS Metrics",
            expand=False,
        )
    )
    runtime = Table(title="Agent Runtime")
    runtime.add_column("Agent")
    runtime.add_column("Runs", justify="right")
    runtime.add_column("Avg Seconds", justify="right")
    runtime.add_column("Success", justify="right")
    for item in snapshot.get("agent_runtime", []):
        runtime.add_row(
            str(item.get("role", "")),
            str(item.get("run_count", 0)),
            f"{float(item.get('avg_duration_seconds')):.2f}" if item.get("avg_duration_seconds") is not None else "n/a",
            f"{float(item.get('success_rate')) * 100:.0f}%" if item.get("success_rate") is not None else "n/a",
        )
    if not snapshot.get("agent_runtime"):
        runtime.add_row("-", "0", "n/a", "n/a")
    console.print(runtime)

    console.print(
        Panel(
            (
                f"Avg nodes per run: {f'{float(avg_nodes):.2f}' if avg_nodes is not None else 'n/a'}\n"
                f"Parallel runs: {run_graph.get('parallel_mode', 'unknown')} ({run_graph.get('parallel_runs', 0)} observed)\n"
                f"Retries triggered: {run_graph.get('retries_triggered', 0)}\n"
                f"Token usage: {int((snapshot.get('token_usage', {}) or {}).get('total_tokens', 0))}"
            ),
            title="Run Graph",
            expand=False,
        )
    )
    if snapshot.get("estimated_cost_usd") is not None:
        console.print(f"Estimated Cost: ${float(snapshot['estimated_cost_usd']):.4f}")


def render_runs(entries) -> None:
    table = Table(title="Recent Runs")
    table.add_column("Flag")
    table.add_column("Kind")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Project")
    table.add_column("Title")
    table.add_column("Timestamp")
    for item in entries:
        table.add_row(
            str(item.get("symbol", "")),
            str(item.get("kind", "")),
            str(item.get("identifier", "")),
            str(item.get("status", "")),
            str(item.get("project_name", "")),
            str(item.get("title", ""))[:80],
            str(item.get("timestamp", "")),
        )
    if not entries:
        table.add_row("", "", "", "", "", "No runs recorded", "")
    console.print(table)


def render_run_details(result) -> None:
    summary = result.get("summary", {}) or {}
    console.print(
        Panel(
            (
                f"Kind: {result.get('kind', '')}\n"
                f"Identifier: {result.get('identifier', '')}\n"
                f"Project: {result.get('project_name', '')}\n"
                f"Status: {result.get('status', '')}\n"
                f"Artifact Path: {result.get('artifact_path', '')}"
            ),
            title="Run Details",
            expand=False,
        )
    )
    if result.get("kind") == "experiment":
        variants = result.get("variants", []) or []
        table = Table(title="Experiment Variants")
        table.add_column("Variant")
        table.add_column("Status")
        table.add_column("Eval Run")
        table.add_column("Runtime", justify="right")
        for item in variants:
            table.add_row(
                str(item.get("variant_id", "")),
                str(item.get("status", "")),
                str(item.get("eval_run_id") or ""),
                f"{float(item.get('runtime_seconds', 0.0)):.2f}s",
            )
        if not variants:
            table.add_row("No variants", "", "", "0.00s")
        console.print(table)
        return
    runtime_seconds = summary.get("runtime_seconds")
    runtime_display = f"{float(runtime_seconds):.2f}s" if runtime_seconds is not None else "n/a"
    console.print(
        Panel(
            (
                f"Task: {summary.get('task') or summary.get('suite_name') or ''}\n"
                f"Graph nodes: {summary.get('graph_nodes', 'n/a')}\n"
                f"Retries: {summary.get('retries', 'n/a')}\n"
                f"Runtime: {runtime_display}"
            ),
            title="Summary",
            expand=False,
        )
    )
    analysis = result.get("analysis") or {}
    if result.get("kind") == "build_task" and analysis.get("run_graph"):
        console.print(Panel(json.dumps(analysis["run_graph"], indent=2, default=str), title="Run Graph", expand=False))
    if result.get("kind") == "evaluation_run" and result.get("run") is not None:
        run = result["run"]
        console.print(f"Eval Result: {run.status} ({run.passed_tasks}/{run.total_tasks} tasks passed)")


def render_replay_result(result: dict[str, object]) -> None:
    console.print(
        Panel(
            (
                f"Mode: {result.get('mode', '')}\n"
                f"Kind: {result.get('kind', '')}\n"
                f"Identifier: {result.get('identifier', '')}\n"
                f"Artifact Path: {result.get('artifact_path', '')}"
            ),
            title="Run Replay",
            expand=False,
        )
    )
    if result.get("summary"):
        console.print(Panel(json.dumps(result["summary"], indent=2, default=str), title="Summary", expand=False))
    if result.get("plan"):
        console.print(Panel(json.dumps(result["plan"], indent=2, default=str), title="Plan", expand=False))
    if result.get("run_graph"):
        console.print(Panel(json.dumps(result["run_graph"], indent=2, default=str), title="Run Graph", expand=False))
    if result.get("step_results"):
        console.print(Panel(json.dumps(result["step_results"], indent=2, default=str), title="Step Results", expand=False))
    if result.get("task_artifacts"):
        console.print(Panel(json.dumps(result["task_artifacts"], indent=2, default=str), title="Task Artifacts", expand=False))
    if result.get("available_files"):
        console.print("Artifact Files:")
        for item in result["available_files"][:40]:
            console.print(f"- {item}")
    if result.get("replayed_run_id") is not None:
        console.print(f"Replayed Eval Run: {result['replayed_run_id']} [{result.get('status', '')}]")
    if result.get("replayed_task_id") is not None:
        console.print(f"Replayed Build Task: {result['replayed_task_id']} [{result.get('status', '')}]")


def render_lab_experiments(experiments) -> None:
    table = Table(title="Lab Experiments")
    table.add_column("Experiment")
    table.add_column("Project")
    table.add_column("Status")
    table.add_column("Metric")
    table.add_column("Recommended")
    table.add_column("Created")
    for item in experiments:
        table.add_row(
            str(item.get("experiment_id", "")),
            str(item.get("project_name", "")),
            str(item.get("status", "")),
            str(item.get("primary_metric") or "runtime_seconds"),
            str(item.get("recommended_variant_id") or ""),
            str(item.get("created_at", "")),
        )
    if not experiments:
        table.add_row("No experiments", "", "", "", "", "")
    console.print(table)


def render_lab_results(experiment) -> None:
    console.print(
        Panel(
            (
                f"Experiment: {experiment.get('experiment_id', '')}\n"
                f"Project: {experiment.get('project_name', '')}\n"
                f"Goal: {experiment.get('goal', '')}\n"
                f"Status: {experiment.get('status', '')}\n"
                f"Primary Metric: {experiment.get('primary_metric') or 'runtime_seconds'} "
                f"({experiment.get('metric_direction', 'minimize')})\n"
                f"Recommended: {experiment.get('recommended_variant_id') or 'none'}"
            ),
            title="Lab Experiment",
            expand=False,
        )
    )
    recommendation_reason = experiment.get("recommendation_reason")
    if recommendation_reason:
        console.print(f"Recommendation: {recommendation_reason}")

    table = Table(title="Variants")
    table.add_column("Variant")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Runtime", justify="right")
    table.add_column("Metric")
    table.add_column("Files")
    for item in experiment.get("variants", []):
        metric_name = experiment.get("primary_metric") or "runtime_seconds"
        metric_value = item.get("metrics", {}).get(metric_name, item.get("runtime_seconds"))
        metric_display = f"{metric_value:.4f}" if isinstance(metric_value, (int, float)) else str(metric_value or "")
        table.add_row(
            str(item.get("variant_id", "")),
            str(item.get("kind", "")),
            str(item.get("status", "")),
            f"{float(item.get('runtime_seconds', 0.0)):.2f}s",
            metric_display,
            ", ".join(item.get("changed_files", [])[:3]) or "-",
        )
    if not experiment.get("variants"):
        table.add_row("No variants", "", "", "0.00s", "", "-")
    console.print(table)


def render_lab_apply(result) -> None:
    console.print(Panel(str(result.get("message", "")), title="Lab Apply", expand=False))
    if result.get("apply_method"):
        console.print(f"Apply Method: {result['apply_method']}")
    if result.get("commit_revision"):
        console.print(f"Commit: {result['commit_revision']}")
    if result.get("metrics"):
        console.print("Metrics:")
        for key, value in sorted(result["metrics"].items()):
            console.print(f"- {key}: {value}")
    if result.get("applied_files"):
        console.print(f"Applied Files: {', '.join(result['applied_files'])}")
    if result.get("skipped_files"):
        console.print(f"Skipped Files: {', '.join(result['skipped_files'])}")
    diff_preview = str(result.get("diff_preview", "")).strip()
    if diff_preview:
        console.print(Panel(Syntax(diff_preview, "diff", word_wrap=False), title="Apply Diff", expand=False))


def ensure_task(task: str) -> str:
    if not task.strip():
        raise typer.BadParameter("Task text cannot be empty.")
    return task


def exit_with_error(exc: Exception) -> None:
    console.print(f"[red]{exc}[/red]")
    raise typer.Exit(code=1)


@app.command()
def start(
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip overwrite prompts."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    orchestrator = build_orchestrator(verbose=verbose)
    render_status(orchestrator)
    console.print("Type `help` for commands, `exit` to quit.")

    while True:
        raw = Prompt.ask(">")
        if not raw.strip():
            continue
        if raw.strip().lower() in {"exit", "quit"}:
            break
        try:
            handle_repl_command(raw, orchestrator, auto_approve=auto_approve)
        except Exception as exc:  # pragma: no cover - CLI surface
            console.print(f"[red]{exc}[/red]")


@app.command()
def project(
    name: str = typer.Argument(..., help="Project name inside ./projects, or 'workspace' for broad workspace mode."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        context = orchestrator.set_active_project(name)
        render_status(orchestrator)
        title = "Workspace Mode" if context.name == "__workspace__" else f"Project {name}"
        console.print(Panel(context.summary, title=title, expand=False))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def scan(
    name: Optional[str] = typer.Argument(None, help="Optional project name. Defaults to the active project."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        context = orchestrator.scan_project(name)
        console.print(Panel(context.summary, title=f"Scan: {context.name}", expand=False))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def status(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_status(orchestrator)
        projects = orchestrator.available_projects()
        console.print(f"Projects: {', '.join(projects) if projects else 'None found'}")
    except Exception as exc:
        exit_with_error(exc)


@app.command("next")
def next_action(
    limit: int = typer.Option(5, "--limit", min=1, max=20, help="Maximum number of recommendations."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        snapshot = orchestrator.project_brain_snapshot()
        render_next_recommendations(str(snapshot["project_name"]), orchestrator.next_recommendations(limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def roadmap(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_roadmap(orchestrator.project_roadmap())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def risks(
    limit: int = typer.Option(8, "--limit", min=1, max=20, help="Maximum number of risks."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        snapshot = orchestrator.project_brain_snapshot()
        render_risks(str(snapshot["project_name"]), orchestrator.project_risks(limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@app.command("index")
def index_project(
    force: bool = typer.Option(False, "--force", help="Re-index all files even if hashes match."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.index_project(force=force)
        render_index_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def search(
    query: str = typer.Argument(..., help="Semantic search query"),
    limit: int = typer.Option(8, "--limit", min=1, max=20, help="Maximum number of results."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        results = orchestrator.search(ensure_task(query), limit=limit)
        render_search_results(query, results)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def research(
    query: str = typer.Argument(..., help="Research query."),
    project: Optional[str] = typer.Option(None, "--project", help="Optional project scope."),
    no_web: bool = typer.Option(False, "--no-web", help="Disable web research and use local context only."),
    no_local: bool = typer.Option(False, "--no-local", help="Disable local workspace search and use web only."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        report = orchestrator.research(
            ensure_task(query),
            project_name=project,
            use_web=not no_web,
            use_local=not no_local,
        )
        render_research(report)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def portfolio(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_portfolio(orchestrator.portfolio_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def permissions(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_permissions(orchestrator.permissions_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def map(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        project_map = orchestrator.project_map()
        render_project_map(project_map)
    except Exception as exc:
        exit_with_error(exc)


@app.command("eval")
def evaluate(
    suite: str = typer.Argument(..., help="Path to an evaluation task suite YAML file."),
    project: Optional[str] = typer.Option(None, "--project", help="Override the project defined in the suite."),
    stop_on_failure: bool = typer.Option(
        False,
        "--stop-on-failure/--continue-on-failure",
        help="Stop after the first failed task instead of continuing through the suite.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.evaluate_suite(
            suite_path=suite,
            project_name=project,
            stop_on_failure=stop_on_failure,
        )
        render_eval_run(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("benchmark")
def benchmark(
    manifest: str = typer.Argument(..., help="Path to a benchmark manifest YAML file."),
    suite: list[str] = typer.Option(None, "--suite", help="Run only the named suite(s) from the manifest."),
    repeat: Optional[int] = typer.Option(None, "--repeat", min=1, help="Override suite repeat count for repeated benchmarking."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.benchmark_manifest(manifest, only_suites=list(suite or []), repeat_override=repeat)
        render_benchmark_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("golden")
def golden(
    suite: list[str] = typer.Option(None, "--suite", help="Run only the named golden suite(s)."),
    repeat: Optional[int] = typer.Option(None, "--repeat", min=1, help="Override suite repeat count."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.run_golden_tasks(only_suites=list(suite or []), repeat_override=repeat)
        render_benchmark_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("benchmark-sync")
def benchmark_sync(
    catalog: str = typer.Argument(..., help="Path to an external benchmark repo catalog YAML file."),
    repo: list[str] = typer.Option(None, "--repo", help="Sync only the named repo(s) from the catalog."),
    update: bool = typer.Option(False, "--update", help="Fetch and update repos that already exist locally."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.sync_external_benchmark_repos(catalog, only_repos=list(repo or []), update=update)
        render_benchmark_sync(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("artifacts")
def artifacts(
    kind: Optional[str] = typer.Option(None, "--kind", help="Optional artifact kind filter."),
    project: Optional[str] = typer.Option(None, "--project", help="Optional project filter."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum number of artifact entries."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_artifact_index(orchestrator.artifact_index(kind=kind, project_name=project, limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@app.command("health")
def health(
    project: Optional[str] = typer.Option(None, "--project", help="Optional project override."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_health(orchestrator.health_snapshot(project_name=project))
    except Exception as exc:
        exit_with_error(exc)


@app.command("metrics")
def metrics(
    project: Optional[str] = typer.Option(None, "--project", help="Optional project override."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_metrics(orchestrator.metrics_snapshot(project_name=project))
    except Exception as exc:
        exit_with_error(exc)


@app.command("runs")
def runs(
    project: Optional[str] = typer.Option(None, "--project", help="Optional project override."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum number of runs to list."),
    details: Optional[str] = typer.Option(None, "--details", help="Show details for a specific run or experiment."),
    kind: str = typer.Option("auto", "--kind", help="Optional kind for details: auto, build, evaluation, experiment."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        if details:
            render_run_details(orchestrator.run_details(details, kind=kind, project_name=project))
            return
        render_runs(orchestrator.recent_runs(project_name=project, limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@runs_app.command("replay")
def run_replay(
    identifier: int = typer.Argument(..., help="Eval run id or build task id."),
    kind: str = typer.Option("auto", "--kind", help="Artifact kind: auto, evaluation, or build."),
    mode: str = typer.Option("analysis", "--mode", help="Replay mode: dry-run, analysis, or full."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Auto-approve when running a full build replay."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_replay_result(
            orchestrator.replay_run(
                identifier,
                kind=kind,
                mode=mode,
                auto_approve=auto_approve,
            )
        )
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("status")
def brain_status(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_snapshot(orchestrator.project_brain_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("rules")
def brain_rules(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_rules(orchestrator.brain_rules())
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("proposals")
def brain_proposals(
    status: Optional[str] = typer.Option("pending", "--status", help="Optional proposal status filter."),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Maximum number of proposals."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        normalized_status = None if str(status).strip().lower() == "all" else status
        render_brain_proposals(orchestrator.brain_proposals(status=normalized_status, limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("add-rule")
def brain_add_rule(
    rule: str = typer.Argument(..., help="Brain rule text to add to the active project."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_action_result(
            orchestrator.add_brain_rule(rule),
            title="Brain Rule Added",
        )
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("remove-rule")
def brain_remove_rule(
    rule: str = typer.Argument(..., help="Exact brain rule text to remove from the active project."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_action_result(
            orchestrator.remove_brain_rule(rule),
            title="Brain Rule Removed",
        )
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("approve")
def brain_approve(
    proposal_id: int = typer.Argument(..., help="Pending brain proposal id."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_approval(orchestrator.approve_brain_proposal(proposal_id))
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("reject")
def brain_reject(
    proposal_id: int = typer.Argument(..., help="Pending brain proposal id."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_action_result(
            orchestrator.reject_brain_proposal(proposal_id),
            title="Brain Proposal Rejected",
        )
    except Exception as exc:
        exit_with_error(exc)


@brain_app.command("reset")
def brain_reset(
    project_name: Optional[str] = typer.Argument(None, help="Optional project name. Defaults to the active project."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_brain_action_result(
            orchestrator.reset_project_brain(project_name=project_name),
            title="Project Brain Reset",
        )
    except Exception as exc:
        exit_with_error(exc)


@roots_app.command("list")
def roots_list(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_roots(orchestrator.workspace_roots_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@roots_app.command("add")
def roots_add(
    name: str = typer.Argument(..., help="Stable root name."),
    path: str = typer.Argument(..., help="Absolute path for this root."),
    mode: str = typer.Option("projects", "--mode", help="Root mode: search, projects, or both."),
    include_root: bool = typer.Option(False, "--include-root", help="Treat the root itself as a project."),
    discover_children: bool = typer.Option(True, "--discover-children/--no-discover-children", help="Discover child projects under this root."),
    max_depth: int = typer.Option(1, "--max-depth", min=0, max=5, help="Maximum discovery depth."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        orchestrator.add_workspace_root(
            name=name,
            path=path,
            mode=mode,
            include_root=include_root,
            discover_children=discover_children,
            max_depth=max_depth,
        )
        render_roots(orchestrator.workspace_roots_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@roots_app.command("remove")
def roots_remove(
    name: str = typer.Argument(..., help="Root name to remove."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        orchestrator.remove_workspace_root(name)
        render_roots(orchestrator.workspace_roots_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@mcp_app.command("list")
def mcp_list(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_mcp(orchestrator.mcp_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Connector name."),
    transport: str = typer.Argument(..., help="Transport: stdio or http/https."),
    target: str = typer.Argument(..., help="Command or URL target."),
    arg: list[str] = typer.Option(None, "--arg", help="Optional connector args."),
    capability: list[str] = typer.Option(None, "--capability", help="Optional capability labels."),
    disabled: bool = typer.Option(False, "--disabled", help="Register disabled."),
    description: str = typer.Option("", "--description", help="Optional description."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        orchestrator.add_mcp_connector(
            name=name,
            transport=transport,
            target=target,
            args=list(arg or []),
            capabilities=list(capability or []),
            enabled=not disabled,
            description=description,
        )
        render_mcp(orchestrator.mcp_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Connector name to remove."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        orchestrator.remove_mcp_connector(name)
        render_mcp(orchestrator.mcp_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@lab_app.command("start")
def lab_start(
    goal: str = typer.Argument(..., help="Experiment goal or optimization target."),
    variant: list[str] = typer.Option(None, "--variant", help="Optional explicit candidate variant descriptions."),
    benchmark_command: list[str] = typer.Option(None, "--benchmark-command", help="Validation or benchmark command to run for each variant."),
    allow_path: list[str] = typer.Option(None, "--allow-path", help="Restrict changes to these paths."),
    metric: Optional[str] = typer.Option(None, "--metric", help="Primary metric name to compare across variants."),
    maximize: bool = typer.Option(False, "--maximize", help="Prefer larger metric values instead of smaller ones."),
    auto_approve: bool = typer.Option(True, "--auto-approve/--require-approval", help="Allow autonomous variant execution without write prompts."),
    max_iterations: int = typer.Option(5, "--max-iterations", min=1, max=20, help="Max fix attempts per candidate variant step."),
    deep: bool = typer.Option(False, "--deep", help="Escalate candidate builds to the stronger pro engineer model."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        experiment = orchestrator.start_lab_experiment(
            ensure_task(goal),
            variants=list(variant or []),
            benchmark_commands=list(benchmark_command or []),
            allowed_paths=list(allow_path or []),
            primary_metric=metric,
            metric_direction="maximize" if maximize else "minimize",
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            deep=deep,
        )
        render_lab_results(experiment)
    except Exception as exc:
        exit_with_error(exc)


@lab_app.command("list")
def lab_list(
    limit: int = typer.Option(20, "--limit", min=1, max=100, help="Number of experiments to show."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_lab_experiments(orchestrator.lab_experiments(limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@lab_app.command("results")
def lab_results(
    experiment_id: str = typer.Argument(..., help="Experiment identifier."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_lab_results(orchestrator.lab_results(experiment_id))
    except Exception as exc:
        exit_with_error(exc)


@lab_app.command("apply")
def lab_apply(
    variant_id: str = typer.Argument(..., help="Variant identifier."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Apply without confirmation."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_lab_apply(
            orchestrator.apply_lab_variant(
                variant_id,
                auto_approve=auto_approve,
                confirm_callback=confirm_action,
                preview_callback=render_lab_apply,
            )
        )
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def open(
    file: str = typer.Argument(..., help="File path to open"),
    line: Optional[int] = typer.Option(None, "--line", min=1, help="Optional line number."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.open_file(file, line=line)
        console.print(f"Opened: {file}")
        if result.get("stderr"):
            console.print(str(result.get("stderr")))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def jump(
    symbol: str = typer.Argument(..., help="Symbol name to open"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.jump_to_symbol(symbol)
        if result.get("found") is False:
            console.print(str(result.get("message", f"Symbol '{symbol}' not found.")))
            return
        console.print(f"Jumped to: {symbol}")
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def dashboard(
    task_id: Optional[int] = typer.Argument(None, help="Optional task id"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        console.print(orchestrator.dashboard(task_id=task_id))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def tools(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_tools(orchestrator.available_tools())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def plugins(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_plugins(orchestrator.available_plugins())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def memory(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_memory_snapshot(orchestrator.memory_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def solutions(
    query: Optional[str] = typer.Argument(None, help="Optional semantic query"),
    limit: int = typer.Option(20, "--limit", min=1, max=50, help="Maximum number of solutions."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_solutions(orchestrator.solutions(query=query, limit=limit))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def learn(
    force: bool = typer.Option(True, "--force/--no-force", help="Force a fresh learning pass."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_memory_snapshot(orchestrator.learn_project(force=force))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def graph(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_knowledge_graph(orchestrator.knowledge_graph_snapshot())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def test(
    workdir: str = typer.Option(".", "--workdir", help="Optional project-relative working directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_test_result(orchestrator.run_tests(workdir=workdir))
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def models(
    select: Optional[str] = typer.Option(None, "--select", help="Select a discovered local model by name."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Optional backend when selecting a local model."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        if select:
            chosen = orchestrator.select_local_model(model=select, backend=backend)
            console.print(f"Selected local model: {chosen['backend']}:{chosen['model']}")
        render_model_catalog(orchestrator.model_catalog())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def improve(
    roles: Optional[str] = typer.Option(None, "--roles", help="Comma-separated agent roles to optimize."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyze without writing optimized prompt files."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        selected_roles = [item.strip() for item in (roles or "").split(",") if item.strip()] or None
        render_evolution_report(
            orchestrator.improve(
                roles=selected_roles,
                write_files=not dry_run,
            )
        )
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def evolve(
    plugin_request: Optional[str] = typer.Option(None, "--plugin-request", help="Generate a new plugin scaffold."),
    auto_confirm: bool = typer.Option(False, "--auto-confirm", help="Skip plugin generation confirmation."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        report = orchestrator.evolve(
            plugin_request=plugin_request,
            auto_confirm=auto_confirm,
            confirm_callback=confirm_action,
        )
        render_evolution_report(report)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def voice(
    text: Optional[str] = typer.Option(None, "--text", help="Use text instead of live microphone input."),
    execute: bool = typer.Option(False, "--execute", help="Execute the parsed command."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Pass through auto-approve to executed commands."),
    timeout: int = typer.Option(5, "--timeout", min=1, max=30, help="Microphone listen timeout in seconds."),
    phrase_time_limit: int = typer.Option(15, "--phrase-time-limit", min=1, max=120, help="Phrase length limit in seconds."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.voice_command(
            transcript=text,
            timeout=timeout,
            phrase_time_limit=phrase_time_limit,
        )
        render_voice_result(result)
        if execute:
            handle_repl_command(str(result.get("command", "")), orchestrator, auto_approve=auto_approve)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def swarm(
    task: Optional[str] = typer.Argument(None, help="Optional swarm task to start"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--require-approval", help="Swarm writes are non-interactive by default."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the swarm run to finish."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        if task is None:
            render_swarm_tasks(orchestrator.swarm_status())
            return
        run = orchestrator.start_swarm(ensure_task(task), auto_approve=auto_approve)
        console.print(f"Started swarm run {run['run_id']} for project {run['project_name']}.")
        if wait:
            while True:
                snapshot = next((item for item in orchestrator.swarm_runs() if item["run_id"] == run["run_id"]), None)
                if snapshot is None:
                    break
                if snapshot["status"] in {"completed", "failed", "cancelled"}:
                    render_swarm_tasks({"runs": [snapshot], "tasks": orchestrator.swarm_tasks(run["run_id"]), "stats": {}})
                    break
                time.sleep(0.5)
        else:
            render_swarm_tasks({"runs": [run], "tasks": orchestrator.swarm_tasks(run["run_id"]), "stats": {}})
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def agents(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_swarm_agents(orchestrator.available_agents())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def tasks(verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging.")) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_swarm_tasks(orchestrator.swarm_status())
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface for the web server."),
    port: int = typer.Option(8080, "--port", min=1, max=65535, help="Port for the web server."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not automatically open the browser."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        from boss.web.server import run_server

        run_server(orchestrator, host=host, port=port, open_browser=not no_browser)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def chat(
    message: str = typer.Argument(..., help="Natural-language message for BOSS."),
    execute: bool = typer.Option(False, "--execute", help="Execute the interpreted action instead of only replying."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Auto-approve code changes for executed chat actions."),
    intent: Optional[str] = typer.Option(None, "--intent", help="Override the inferred intent."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        response = orchestrator.chat(
            message,
            execute=execute,
            auto_approve=auto_approve,
            intent_override=intent,
        )
        render_chat_response(response)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def build(
    task: str = typer.Argument(..., help="Autonomous development task"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip diff confirmation prompts."),
    deep: bool = typer.Option(False, "--deep", help="Escalate the engineer role to the stronger pro model."),
    no_commit: bool = typer.Option(False, "--no-commit", help="Skip git commits between steps."),
    max_iterations: int = typer.Option(10, "--max-iterations", min=1, max=50, help="Max fix attempts per step."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.build(
            ensure_task(task),
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            commit_changes=not no_commit,
            deep=deep,
        )
        render_build_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("autobuild")
def autobuild(
    task: str = typer.Argument(..., help="Autonomous development task"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip diff confirmation prompts."),
    deep: bool = typer.Option(False, "--deep", help="Escalate the engineer role to the stronger pro model."),
    no_commit: bool = typer.Option(False, "--no-commit", help="Skip git commits between steps."),
    max_iterations: int = typer.Option(10, "--max-iterations", min=1, max=50, help="Max fix attempts per step."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    build(
        task=task,
        auto_approve=auto_approve,
        deep=deep,
        no_commit=no_commit,
        max_iterations=max_iterations,
        verbose=verbose,
    )


@app.command("ship")
def ship(
    task: str = typer.Argument(..., help="Ship-ready autonomous development task"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip diff confirmation prompts."),
    deep: bool = typer.Option(False, "--deep", help="Escalate the engineer role to the stronger pro model."),
    no_commit: bool = typer.Option(False, "--no-commit", help="Skip git commits between steps."),
    no_push: bool = typer.Option(False, "--no-push", help="Skip git push after commit."),
    max_iterations: int = typer.Option(10, "--max-iterations", min=1, max=50, help="Max fix attempts per step."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.ship(
            ensure_task(task),
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            commit_changes=not no_commit,
            push_changes=not no_push,
            deep=deep,
        )
        render_build_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command("task-status")
def task_status(
    task_id: Optional[int] = typer.Argument(None, help="Optional task id"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        render_task_status(orchestrator.task_status(task_id=task_id))
    except Exception as exc:
        exit_with_error(exc)


@app.command("loop-status")
def loop_status(
    task_id: Optional[int] = typer.Argument(None, help="Optional task id"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    task_status(task_id=task_id, verbose=verbose)


@app.command()
def diff(
    run_id: Optional[int] = typer.Argument(None, help="Run id to inspect. Defaults to the current or latest task."),
    kind: str = typer.Option("auto", "--kind", help="Run kind: auto, build, or evaluation."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        target = run_id
        if target is None:
            current = orchestrator.task_status()
            if current is None:
                raise RuntimeError("No current or recent task is available for diff inspection.")
            target = int(current["id"])
        render_diff_bundle(orchestrator.run_diff(target, kind=kind))
    except Exception as exc:
        exit_with_error(exc)


@app.command("tail")
def tail(
    task_id: Optional[int] = typer.Argument(None, help="Optional task id"),
    interval: float = typer.Option(1.0, "--interval", min=0.2, help="Refresh interval in seconds."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        with Live(console=console, refresh_per_second=max(1, int(round(1 / interval)))) as live:
            while True:
                task = orchestrator.task_status(task_id=task_id)
                live.update(build_task_status_renderable(task))
                if task is None or str(task.get("status", "")).lower() not in {"running", "planning", "retrying", "queued"}:
                    break
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("Stopped tail.")
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def stop(
    task_id: Optional[int] = typer.Argument(None, help="Optional task id"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        task = orchestrator.stop_task(task_id=task_id)
        if task is None:
            console.print("No running task found.")
            return
        console.print(f"Stop requested for task #{task['id']}.")
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def approve(
    run_id: Optional[int] = typer.Argument(None, help="Build run id to approve. Defaults to the current or latest task."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        target = run_id
        if target is None:
            current = orchestrator.task_status()
            if current is None:
                raise RuntimeError("No current or recent build task is available to approve.")
            target = int(current["id"])
        result = orchestrator.approve_run_commit(target, kind="build")
        console.print(result.get("message") or "Commit approval finished.")
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def reject(
    run_id: Optional[int] = typer.Argument(None, help="Build run id to reject. Defaults to the current or latest task."),
    reason: str = typer.Option("Commit rejected. Revise the changes before trying again.", "--reason", help="Optional rejection note."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        target = run_id
        if target is None:
            current = orchestrator.task_status()
            if current is None:
                raise RuntimeError("No current or recent build task is available to reject.")
            target = int(current["id"])
        result = orchestrator.reject_run_commit(target, kind="build", reason=reason)
        console.print(result.get("message") or "Commit rejected.")
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def plan(
    task: str = typer.Argument(..., help="Planning request"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.plan(ensure_task(task))
        render_plan(result.text)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def code(
    task: str = typer.Argument(..., help="Implementation request"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip overwrite prompts."),
    deep: bool = typer.Option(False, "--deep", help="Escalate the engineer role to the stronger pro model."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.code(
            ensure_task(task),
            auto_approve=auto_approve,
            confirm_overwrite=None if auto_approve else confirm_overwrite,
            deep=deep,
        )
        render_code_result(result)
    except Exception as exc:
        exit_with_error(exc)


@app.command()
def audit(
    task: str = typer.Argument("Audit the active project for defects and risks.", help="Audit request"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging."),
) -> None:
    try:
        orchestrator = build_orchestrator(verbose=verbose)
        result = orchestrator.audit(ensure_task(task))
        render_audit_result(result)
    except Exception as exc:
        exit_with_error(exc)


def handle_repl_command(raw: str, orchestrator: BOSSOrchestrator, auto_approve: bool) -> None:
    parts = shlex.split(raw)
    command = parts[0]
    args = parts[1:]

    if command == "help":
        console.print(
            "Commands: project <name>, scan [name], status, next, roadmap, risks, health, metrics, runs [--details <id>], research <query>, portfolio, permissions, roots <list|add|remove>, mcp <list|add|remove>, brain status, brain rules, brain proposals, brain add-rule <text>, brain remove-rule <text>, brain approve <id>, brain reject <id>, brain reset [project], index, search <query>, map, eval <suite.yaml>, benchmark <manifest.yaml>, golden [--suite <name>], benchmark-sync <catalog.yaml>, artifacts, run replay <id>, lab start <goal>, lab list, lab results <id>, lab apply <variant>, memory, solutions [query], learn, graph, test, models, improve, evolve, voice, swarm [task], agents, tasks, web, open <file>, jump <symbol>, dashboard, tools, plugins, build <task>, autobuild <task>, ship <task>, task-status, loop-status, tail, stop, plan <task>, code <task>, audit [task], exit"
        )
        return
    if command == "status":
        render_status(orchestrator)
        return
    if command == "next":
        snapshot = orchestrator.project_brain_snapshot()
        render_next_recommendations(str(snapshot["project_name"]), orchestrator.next_recommendations())
        return
    if command == "roadmap":
        render_roadmap(orchestrator.project_roadmap())
        return
    if command == "risks":
        snapshot = orchestrator.project_brain_snapshot()
        render_risks(str(snapshot["project_name"]), orchestrator.project_risks())
        return
    if command == "health":
        render_health(orchestrator.health_snapshot())
        return
    if command == "metrics":
        render_metrics(orchestrator.metrics_snapshot())
        return
    if command == "project":
        if not args:
            raise ValueError("Usage: project <name>")
        context = orchestrator.set_active_project(args[0])
        title = "Workspace Mode" if context.name == "__workspace__" else f"Project {args[0]}"
        console.print(Panel(context.summary, title=title, expand=False))
        return
    if command == "scan":
        context = orchestrator.scan_project(args[0] if args else None)
        console.print(Panel(context.summary, title=f"Scan: {context.name}", expand=False))
        return
    if command == "index":
        result = orchestrator.index_project(force="--force" in args)
        render_index_result(result)
        return
    if command == "search":
        limit = 8
        cleaned_args: list[str] = []
        skip_next = False
        for index, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--limit" and index + 1 < len(args):
                limit = int(args[index + 1])
                skip_next = True
                continue
            cleaned_args.append(arg)
        query = " ".join(cleaned_args)
        results = orchestrator.search(ensure_task(query), limit=limit)
        render_search_results(query, results)
        return
    if command == "research":
        query = " ".join(args)
        render_research(orchestrator.research(ensure_task(query)))
        return
    if command == "portfolio":
        render_portfolio(orchestrator.portfolio_snapshot())
        return
    if command == "permissions":
        render_permissions(orchestrator.permissions_snapshot())
        return
    if command == "map":
        render_project_map(orchestrator.project_map())
        return
    if command == "eval":
        if not args:
            raise ValueError("Usage: eval <suite.yaml>")
        render_eval_run(orchestrator.evaluate_suite(args[0]))
        return
    if command == "benchmark":
        if not args:
            raise ValueError("Usage: benchmark <manifest.yaml>")
        render_benchmark_result(orchestrator.benchmark_manifest(args[0]))
        return
    if command == "golden":
        suites: list[str] = []
        repeat_override: Optional[int] = None
        skip_next = False
        for index, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--suite" and index + 1 < len(args):
                suites.append(args[index + 1])
                skip_next = True
                continue
            if arg == "--repeat" and index + 1 < len(args):
                repeat_override = int(args[index + 1])
                skip_next = True
                continue
            raise ValueError("Usage: golden [--suite <name>] [--repeat <n>]")
        render_benchmark_result(orchestrator.run_golden_tasks(only_suites=suites, repeat_override=repeat_override))
        return
    if command == "benchmark-sync":
        if not args:
            raise ValueError("Usage: benchmark-sync <catalog.yaml>")
        render_benchmark_sync(orchestrator.sync_external_benchmark_repos(args[0]))
        return
    if command == "artifacts":
        kind = None
        project = None
        limit = 20
        cleaned_args: list[str] = []
        skip_next = False
        for index, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--kind" and index + 1 < len(args):
                kind = args[index + 1]
                skip_next = True
                continue
            if arg == "--project" and index + 1 < len(args):
                project = args[index + 1]
                skip_next = True
                continue
            if arg == "--limit" and index + 1 < len(args):
                limit = int(args[index + 1])
                skip_next = True
                continue
            cleaned_args.append(arg)
        if cleaned_args:
            raise ValueError("Usage: artifacts [--kind <kind>] [--project <name>] [--limit <n>]")
        render_artifact_index(orchestrator.artifact_index(kind=kind, project_name=project, limit=limit))
        return
    if command == "runs":
        project = None
        limit = 20
        details = None
        kind = "auto"
        cleaned_args: list[str] = []
        skip_next = False
        for index, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "--project" and index + 1 < len(args):
                project = args[index + 1]
                skip_next = True
                continue
            if arg == "--limit" and index + 1 < len(args):
                limit = int(args[index + 1])
                skip_next = True
                continue
            if arg == "--details" and index + 1 < len(args):
                details = args[index + 1]
                skip_next = True
                continue
            if arg == "--kind" and index + 1 < len(args):
                kind = args[index + 1]
                skip_next = True
                continue
            cleaned_args.append(arg)
        if cleaned_args:
            raise ValueError("Usage: runs [--project <name>] [--limit <n>] [--details <id>] [--kind <kind>]")
        if details:
            render_run_details(orchestrator.run_details(details, kind=kind, project_name=project))
            return
        render_runs(orchestrator.recent_runs(project_name=project, limit=limit))
        return
    if command == "run":
        if not args:
            raise ValueError("Usage: run replay <id> [--kind <kind>] [--mode <mode>] [--auto-approve]")
        subcommand = args[0]
        subargs = args[1:]
        if subcommand != "replay":
            raise ValueError(f"Unknown run command '{subcommand}'.")
        if not subargs:
            raise ValueError("Usage: run replay <id> [--kind <kind>] [--mode <mode>] [--auto-approve]")
        try:
            identifier = int(subargs[0])
        except ValueError as exc:
            raise ValueError("Replay id must be an integer.") from exc
        kind = "auto"
        mode = "analysis"
        replay_auto_approve = False
        skip_next = False
        for index, arg in enumerate(subargs[1:]):
            if skip_next:
                skip_next = False
                continue
            if arg == "--kind" and index + 2 < len(subargs):
                kind = subargs[index + 2]
                skip_next = True
                continue
            if arg == "--mode" and index + 2 < len(subargs):
                mode = subargs[index + 2]
                skip_next = True
                continue
            if arg == "--auto-approve":
                replay_auto_approve = True
                continue
            raise ValueError("Usage: run replay <id> [--kind <kind>] [--mode <mode>] [--auto-approve]")
        render_replay_result(
            orchestrator.replay_run(
                identifier,
                kind=kind,
                mode=mode,
                auto_approve=replay_auto_approve or auto_approve,
            )
        )
        return
    if command == "brain":
        if not args:
            render_brain_snapshot(orchestrator.project_brain_snapshot())
            return
        subcommand = args[0]
        subargs = args[1:]
        if subcommand == "status":
            render_brain_snapshot(orchestrator.project_brain_snapshot())
            return
        if subcommand == "rules":
            render_brain_rules(orchestrator.brain_rules())
            return
        if subcommand == "proposals":
            status_filter = "pending"
            limit = 20
            skip_next = False
            for index, arg in enumerate(subargs):
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--status" and index + 1 < len(subargs):
                    status_filter = subargs[index + 1]
                    skip_next = True
                    continue
                if arg == "--limit" and index + 1 < len(subargs):
                    limit = int(subargs[index + 1])
                    skip_next = True
                    continue
                raise ValueError("Usage: brain proposals [--status <status>] [--limit <n>]")
            normalized_status = None if status_filter.strip().lower() == "all" else status_filter
            render_brain_proposals(orchestrator.brain_proposals(status=normalized_status, limit=limit))
            return
        if subcommand == "add-rule":
            if not subargs:
                raise ValueError("Usage: brain add-rule <text>")
            render_brain_action_result(
                orchestrator.add_brain_rule(" ".join(subargs).strip()),
                title="Brain Rule Added",
            )
            return
        if subcommand == "remove-rule":
            if not subargs:
                raise ValueError("Usage: brain remove-rule <text>")
            render_brain_action_result(
                orchestrator.remove_brain_rule(" ".join(subargs).strip()),
                title="Brain Rule Removed",
            )
            return
        if subcommand == "approve":
            if not subargs:
                raise ValueError("Usage: brain approve <proposal_id>")
            render_brain_approval(orchestrator.approve_brain_proposal(int(subargs[0])))
            return
        if subcommand == "reject":
            if not subargs:
                raise ValueError("Usage: brain reject <proposal_id>")
            render_brain_action_result(
                orchestrator.reject_brain_proposal(int(subargs[0])),
                title="Brain Proposal Rejected",
            )
            return
        if subcommand == "reset":
            project_name = subargs[0] if subargs else None
            render_brain_action_result(
                orchestrator.reset_project_brain(project_name=project_name),
                title="Project Brain Reset",
            )
            return
        raise ValueError(f"Unknown brain command '{subcommand}'.")
    if command == "roots":
        if not args or args[0] == "list":
            render_roots(orchestrator.workspace_roots_snapshot())
            return
        subcommand = args[0]
        subargs = args[1:]
        if subcommand == "add":
            if len(subargs) < 2:
                raise ValueError("Usage: roots add <name> <path>")
            orchestrator.add_workspace_root(name=subargs[0], path=subargs[1])
            render_roots(orchestrator.workspace_roots_snapshot())
            return
        if subcommand == "remove":
            if not subargs:
                raise ValueError("Usage: roots remove <name>")
            orchestrator.remove_workspace_root(subargs[0])
            render_roots(orchestrator.workspace_roots_snapshot())
            return
        raise ValueError(f"Unknown roots command '{subcommand}'.")
    if command == "mcp":
        if not args or args[0] == "list":
            render_mcp(orchestrator.mcp_snapshot())
            return
        subcommand = args[0]
        subargs = args[1:]
        if subcommand == "add":
            if len(subargs) < 3:
                raise ValueError("Usage: mcp add <name> <transport> <target>")
            orchestrator.add_mcp_connector(name=subargs[0], transport=subargs[1], target=subargs[2])
            render_mcp(orchestrator.mcp_snapshot())
            return
        if subcommand == "remove":
            if not subargs:
                raise ValueError("Usage: mcp remove <name>")
            orchestrator.remove_mcp_connector(subargs[0])
            render_mcp(orchestrator.mcp_snapshot())
            return
        raise ValueError(f"Unknown mcp command '{subcommand}'.")
    if command == "lab":
        if not args:
            raise ValueError("Usage: lab <start|list|results|apply> ...")
        subcommand = args[0]
        subargs = args[1:]
        if subcommand == "list":
            render_lab_experiments(orchestrator.lab_experiments())
            return
        if subcommand == "results":
            if not subargs:
                raise ValueError("Usage: lab results <experiment_id>")
            render_lab_results(orchestrator.lab_results(subargs[0]))
            return
        if subcommand == "apply":
            if not subargs:
                raise ValueError("Usage: lab apply <variant_id>")
            render_lab_apply(
                orchestrator.apply_lab_variant(
                    subargs[0],
                    auto_approve=auto_approve,
                    confirm_callback=confirm_action,
                    preview_callback=render_lab_apply,
                )
            )
            return
        if subcommand == "start":
            if not subargs:
                raise ValueError("Usage: lab start <goal>")
            goal = " ".join(subargs)
            render_lab_results(
                orchestrator.start_lab_experiment(
                    ensure_task(goal),
                    auto_approve=auto_approve,
                )
            )
            return
        raise ValueError(f"Unknown lab command '{subcommand}'.")
    if command == "open":
        file = " ".join(args)
        orchestrator.open_file(file)
        console.print(f"Opened: {file}")
        return
    if command == "jump":
        symbol = " ".join(args)
        result = orchestrator.jump_to_symbol(symbol)
        if result.get("found") is False:
            console.print(str(result.get("message", f"Symbol '{symbol}' not found.")))
        else:
            console.print(f"Jumped to: {symbol}")
        return
    if command == "dashboard":
        console.print(orchestrator.dashboard())
        return
    if command == "tools":
        render_tools(orchestrator.available_tools())
        return
    if command == "plugins":
        render_plugins(orchestrator.available_plugins())
        return
    if command == "memory":
        render_memory_snapshot(orchestrator.memory_snapshot())
        return
    if command == "solutions":
        query = " ".join(args) if args else None
        render_solutions(orchestrator.solutions(query=query, limit=20))
        return
    if command == "learn":
        render_memory_snapshot(orchestrator.learn_project(force=True))
        return
    if command == "graph":
        render_knowledge_graph(orchestrator.knowledge_graph_snapshot())
        return
    if command == "test":
        render_test_result(orchestrator.run_tests())
        return
    if command == "models":
        render_model_catalog(orchestrator.model_catalog())
        return
    if command == "improve":
        selected_roles = [item.strip() for item in " ".join(args).split(",") if item.strip()] or None
        render_evolution_report(orchestrator.improve(roles=selected_roles, write_files=True))
        return
    if command == "evolve":
        plugin_request = " ".join(args) if args else None
        render_evolution_report(
            orchestrator.evolve(
                plugin_request=plugin_request,
                auto_confirm=auto_approve,
                confirm_callback=confirm_action,
            )
        )
        return
    if command == "voice":
        transcript = " ".join(args) if args else None
        result = orchestrator.voice_command(transcript=transcript)
        render_voice_result(result)
        return
    if command == "swarm":
        task = " ".join(args) if args else None
        if task is None:
            render_swarm_tasks(orchestrator.swarm_status())
        else:
            run = orchestrator.start_swarm(ensure_task(task), auto_approve=auto_approve)
            console.print(f"Started swarm run {run['run_id']}.")
        return
    if command == "agents":
        render_swarm_agents(orchestrator.available_agents())
        return
    if command == "tasks":
        render_swarm_tasks(orchestrator.swarm_status())
        return
    if command == "build":
        deep = False
        filtered_args: list[str] = []
        for arg in args:
            if arg == "--deep":
                deep = True
                continue
            filtered_args.append(arg)
        task = " ".join(filtered_args)
        result = orchestrator.build(ensure_task(task), auto_approve=auto_approve, deep=deep)
        render_build_result(result)
        return
    if command == "task-status":
        render_task_status(orchestrator.task_status())
        return
    if command == "stop":
        task = orchestrator.stop_task()
        if task is None:
            console.print("No running task found.")
        else:
            console.print(f"Stop requested for task #{task['id']}.")
        return
    if command == "plan":
        task = " ".join(args)
        result = orchestrator.plan(ensure_task(task))
        render_plan(result.text)
        return
    if command == "code":
        deep = False
        filtered_args = []
        for arg in args:
            if arg == "--deep":
                deep = True
                continue
            filtered_args.append(arg)
        task = " ".join(filtered_args)
        result = orchestrator.code(
            ensure_task(task),
            auto_approve=auto_approve,
            confirm_overwrite=None if auto_approve else confirm_overwrite,
            deep=deep,
        )
        render_code_result(result)
        return
    if command == "audit":
        task = " ".join(args) or "Audit the active project for defects and risks."
        result = orchestrator.audit(task)
        render_audit_result(result)
        return

    raise ValueError(f"Unknown command '{command}'. Type `help` for available commands.")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
