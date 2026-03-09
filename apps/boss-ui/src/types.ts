export interface ProjectCatalogItem {
  key: string;
  display_name: string;
  root: string;
  source_root: string;
}

export interface WorkspaceRootItem {
  name: string;
  path: string;
  mode: string;
  enabled: boolean;
}

export interface ProjectsResponse {
  active_project: string | null;
  project_catalog: ProjectCatalogItem[];
}

export interface ProjectBrain {
  project_name: string;
  mission: string;
  current_focus: string;
  architecture: string[];
  milestones: string[];
  recent_progress: string[];
  open_problems: string[];
  next_priorities: string[];
  known_risks: string[];
  recent_artifacts: Array<Record<string, unknown>>;
  updated_at: string;
}

export interface BrainSnapshot {
  project_name: string;
  brain: ProjectBrain;
  policy: Record<string, unknown>;
  pending_proposals: number;
  artifact_count: number;
}

export interface RecommendationItem {
  title: string;
  reason?: string;
  source?: string;
  score?: number;
}

export interface NextSnapshot {
  recommendations: RecommendationItem[];
}

export interface RiskItem {
  title: string;
  reason?: string;
  source?: string;
  severity?: string;
}

export interface RisksSnapshot {
  risks: RiskItem[];
}

export interface WorkspaceEdit {
  file?: string;
  path?: string;
  summary?: string;
  type?: string;
}

export interface WorkspaceEventRecord {
  type?: string;
  file?: string;
  path?: string;
  command?: string;
  change_type?: string;
  summary?: string;
  timestamp?: string;
  exit_code?: number | null;
}

export interface TerminalCommandRecord {
  command?: string;
  workdir?: string;
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
  timestamp?: string;
}

export interface WorkspaceSnapshot {
  active_project: string;
  scope?: string;
  workspace_root?: string;
  search_roots?: string[];
  open_files: string[];
  recent_edits: WorkspaceEdit[];
  recent_events?: WorkspaceEventRecord[];
  recent_terminal_commands: TerminalCommandRecord[];
  last_terminal_command: string;
  last_terminal_result: TerminalCommandRecord;
  last_test_results: Record<string, unknown>;
  last_git_diff: string;
  last_git_status: Record<string, unknown>;
  last_commit: Record<string, unknown>;
  last_editor_event: Record<string, unknown>;
  updated_at: string;
}

export interface ActivityItem {
  agent: string;
  status?: string;
  message?: string;
  project_name?: string;
  task_id?: number;
  metadata?: Record<string, unknown>;
  updated_at?: string;
}

export interface ActivitySnapshot {
  activities: ActivityItem[];
}

export interface TimelineEvent {
  sequence?: number;
  title?: string;
  status?: string;
  agent?: string;
  project_name?: string;
  task_id?: number;
  message?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface TimelineSnapshot {
  events: TimelineEvent[];
}

export interface HealthSnapshot {
  status: string;
  status_reasons?: string[];
  autonomous_success_rate?: number;
  recent_eval_failures?: number;
  artifact_store_size?: number;
  workspace_watchers?: string;
}

export interface MetricSnapshot {
  task_runs_recorded?: number;
  eval_runs_recorded?: number;
  artifacts_stored?: number;
  benchmarks_executed?: number;
  experiments_executed?: number;
  agent_runtime?: Array<Record<string, unknown>>;
  run_graph?: Record<string, unknown>;
  token_usage?: Record<string, number>;
}

export interface PermissionSnapshot {
  full_access_mode: boolean;
  workspace_write_mode: string;
  project_write_mode: string;
  destructive_mode: string;
  allow_web_research: boolean;
  allow_mcp: boolean;
  allow_workspace_write: boolean;
  writable_roots: string[];
  trusted_project_roots: string[];
}

export interface RunSummary {
  kind?: string;
  identifier?: string | number;
  status?: string;
  project_name?: string;
  title?: string;
  timestamp?: string;
  artifact_path?: string;
  symbol?: string;
}

export interface RunsSnapshot {
  runs: RunSummary[];
}

export interface InternalRunSummary {
  goal?: string;
  status?: string;
  planned_steps?: number;
  completed_steps?: number;
  total_steps?: number;
  failed_steps?: number;
  retries?: number;
  changed_files?: string[];
  changed_files_count?: number;
  shipping_status?: string;
  details_available?: boolean;
  roles?: string[];
}

export interface ChatResultPayload {
  status?: string;
  task_id?: number;
  final_result?: string;
  changed_files?: string[];
  run_kind?: string;
  artifact_path?: string;
  shipping?: Record<string, unknown>;
  internal_summary?: InternalRunSummary;
}

export interface BuildTaskStep {
  task_id?: number;
  step_index?: number;
  title?: string;
  status?: string;
  engineer_output?: string;
  test_output?: Record<string, unknown>;
  audit_output?: string;
  files_changed?: string[];
  errors?: string[];
  commit_message?: string;
  iterations?: number;
  runtime_seconds?: number;
  metadata?: Record<string, unknown>;
}

export interface BuildTaskRecord {
  id?: number;
  project_name?: string;
  task?: string;
  plan?: {
    goal?: string;
    steps?: string[];
  };
  status?: string;
  total_steps?: number;
  files_changed?: string[];
  errors?: string[];
  final_result?: string;
  runtime_seconds?: number;
  metadata?: Record<string, unknown>;
  steps?: BuildTaskStep[];
}

export interface RunDetailsResponse {
  kind?: string;
  identifier?: string | number;
  project_name?: string;
  status?: string;
  summary?: Record<string, unknown>;
  task?: BuildTaskRecord;
  analysis?: Record<string, unknown>;
  artifact_path?: string;
}

export interface RootSnapshot {
  primary_root: string;
  search_roots: string[];
  roots: WorkspaceRootItem[];
  projects: ProjectCatalogItem[];
}

export interface ChatHistoryTurn {
  id: string | number;
  message: string;
  response: string;
  intent?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
}

export interface ChatHistorySnapshot {
  history: ChatHistoryTurn[];
}

export interface StreamingTurn {
  id: string;
  message: string;
  response: string;
  intent: string;
  metadata: {
    mode: string;
    actions: unknown[];
    result?: ChatResultPayload | null;
    stream_id?: string;
  };
  pending: boolean;
  cancelling: boolean;
}

export interface ChatRequest {
  message: string;
  execute: boolean;
  auto_approve: boolean;
  project_name?: string;
}

export interface StreamDoneResponse {
  id?: string | number;
  reply?: string;
  intent?: string;
  mode?: string;
  actions?: unknown[];
  result?: ChatResultPayload | null;
}

export type StreamEvent =
  | {
      type: "meta";
      stream_id?: string;
      intent?: string;
      mode?: string;
      actions?: unknown[];
    }
  | {
      type: "delta";
      delta?: string;
    }
  | {
      type: "interrupted";
    }
  | {
      type: "error";
      error?: string;
    }
  | {
      type: "done";
      response?: StreamDoneResponse;
    };

export interface CommandCenterSnapshot {
  projects: ProjectsResponse;
  brain: BrainSnapshot;
  next: NextSnapshot;
  risks: RisksSnapshot;
  workspace: WorkspaceSnapshot;
  activity: ActivitySnapshot;
  timeline: TimelineSnapshot;
  health: HealthSnapshot;
  metrics: MetricSnapshot;
  permissions: PermissionSnapshot;
  runs: RunsSnapshot;
  history: ChatHistorySnapshot;
  roots: RootSnapshot;
}
