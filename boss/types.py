from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ToolHandler = Callable[[dict[str, Any]], Any]
WriteConfirmationHandler = Callable[[Path, str, bool], bool]


@dataclass
class ToolExecutionRecord:
    name: str
    arguments: dict[str, Any]
    success: bool
    result: Any | None = None
    error: str | None = None
    started_at: str = field(default_factory=utc_now_iso)


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def as_openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def as_anthropic_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionRecord:
        try:
            result = self.handler(arguments)
            return ToolExecutionRecord(
                name=self.name,
                arguments=arguments,
                success=True,
                result=result,
            )
        except Exception as exc:  # pragma: no cover - defensive branch
            return ToolExecutionRecord(
                name=self.name,
                arguments=arguments,
                success=False,
                error=str(exc),
            )


@dataclass
class MemoryEntry:
    category: str
    content: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeSummary:
    file_path: str
    language: str
    summary: str
    updated_at: str


@dataclass
class ScannedFile:
    relative_path: str
    absolute_path: Path
    language: str
    size: int
    modified_at: float
    content_hash: str
    is_important: bool = False
    is_entry_point: bool = False


@dataclass
class CodebaseScanResult:
    project_name: str
    root: Path
    files: list[ScannedFile]
    languages: dict[str, int]
    important_files: list[str]
    entry_points: list[str]
    main_modules: list[str]
    dependencies: list[str]


@dataclass
class FileSummary:
    file_path: str
    language: str
    purpose: str
    summary: str
    symbols: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)


@dataclass
class IndexedFile:
    file_path: str
    language: str
    content_hash: str
    size: int
    modified_at: str
    summary: str
    purpose: str
    symbols: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class ProjectMap:
    name: str
    overview: str
    languages: dict[str, int]
    main_modules: list[str]
    entry_points: list[str]
    key_files: list[str]
    dependencies: list[str]
    indexed_at: str = ""


@dataclass
class KnowledgeNode:
    node_id: int
    node_type: str
    name: str
    project_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class KnowledgeEdge:
    edge_id: int
    source_node_id: int
    target_node_id: int
    relationship: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class ProjectMemoryProfile:
    project_name: str
    description: str
    primary_language: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    architecture: str = ""
    key_modules: list[str] = field(default_factory=list)
    coding_patterns: list[str] = field(default_factory=list)
    related_projects: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class ProjectBrain:
    project_name: str
    mission: str = ""
    current_focus: str = ""
    architecture: list[str] = field(default_factory=list)
    brain_rules: list[str] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    recent_progress: list[str] = field(default_factory=list)
    open_problems: list[str] = field(default_factory=list)
    next_priorities: list[str] = field(default_factory=list)
    known_risks: list[str] = field(default_factory=list)
    recent_artifacts: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class WorkspaceRoot:
    name: str
    path: str
    mode: str = "search"
    include_root: bool = False
    discover_children: bool = False
    max_depth: int = 1
    enabled: bool = True


@dataclass
class ProjectReference:
    key: str
    name: str
    root: str
    source_root: str
    relative_path: str = "."
    display_name: str = ""
    mode: str = "registered"


@dataclass
class SolutionEntry:
    solution_id: int
    title: str
    description: str
    code_snippet: str
    tags: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    source_task: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    score: float | None = None


@dataclass
class StyleProfile:
    project_name: str
    indentation: str
    naming_conventions: list[str] = field(default_factory=list)
    code_structure: str = ""
    test_style: str = ""
    error_handling_style: str = ""
    notes: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class ProjectIndexResult:
    project_name: str
    total_files: int
    indexed_files: int
    changed_files: int
    removed_files: int
    skipped_files: int
    project_map: ProjectMap


@dataclass
class ProjectContext:
    name: str
    root: Path
    summary: str
    file_count: int
    languages: dict[str, int]
    important_files: list[str]
    architecture_notes: list[str]
    memory_entries: list[MemoryEntry]
    code_summaries: list[CodeSummary]
    project_map: ProjectMap | None = None
    relevant_files: list[IndexedFile] = field(default_factory=list)
    semantic_results: list[dict[str, Any]] = field(default_factory=list)
    relevant_memories: list[dict[str, Any]] = field(default_factory=list)
    active_file: str | None = None
    recent_files: list[str] = field(default_factory=list)
    recent_changes: list[dict[str, Any]] = field(default_factory=list)
    recent_searches: list[dict[str, Any]] = field(default_factory=list)
    workspace_state: "WorkspaceState | None" = None
    project_profile: ProjectMemoryProfile | None = None
    project_brain: ProjectBrain | None = None
    style_profile: StyleProfile | None = None
    relevant_solutions: list[SolutionEntry] = field(default_factory=list)
    similar_tasks: list[dict[str, Any]] = field(default_factory=list)
    knowledge_nodes: list[KnowledgeNode] = field(default_factory=list)
    knowledge_edges: list[KnowledgeEdge] = field(default_factory=list)
    graph_insights: list[str] = field(default_factory=list)
    related_projects: list[ProjectMemoryProfile] = field(default_factory=list)


@dataclass
class WorkspaceState:
    active_project: str
    open_files: list[str] = field(default_factory=list)
    recent_edits: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    recent_terminal_commands: list[dict[str, Any]] = field(default_factory=list)
    last_terminal_command: str = ""
    last_terminal_result: dict[str, Any] = field(default_factory=dict)
    last_test_results: dict[str, Any] = field(default_factory=dict)
    last_git_diff: str = ""
    last_git_status: dict[str, Any] = field(default_factory=dict)
    last_commit: dict[str, Any] = field(default_factory=dict)
    last_editor_event: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class ResearchSource:
    source_type: str
    title: str
    citation: str
    url: str = ""
    file_path: str = ""
    snippet: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchReport:
    query: str
    summary: str
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    sources: list[ResearchSource] = field(default_factory=list)
    local_results: list[dict[str, Any]] = field(default_factory=list)
    project_name: str | None = None
    project_scope: str = "workspace"
    mode: str = "research"
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionPolicy:
    full_access_mode: bool = False
    workspace_write_mode: str = "confirm"
    project_write_mode: str = "confirm"
    destructive_mode: str = "confirm"
    allow_web_research: bool = True
    allow_mcp: bool = True
    allow_workspace_write: bool = True
    writable_roots: list[str] = field(default_factory=list)
    trusted_project_roots: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class MCPConnector:
    name: str
    transport: str
    target: str
    capabilities: list[str] = field(default_factory=list)
    enabled: bool = True
    args: list[str] = field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioProject:
    project_key: str
    display_name: str
    root: str
    source_root: str
    mission: str = ""
    focus: str = ""
    next_priority: str = ""
    updated_at: str = ""


@dataclass
class ModelRunResult:
    text: str
    provider: str
    model: str
    duration_seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)
    raw: Any | None = None


@dataclass
class AgentResult:
    agent_name: str
    provider: str
    model: str
    text: str
    duration_seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)


@dataclass
class AuditIssue:
    severity: str
    location: str
    description: str


@dataclass
class AuditResult:
    agent_name: str
    provider: str
    model: str
    text: str
    passed: bool
    duration_seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    issues: list[AuditIssue] = field(default_factory=list)
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)


@dataclass
class WorkflowResult:
    plan: AgentResult
    implementation: AgentResult
    audit: AuditResult
    iterations: int
    changed_files: list[str] = field(default_factory=list)


@dataclass
class StructuredPlan:
    goal: str
    steps: list[str]
    contracts: list["PlanStepContract"] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class PlanStepContract:
    title: str
    step_id: str = ""
    objective: str = ""
    agent_role: str = "engineer"
    dependencies: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class StepExecutionResult:
    step_index: int
    step_title: str
    status: str
    iterations: int
    runtime_seconds: float = 0.0
    changed_files: list[str] = field(default_factory=list)
    commit_message: str | None = None
    test_result: dict[str, Any] = field(default_factory=dict)
    audit_result: str = ""
    engineer_result: str = ""
    errors: list[str] = field(default_factory=list)
    model_usage: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    tool_errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutonomousBuildResult:
    task_id: int
    project_name: str
    goal: str
    status: str
    plan: StructuredPlan
    runtime_seconds: float = 0.0
    step_results: list[StepExecutionResult] = field(default_factory=list)
    final_result: str = ""
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model_usage: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SwarmTask:
    task_id: int
    run_id: str
    agent_type: str
    title: str
    priority: int
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    retries: int = 0
    max_retries: int = 1
    depends_on: list[int] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class AgentWorkerStatus:
    agent_name: str
    role: str
    status: str = "idle"
    current_task: str = ""
    current_run_id: str | None = None
    progress: float = 0.0
    last_message: str = ""
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class AgentWorkerResult:
    task_id: int
    run_id: str
    agent_type: str
    status: str
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    completed_at: str = field(default_factory=utc_now_iso)


@dataclass
class SwarmRun:
    run_id: str
    project_name: str
    goal: str
    status: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    plan_text: str = ""
    plan_steps: list[str] = field(default_factory=list)
    task_ids: list[int] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class TaskContract:
    name: str
    description: str
    mode: str = "code"
    project_name: str | None = None
    sandbox_mode: str | None = None
    keep_sandbox: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    expected_file_contains: dict[str, list[str]] = field(default_factory=dict)
    expected_imports: list[str] = field(default_factory=list)
    expected_symbols: list[str] = field(default_factory=list)
    required_changed_files: list[str] = field(default_factory=list)
    forbidden_changed_files: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    metric_targets: dict[str, dict[str, float]] = field(default_factory=dict)
    expected_output_contains: list[str] = field(default_factory=list)
    forbidden_output_contains: list[str] = field(default_factory=list)
    expected_status: str | None = None
    require_tests_passed: bool = False
    auto_approve: bool = True
    max_iterations: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskSuite:
    name: str
    path: str
    project_name: str | None = None
    default_mode: str = "code"
    sandbox_mode: str | None = None
    keep_sandbox: bool = False
    auto_approve: bool = True
    max_iterations: int = 2
    stop_on_failure: bool = False
    tasks: list[TaskContract] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationOutcome:
    name: str
    passed: bool
    message: str


@dataclass
class EvalTaskResult:
    task_name: str
    description: str
    project_name: str
    mode: str
    status: str
    runtime_seconds: float
    files_changed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failure_category: str | None = None
    output_summary: str = ""
    validations: list[ValidationOutcome] = field(default_factory=list)
    model_usage: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str = field(default_factory=utc_now_iso)


@dataclass
class EvalRunResult:
    run_id: int
    suite_name: str
    suite_path: str
    project_name: str
    status: str
    total_tasks: int
    passed_tasks: int
    failed_tasks: int
    runtime_seconds: float
    total_estimated_cost_usd: float | None = None
    tasks: list[EvalTaskResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str = field(default_factory=utc_now_iso)
