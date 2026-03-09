import { invoke } from "@tauri-apps/api/core";
import { startTransition, useEffect, useRef, useState } from "react";

import { fetchRunDetails, loadCommandCenter, postJson, setActiveProject, streamChat } from "./api";
import type {
  ActivityItem,
  BrainSnapshot,
  BuildTaskStep,
  ChatHistoryTurn,
  ChatResultPayload,
  CommandCenterSnapshot,
  HealthSnapshot,
  InternalRunSummary,
  MetricSnapshot,
  PermissionSnapshot,
  ProjectCatalogItem,
  RootSnapshot,
  RunDetailsResponse,
  RunSummary,
  StreamEvent,
  StreamingTurn,
  TerminalCommandRecord,
  TimelineEvent,
  WorkspaceSnapshot,
} from "./types";

type BackendStatus = "starting" | "ready" | "failed";
type ChatTurn = ChatHistoryTurn | StreamingTurn;
type Tone = "good" | "warn" | "bad" | "neutral";

interface SummaryListItem {
  title: string;
  detail: string;
  meta?: string;
  tone?: Tone;
}

interface ActionNotice {
  id: string;
  title: string;
  detail: string;
  tone: Tone;
  primaryLabel?: string;
  primaryDisabled?: boolean;
  primaryAction?: () => void | Promise<void>;
  secondaryLabel?: string;
  secondaryDisabled?: boolean;
  secondaryAction?: () => void | Promise<void>;
}

interface LiveFeedItem {
  id: string;
  title: string;
  detail: string;
  meta?: string;
  tone: Tone;
}

interface ProjectRailItem {
  key: string | null;
  label: string;
  meta: string;
}

const QUICK_PROMPTS = [
  "Fix the highest-priority issue in the active project.",
  "Audit the active project and tell me what matters.",
  "Summarize what changed and tell me the next move.",
];

const IDLE_REFRESH_INTERVAL_MS = 5000;
const LIVE_REFRESH_INTERVAL_MS = 1200;
const INTERNAL_AGENTS = new Set(["architect", "engineer", "test", "auditor"]);

const EMPTY_WORKSPACE: WorkspaceSnapshot = {
  active_project: "__workspace__",
  open_files: [],
  recent_edits: [],
  recent_events: [],
  recent_terminal_commands: [],
  last_terminal_command: "",
  last_terminal_result: {},
  last_test_results: {},
  last_git_diff: "",
  last_git_status: {},
  last_commit: {},
  last_editor_event: {},
  updated_at: "",
};

const EMPTY_HEALTH: HealthSnapshot = {
  status: "unknown",
  status_reasons: [],
  autonomous_success_rate: 0,
  recent_eval_failures: 0,
  artifact_store_size: 0,
  workspace_watchers: "unknown",
};

const EMPTY_METRICS: MetricSnapshot = {
  task_runs_recorded: 0,
  eval_runs_recorded: 0,
  artifacts_stored: 0,
  benchmarks_executed: 0,
  experiments_executed: 0,
  agent_runtime: [],
  run_graph: {},
  token_usage: {},
};

const EMPTY_PERMISSIONS: PermissionSnapshot = {
  full_access_mode: false,
  workspace_write_mode: "confirm",
  project_write_mode: "confirm",
  destructive_mode: "confirm",
  allow_web_research: false,
  allow_mcp: false,
  allow_workspace_write: false,
  writable_roots: [],
  trusted_project_roots: [],
};

const EMPTY_ROOTS: RootSnapshot = {
  primary_root: "",
  search_roots: [],
  roots: [],
  projects: [],
};

const EMPTY_BRAIN: BrainSnapshot = {
  project_name: "__workspace__",
  brain: {
    project_name: "__workspace__",
    mission: "",
    current_focus: "",
    architecture: [],
    milestones: [],
    recent_progress: [],
    open_problems: [],
    next_priorities: [],
    known_risks: [],
    recent_artifacts: [],
    updated_at: "",
  },
  policy: {},
  pending_proposals: 0,
  artifact_count: 0,
};

function formatTimestamp(value?: string | null): string {
  if (!value) return "Now";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatPercent(value?: number | null): string {
  if (typeof value !== "number") return "--";
  return `${Math.round(value * 100)}%`;
}

function formatCount(value?: number | null): string {
  if (typeof value !== "number") return "--";
  return new Intl.NumberFormat().format(value);
}

function formatStatus(value?: string | null): string {
  if (!value) return "Unknown";
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function projectLabel(projectName: string | null): string {
  if (!projectName || projectName === "__workspace__") {
    return "Workspace";
  }
  return projectName;
}

function trimText(value: string | undefined, limit = 220): string {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function trimBlock(value: string | undefined, limit = 960): string {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
}

function turnMode(turn: ChatTurn): string {
  if ("pending" in turn) {
    return turn.metadata.mode || "chat";
  }
  const metadata = turn.metadata ?? {};
  const mode = metadata.mode;
  return typeof mode === "string" ? mode : "chat";
}

function statusTone(status?: string | null): Tone {
  const normalized = String(status || "unknown").toLowerCase();
  if (normalized === "stable" || normalized === "healthy" || normalized === "completed") return "good";
  if (normalized === "running" || normalized === "queued" || normalized === "planning" || normalized === "reviewing") {
    return "warn";
  }
  if (normalized === "failed" || normalized === "critical" || normalized === "error") return "bad";
  return "neutral";
}

function isWorkingStatus(status?: string | null): boolean {
  const normalized = String(status || "").toLowerCase();
  return ["queued", "running", "planning", "reviewing"].includes(normalized);
}

function workspaceTestSummary(workspace: WorkspaceSnapshot): string {
  const message = workspace.last_test_results?.message;
  if (typeof message === "string" && message) {
    return message;
  }
  const failureSummary = workspace.last_test_results?.failure_summary;
  if (typeof failureSummary === "string" && failureSummary) {
    return failureSummary;
  }
  return "No test execution recorded.";
}

function latestActivitySummary(activities: ActivityItem[], timeline: TimelineEvent[]): string {
  const active = activities.find((item) => isWorkingStatus(item.status));
  if (active) {
    return activityDetail(active);
  }
  const lastEvent = timeline[0];
  if (lastEvent?.title || lastEvent?.message) {
    return timelineDetail(lastEvent);
  }
  return "Waiting for the next request.";
}

function latestWorkSummary(runs: RunSummary[], timeline: TimelineEvent[]): string {
  const running = runs.find((item) => isWorkingStatus(item.status));
  if (running) {
    return `Working on ${running.title || "the active request"}.`;
  }
  const latestRun = runs[0];
  if (latestRun) {
    return `${formatStatus(latestRun.status)}: ${latestRun.title || "Recent run"}.`;
  }
  return latestActivitySummary([], timeline);
}

function isInternalTimelineEvent(event: TimelineEvent): boolean {
  return INTERNAL_AGENTS.has(String(event.agent || "").toLowerCase());
}

function userFacingStage(agent?: string | null, status?: string | null): string {
  const normalized = String(agent || "").toLowerCase();
  const state = String(status || "").toLowerCase();
  if (normalized === "architect") {
    return state === "completed" ? "Plan ready" : "Mapping the change";
  }
  if (normalized === "engineer") {
    return state === "completed" ? "Code updated" : "Editing the project";
  }
  if (normalized === "test") {
    return state === "failed" ? "Checks failed" : state === "completed" ? "Checks complete" : "Running checks";
  }
  if (normalized === "auditor") {
    return state === "failed" ? "Review found issues" : state === "completed" ? "Review complete" : "Checking the result";
  }
  if (normalized === "research") {
    return "Gathering context";
  }
  return formatStatus(status || agent || "Working");
}

function sanitizeInternalMessage(agent?: string | null, message?: string | null): string {
  const text = String(message || "").trim();
  if (!text || /^idle$/i.test(text)) {
    return "Standing by.";
  }
  const normalized = String(agent || "").toLowerCase();
  if (normalized === "architect") {
    if (text.startsWith("Generating plan for:") || text.startsWith("Creating execution plan for:")) {
      return "Working out the safest way to make the change.";
    }
    if (/planning skipped/i.test(text)) {
      return "Skipping planning and moving straight into the change.";
    }
    return trimText(text);
  }
  if (normalized === "engineer") {
    if (/implementing code changes/i.test(text) || /applying audit feedback/i.test(text)) {
      return "Editing files and wiring the change through the project.";
    }
    return trimText(text);
  }
  if (normalized === "test") {
    if (/running project tests/i.test(text)) {
      return "Running validation against the current project.";
    }
    return trimText(text);
  }
  if (normalized === "auditor") {
    if (/reviewing/i.test(text)) {
      return "Checking the result for regressions and missed edges.";
    }
    if (/audit requested fixes/i.test(text)) {
      return "Review found issues that still need fixing.";
    }
    return trimText(text);
  }
  return trimText(text);
}

function activityDetail(activity: ActivityItem): string {
  return sanitizeInternalMessage(activity.agent, activity.message);
}

function timelineTitle(event: TimelineEvent): string {
  const agent = String(event.agent || "").toLowerCase();
  const title = String(event.title || "").trim();
  if (agent && INTERNAL_AGENTS.has(agent)) {
    if (/planning/i.test(title)) {
      return "Plan";
    }
    if (/implementation|retry/i.test(title)) {
      return "Code";
    }
    if (/audit|review/i.test(title)) {
      return "Review";
    }
    if (/test/i.test(title)) {
      return "Checks";
    }
    return userFacingStage(agent, event.status);
  }
  return trimText(title || "Recent update", 80);
}

function timelineDetail(event: TimelineEvent): string {
  const agent = String(event.agent || "").toLowerCase();
  const message = String(event.message || "").trim();
  if (agent && INTERNAL_AGENTS.has(agent)) {
    if (message) {
      return sanitizeInternalMessage(agent, message);
    }
    return userFacingStage(agent, event.status);
  }
  return trimText(message || titleFallback(event.title), 180);
}

function titleFallback(value?: string | null): string {
  return trimText(String(value || "Recent update"), 80);
}

function buildLiveFeed(timeline: TimelineEvent[]): LiveFeedItem[] {
  return timeline
    .filter((event) => String(event.status || "").toLowerCase() !== "idle")
    .slice(0, 6)
    .map((event) => ({
      id: `${event.sequence || event.timestamp || event.title || "event"}`,
      title: timelineTitle(event),
      detail: timelineDetail(event),
      meta: formatTimestamp(event.timestamp),
      tone: statusTone(event.status),
    }));
}

function currentLiveItem(
  activities: ActivityItem[],
  timeline: TimelineEvent[],
  runs: RunSummary[],
  streamingTurn: StreamingTurn | null,
): LiveFeedItem {
  const active = activities.find((item) => isWorkingStatus(item.status));
  if (active) {
    return {
      id: `activity-${active.agent}`,
      title: userFacingStage(active.agent, active.status),
      detail: activityDetail(active),
      meta: active.project_name || "workspace",
      tone: statusTone(active.status),
    };
  }
  const runningRun = runs.find((item) => isWorkingStatus(item.status));
  if (runningRun) {
    return {
      id: `run-${runningRun.identifier || runningRun.title || "active"}`,
      title: "Working on your request",
      detail: runningRun.title || "Processing the current run.",
      meta: projectLabel(runningRun.project_name || null),
      tone: statusTone(runningRun.status),
    };
  }
  if (streamingTurn?.pending) {
    return {
      id: "streaming",
      title: "Writing back",
      detail: "BOSS is preparing the reply now.",
      tone: "warn",
    };
  }
  const latest = timeline[0];
  if (latest) {
    return {
      id: `timeline-${latest.sequence || latest.timestamp || "latest"}`,
      title: timelineTitle(latest),
      detail: timelineDetail(latest),
      meta: formatTimestamp(latest.timestamp),
      tone: statusTone(latest.status),
    };
  }
  return {
    id: "idle",
    title: "Ready",
    detail: "Tell BOSS what outcome you want.",
    tone: "good",
  };
}

function terminalTone(entry: TerminalCommandRecord): Tone {
  if (typeof entry.exit_code !== "number") return "neutral";
  return entry.exit_code === 0 ? "good" : "bad";
}

function formatFileSummary(files: string[] | undefined, limit = 3): string {
  const entries = (files || []).filter(Boolean);
  if (!entries.length) return "No file changes recorded.";
  if (entries.length <= limit) return entries.join(", ");
  return `${entries.slice(0, limit).join(", ")} +${entries.length - limit} more`;
}

function testOutputSummary(step: BuildTaskStep): string {
  const output = step.test_output || {};
  const passed = output.passed;
  const message = typeof output.message === "string" ? output.message : "";
  const failure = typeof output.failure_summary === "string" ? output.failure_summary : "";
  if (typeof passed === "boolean") {
    return passed ? "Checks passed." : failure || message || "Checks failed.";
  }
  return message || failure || "No dedicated check result was captured.";
}

function turnResult(turn: ChatTurn): ChatResultPayload | null {
  const metadata = turn.metadata ?? {};
  const result = metadata.result;
  if (!result || typeof result !== "object") {
    return null;
  }
  return result as ChatResultPayload;
}

function hasBuildRunDetails(result: ChatResultPayload | null): result is ChatResultPayload & {
  task_id: number;
  run_kind: string;
  internal_summary: InternalRunSummary;
} {
  return Boolean(
    result &&
      typeof result.task_id === "number" &&
      result.run_kind &&
      result.internal_summary?.details_available,
  );
}

function buildRunSummaryCopy(summary: InternalRunSummary): string {
  const totalSteps = Number(summary.total_steps || 0);
  const completedSteps = Number(summary.completed_steps || 0);
  const failedSteps = Number(summary.failed_steps || 0);
  const retries = Number(summary.retries || 0);
  const changedFilesCount = Number(summary.changed_files_count || 0);
  const parts: string[] = [];
  if (totalSteps) {
    parts.push(`Plan covered ${totalSteps} step${totalSteps === 1 ? "" : "s"}.`);
    parts.push(`Finished ${completedSteps}/${totalSteps}.`);
  } else {
    parts.push("Execution details are available for this run.");
  }
  if (failedSteps) {
    parts.push(`${failedSteps} step${failedSteps === 1 ? "" : "s"} still need follow-up.`);
  }
  if (retries) {
    parts.push(`${retries} retr${retries === 1 ? "y" : "ies"} happened during the run.`);
  }
  if (changedFilesCount) {
    parts.push(`Changed ${changedFilesCount} file${changedFilesCount === 1 ? "" : "s"}.`);
  }
  if (summary.shipping_status) {
    parts.push(`Shipping is ${summary.shipping_status.replace(/_/g, " ")}.`);
  }
  return parts.join(" ");
}

function latestTurnMatching(
  turns: ChatTurn[],
  predicate: (turn: ChatTurn) => boolean,
): ChatTurn | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (predicate(turns[index])) {
      return turns[index];
    }
  }
  return null;
}

function pendingCommitResult(turns: ChatTurn[]): (ChatResultPayload & { task_id: number }) | null {
  const match = latestTurnMatching(turns, (turn) => {
    const result = turnResult(turn);
    if (!result || typeof result.task_id !== "number") {
      return false;
    }
    const shipping = result.shipping || {};
    const status =
      typeof shipping.status === "string"
        ? shipping.status
        : result.internal_summary?.shipping_status || "";
    return String(status).toLowerCase() === "awaiting_commit";
  });
  const result = match ? turnResult(match) : null;
  return result && typeof result.task_id === "number" ? (result as ChatResultPayload & { task_id: number }) : null;
}

function projectRailItems(
  projects: ProjectCatalogItem[],
  activeProject: string | null,
  limit = 7,
): ProjectRailItem[] {
  const items: ProjectRailItem[] = [
    {
      key: null,
      label: "Workspace",
      meta: "All projects",
    },
  ];
  const sorted = [...projects].sort((left, right) =>
    (left.display_name || left.key).localeCompare(right.display_name || right.key),
  );
  const activeItem = sorted.find((project) => project.key === activeProject) ?? null;
  if (activeItem) {
    items.push({
      key: activeItem.key,
      label: activeItem.display_name || activeItem.key,
      meta: activeItem.source_root || "project",
    });
  }
  for (const project of sorted) {
    if (items.some((item) => item.key === project.key)) {
      continue;
    }
    items.push({
      key: project.key,
      label: project.display_name || project.key,
      meta: project.source_root || "project",
    });
    if (items.length >= limit + 1) {
      break;
    }
  }
  return items;
}

export function App() {
  const [backendUrl, setBackendUrl] = useState("");
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("starting");
  const [backendMessage, setBackendMessage] = useState("Starting the BOSS backend...");
  const [projects, setProjects] = useState<ProjectCatalogItem[]>([]);
  const [activeProject, setActiveProjectState] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(EMPTY_WORKSPACE);
  const [brain, setBrain] = useState<BrainSnapshot>(EMPTY_BRAIN);
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [health, setHealth] = useState<HealthSnapshot>(EMPTY_HEALTH);
  const [metrics, setMetrics] = useState<MetricSnapshot>(EMPTY_METRICS);
  const [permissions, setPermissions] = useState<PermissionSnapshot>(EMPTY_PERMISSIONS);
  const [roots, setRoots] = useState<RootSnapshot>(EMPTY_ROOTS);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [chatHistory, setChatHistory] = useState<ChatHistoryTurn[]>([]);
  const [composerValue, setComposerValue] = useState("");
  const [executeMode, setExecuteMode] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [surfaceError, setSurfaceError] = useState("");
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(null);
  const [sending, setSending] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [actionBusyId, setActionBusyId] = useState("");
  const streamAbortRef = useRef<AbortController | null>(null);
  const activeStreamIdRef = useRef<string | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);

  const hasActiveRun = runs.some((item) => isWorkingStatus(item.status));
  const hasActiveWork =
    Boolean(streamingTurn?.pending) ||
    hasActiveRun ||
    activities.some((item) => isWorkingStatus(item.status));
  const refreshInterval = hasActiveWork ? LIVE_REFRESH_INTERVAL_MS : IDLE_REFRESH_INTERVAL_MS;

  useEffect(() => {
    let active = true;

    async function start() {
      try {
        const url = await invoke<string>("start_backend");
        if (!active) return;
        setBackendUrl(url);
        setBackendStatus("ready");
        setBackendMessage("BOSS is online.");
      } catch (error) {
        if (!active) return;
        setBackendStatus("failed");
        setBackendMessage(String(error));
      }
    }

    void start();

    return () => {
      active = false;
      streamAbortRef.current?.abort();
      void invoke("stop_backend").catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    if (backendStatus !== "ready" || !backendUrl) return;
    void refreshAll(backendUrl);
  }, [backendStatus, backendUrl]);

  useEffect(() => {
    if (backendStatus !== "ready" || !backendUrl) return;
    const interval = window.setInterval(() => {
      void refreshAll(backendUrl, true);
    }, refreshInterval);
    return () => window.clearInterval(interval);
  }, [backendStatus, backendUrl, refreshInterval]);

  useEffect(() => {
    const node = chatScrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [chatHistory.length, streamingTurn?.response, streamingTurn?.pending]);

  async function refreshAll(url = backendUrl, background = false) {
    if (!url) return;
    if (!background) {
      setRefreshing(true);
    }
    try {
      const snapshot = await loadCommandCenter(url);
      applySnapshot(snapshot);
      if (!background) {
        setSurfaceError("");
      }
    } catch (error) {
      const message = String(error);
      setSurfaceError(message);
      if (backendStatus !== "failed") {
        setBackendStatus("failed");
        setBackendMessage(message);
      }
    } finally {
      if (!background) {
        setRefreshing(false);
      }
    }
  }

  function applySnapshot(snapshot: CommandCenterSnapshot) {
    startTransition(() => {
      setProjects(snapshot.projects.project_catalog ?? []);
      setActiveProjectState(snapshot.projects.active_project ?? null);
      setWorkspace(snapshot.workspace);
      setBrain(snapshot.brain);
      setActivities(snapshot.activity.activities ?? []);
      setTimeline(snapshot.timeline.events ?? []);
      setHealth(snapshot.health);
      setMetrics(snapshot.metrics);
      setPermissions(snapshot.permissions);
      setRoots(snapshot.roots);
      setRuns(snapshot.runs.runs ?? []);
      setChatHistory(snapshot.history.history ?? []);
    });
    setBackendStatus("ready");
    setBackendMessage("BOSS is online.");
  }

  async function retryLaunch() {
    setBackendStatus("starting");
    setBackendMessage("Retrying backend launch...");
    setSurfaceError("");
    try {
      await invoke("stop_backend");
    } catch (_error) {
      // Ignore stop failures during restart.
    }
    try {
      const url = await invoke<string>("start_backend");
      setBackendUrl(url);
      setBackendStatus("ready");
      setBackendMessage("BOSS is online.");
      await refreshAll(url);
    } catch (error) {
      setBackendStatus("failed");
      setBackendMessage(String(error));
    }
  }

  async function handleProjectSelect(projectName: string | null) {
    if (!backendUrl) return;
    const target = projectName ?? "__workspace__";
    setRefreshing(true);
    setSurfaceError("");
    try {
      await setActiveProject(backendUrl, target);
      await refreshAll(backendUrl);
    } catch (error) {
      setSurfaceError(String(error));
    } finally {
      setRefreshing(false);
    }
  }

  async function handleSubmit() {
    if (!backendUrl) return;
    const message = composerValue.trim();
    if (!message || sending) return;

    setComposerValue("");
    setSending(true);
    setSurfaceError("");
    const controller = new AbortController();
    streamAbortRef.current = controller;
    activeStreamIdRef.current = null;
    setStreamingTurn({
      id: "pending",
      message,
      response: "",
      intent: executeMode ? "execution" : "conversation",
      metadata: {
        mode: executeMode ? "executing" : "chat",
        actions: [],
      },
      pending: true,
      cancelling: false,
    });

    try {
      await streamChat(
        backendUrl,
        {
          message,
          execute: executeMode,
          auto_approve: executeMode,
          project_name: activeProject ?? undefined,
        },
        (event) => applyStreamEvent(event, message),
        controller.signal,
      );
      await refreshAll(backendUrl, true);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      const messageText = String(error);
      setSurfaceError(messageText);
      setStreamingTurn((current) =>
        current
          ? {
              ...current,
              response: current.response ? `${current.response}\n${messageText}` : messageText,
              pending: false,
              metadata: { ...current.metadata, mode: "blocked" },
            }
          : null,
      );
    } finally {
      streamAbortRef.current = null;
      activeStreamIdRef.current = null;
      setSending(false);
    }
  }

  function applyStreamEvent(event: StreamEvent, originalMessage: string) {
    if (event.type === "meta") {
      if (event.stream_id) {
        activeStreamIdRef.current = event.stream_id;
      }
      setStreamingTurn((current) =>
        current
          ? {
              ...current,
              intent: event.intent || current.intent,
              metadata: {
                ...current.metadata,
                mode: event.mode || current.metadata.mode,
                actions: event.actions || current.metadata.actions,
                stream_id: event.stream_id || current.metadata.stream_id,
              },
            }
          : current,
      );
      return;
    }

    if (event.type === "delta") {
      setStreamingTurn((current) =>
        current ? { ...current, response: `${current.response}${event.delta || ""}` } : current,
      );
      return;
    }

    if (event.type === "interrupted") {
      setStreamingTurn((current) =>
        current
          ? {
              ...current,
              pending: false,
              metadata: { ...current.metadata, mode: "interrupted" },
            }
          : current,
      );
      return;
    }

    if (event.type === "error") {
      setStreamingTurn((current) =>
        current
          ? {
              ...current,
              response: current.response
                ? `${current.response}\n${event.error || "Chat stream failed."}`
                : event.error || "Chat stream failed.",
              pending: false,
              metadata: { ...current.metadata, mode: "blocked" },
            }
          : current,
      );
      return;
    }

    if (event.type === "done" && event.response) {
      const turn: ChatHistoryTurn = {
        id: event.response.id || `${Date.now()}`,
        message: originalMessage,
        response: event.response.reply || "",
        intent: event.response.intent || "conversation",
        metadata: {
          mode: event.response.mode || "chat",
          actions: event.response.actions || [],
          result: event.response.result || null,
        },
        created_at: new Date().toISOString(),
      };
      setChatHistory((current) => [...current, turn]);
      setStreamingTurn(null);
    }
  }

  async function stopStreaming() {
    if (!backendUrl || !streamingTurn?.pending) return;
    setStreamingTurn((current) => (current ? { ...current, cancelling: true } : current));
    try {
      if (activeStreamIdRef.current) {
        await postJson(backendUrl, "/chat/cancel", {
          stream_id: activeStreamIdRef.current,
          project_name: activeProject ?? undefined,
        });
      }
    } catch (_error) {
      // Ignore backend cancellation failures and abort locally.
    } finally {
      streamAbortRef.current?.abort();
      setStreamingTurn((current) =>
        current
          ? {
              ...current,
              pending: false,
              cancelling: false,
              metadata: { ...current.metadata, mode: "interrupted" },
            }
          : current,
      );
    }
  }

  async function handleCommitDecision(runId: number, decision: "approve" | "reject") {
    if (!backendUrl) return;
    const busyId = `${decision}-${runId}`;
    setActionBusyId(busyId);
    setSurfaceError("");
    try {
      await postJson(
        backendUrl,
        `/runs/${encodeURIComponent(String(runId))}/${decision === "approve" ? "commit" : "commit/reject"}`,
        {
          kind: "build",
          project_name: activeProject ?? undefined,
        },
      );
      await refreshAll(backendUrl, true);
    } catch (error) {
      setSurfaceError(String(error));
    } finally {
      setActionBusyId("");
    }
  }

  function openLegacyUi() {
    if (!backendUrl) return;
    window.open(`${backendUrl}/`, "_blank", "noopener,noreferrer");
  }

  function usePrompt(prompt: string) {
    setComposerValue(prompt);
  }

  const activeProjectMeta = projects.find((project) => project.key === activeProject) ?? null;
  const chatTurns: ChatTurn[] = streamingTurn ? [...chatHistory, streamingTurn] : chatHistory;
  const blockedTurn =
    chatTurns.length && turnMode(chatTurns[chatTurns.length - 1]) === "blocked"
      ? chatTurns[chatTurns.length - 1]
      : null;
  const commitResult = pendingCommitResult(chatTurns);
  const liveStatus = currentLiveItem(activities, timeline, runs, streamingTurn);
  const railProjects = projectRailItems(projects, activeProject, 8);

  const actionNotices: ActionNotice[] = [];
  if (surfaceError) {
    actionNotices.push({
      id: "connection",
      title: backendStatus === "failed" ? "Connection lost" : "Refresh needed",
      detail: surfaceError,
      tone: "bad",
      primaryLabel: backendStatus === "failed" ? "Retry launch" : "Refresh",
      primaryAction: () => (backendStatus === "failed" ? retryLaunch() : refreshAll()),
    });
  }
  if (commitResult) {
    const shipping = commitResult.shipping || {};
    const summary = commitResult.internal_summary;
    actionNotices.push({
      id: `commit-${commitResult.task_id}`,
      title: "Commit approval needed",
      detail:
        (typeof shipping.message === "string" && shipping.message) ||
        (summary ? buildRunSummaryCopy(summary) : "Review the diff, then approve or reject the pending commit."),
      tone: "warn",
      primaryLabel: actionBusyId === `approve-${commitResult.task_id}` ? "Approving..." : "Approve commit",
      primaryDisabled: actionBusyId !== "",
      primaryAction: () => handleCommitDecision(commitResult.task_id, "approve"),
      secondaryLabel: actionBusyId === `reject-${commitResult.task_id}` ? "Rejecting..." : "Reject commit",
      secondaryDisabled: actionBusyId !== "",
      secondaryAction: () => handleCommitDecision(commitResult.task_id, "reject"),
    });
  }
  if (blockedTurn) {
    actionNotices.push({
      id: `blocked-${blockedTurn.id}`,
      title: "Ready to run when you are",
      detail: trimText(blockedTurn.response || "Turn on changes and send it again, or ask BOSS to plan it first."),
      tone: "warn",
      primaryLabel: "Enable changes",
      primaryAction: () => {
        setExecuteMode(true);
        setComposerValue(blockedTurn.message);
      },
    });
  }
  if (brain.pending_proposals > 0) {
    actionNotices.push({
      id: "brain-proposals",
      title: "A few project notes need review",
      detail: `${brain.pending_proposals} saved note${brain.pending_proposals === 1 ? "" : "s"} are waiting in the details panel.`,
      tone: "warn",
      primaryLabel: "Open details",
      primaryAction: () => setShowDetails(true),
    });
  }

  if (backendStatus !== "ready") {
    return (
      <main className="startup-shell">
        <div className="hud-grid" />
        <section className="startup-card hud-panel">
          <div className="startup-copy-block">
            <p className="section-eyebrow">BOSS</p>
            <h1>Booting the console</h1>
            <p className="supporting-copy">
              BOSS is bringing the local runtime online. Once it is ready, the screen stays focused
              on the thread, the live workstream, and the terminal trail.
            </p>
            <StatusPill tone={backendStatus === "failed" ? "bad" : "warn"}>{backendMessage}</StatusPill>
            <div className="startup-actions">
              <button type="button" className="primary-action" onClick={retryLaunch}>
                Retry launch
              </button>
            </div>
          </div>
          <div className="signal-core signal-core-large">
            <div className="signal-ring signal-ring-one" />
            <div className="signal-ring signal-ring-two" />
            <div className="signal-ring signal-ring-three" />
            <div className="signal-center">
              <span>{backendStatus === "failed" ? "OFFLINE" : "BOOT"}</span>
            </div>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="workspace-shell">
      <div className="hud-grid" />
      <div className="hud-noise" />

      <div className="workspace-layout">
        <aside className="project-rail hud-panel">
          <div className="project-rail-head">
            <p className="section-eyebrow">BOSS</p>
            <h1>Projects</h1>
            <p className="supporting-copy">Start in a project on the left. Everything else stays in the chat.</p>
          </div>

          <div className="project-rail-status">
            <StatusPill tone="good">Online</StatusPill>
            <StatusPill tone={hasActiveWork ? "warn" : "neutral"}>{liveStatus.title}</StatusPill>
          </div>

          <div className="project-rail-list">
            {railProjects.map((project) => {
              const selected =
                (project.key === null && activeProject === null) ||
                (project.key !== null && project.key === activeProject);
              return (
                <button
                  key={project.key ?? "__workspace__"}
                  type="button"
                  className={`project-rail-item${selected ? " active" : ""}`}
                  onClick={() => void handleProjectSelect(project.key)}
                >
                  <strong>{project.label}</strong>
                  <span>{project.meta}</span>
                </button>
              );
            })}
          </div>

          <label className="project-picker project-picker-compact">
            <span>All projects</span>
            <select
              value={activeProject ?? "__workspace__"}
              onChange={(event) =>
                void handleProjectSelect(
                  event.target.value === "__workspace__" ? null : event.target.value,
                )
              }
            >
              <option value="__workspace__">Workspace</option>
              {projects.map((project) => (
                <option key={project.key} value={project.key}>
                  {project.display_name || project.key}
                </option>
              ))}
            </select>
          </label>
        </aside>

        <section className="chat-shell hud-panel">
          <div className="chat-shell-head">
            <div>
              <p className="section-eyebrow">Thread</p>
              <h2>Work with BOSS</h2>
              <p className="chat-shell-subtitle">
                {activeProjectMeta?.display_name || projectLabel(activeProject)} ·{" "}
                {hasActiveWork ? liveStatus.detail : latestWorkSummary(runs, timeline)}
              </p>
            </div>

            <div className="chat-shell-actions">
              <button type="button" className="ghost-action" onClick={() => void refreshAll()}>
                {refreshing ? "Refreshing..." : "Refresh"}
              </button>
            </div>
          </div>

          {actionNotices.length ? (
            <section className="notice-stack notice-stack-inline">
              {actionNotices.map((notice) => (
                <ActionNoticeCard key={notice.id} notice={notice} />
              ))}
            </section>
          ) : null}

          <div ref={chatScrollRef} className="chat-scroll">
            <div className="chat-feed">
              {chatTurns.length ? (
                chatTurns.map((turn) => (
                  <ChatTurnCard
                    key={`${turn.id}-${turnMode(turn)}`}
                    turn={turn}
                    backendUrl={backendUrl}
                  />
                ))
              ) : (
                <EmptyState message="Ask BOSS to build, fix, review, or summarize something." />
              )}
            </div>
          </div>

          <section className="composer-dock composer-dock-fixed">
            <div className="prompt-row prompt-row-compact">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="prompt-chip"
                  onClick={() => usePrompt(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>

            <label className="composer-field">
              <span className="field-label">Message</span>
              <textarea
                value={composerValue}
                onChange={(event) => setComposerValue(event.target.value)}
                rows={4}
                placeholder="Fix the checkout bug, audit the active project, summarize what changed, ..."
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                    event.preventDefault();
                    void handleSubmit();
                  }
                }}
              />
            </label>

            <div className="composer-footer">
              <div className="mode-switch" role="tablist" aria-label="BOSS mode">
                <button
                  type="button"
                  className={!executeMode ? "active" : ""}
                  onClick={() => setExecuteMode(false)}
                >
                  Chat
                </button>
                <button
                  type="button"
                  className={executeMode ? "active" : ""}
                  onClick={() => setExecuteMode(true)}
                >
                  Build
                </button>
              </div>

              <p className="helper-copy">
                {chatTurns.length ? `${chatTurns.length} turns` : "Ready"} · Cmd/Ctrl+Enter to send
              </p>

              <div className="action-group">
                {streamingTurn?.pending ? (
                  <button type="button" className="ghost-action" onClick={() => void stopStreaming()}>
                    {streamingTurn.cancelling ? "Stopping..." : "Stop"}
                  </button>
                ) : null}

                <button
                  type="button"
                  className="primary-action"
                  disabled={sending || !composerValue.trim()}
                  onClick={() => void handleSubmit()}
                >
                  {sending ? "Working..." : executeMode ? "Run request" : "Ask BOSS"}
                </button>
              </div>
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}

function SectionHeader(props: { eyebrow: string; title: string; detail?: string }) {
  return (
    <div className="section-header">
      <div>
        <p className="section-eyebrow">{props.eyebrow}</p>
        <h4>{props.title}</h4>
      </div>
      {props.detail ? <span className="section-detail">{props.detail}</span> : null}
    </div>
  );
}

function StatusPill(props: { tone: Tone; children: string }) {
  return <span className={`status-pill tone-${props.tone}`}>{props.children}</span>;
}

function LiveSurface(props: {
  liveStatus: LiveFeedItem;
  liveFeedItems: LiveFeedItem[];
  terminalEntries: TerminalCommandRecord[];
  hasActiveWork: boolean;
}) {
  return (
    <section className="live-surface">
      <article className="live-panel">
        <SectionHeader
          eyebrow="Live"
          title={props.liveStatus.title}
          detail={props.hasActiveWork ? "Updating automatically" : "Recent work"}
        />
        <p className="live-status-copy">{props.liveStatus.detail}</p>

        <div className="live-feed-list">
          {props.liveFeedItems.length ? (
            props.liveFeedItems.map((item) => (
              <article key={item.id} className={`live-feed-item tone-${item.tone}`}>
                <div className="live-feed-head">
                  <strong>{item.title}</strong>
                  {item.meta ? <span>{item.meta}</span> : null}
                </div>
                <p>{item.detail}</p>
              </article>
            ))
          ) : (
            <EmptyState message="Live progress updates will appear here while BOSS is working." />
          )}
        </div>
      </article>

      <article className="terminal-panel">
        <SectionHeader
          eyebrow="Terminal"
          title={props.terminalEntries.length ? "Recent command trail" : "Waiting for command output"}
          detail={props.terminalEntries.length ? `${props.terminalEntries.length} command${props.terminalEntries.length === 1 ? "" : "s"}` : undefined}
        />
        <div className="terminal-list">
          {props.terminalEntries.length ? (
            props.terminalEntries.map((entry, index) => <TerminalCard key={`${entry.timestamp || index}-${entry.command || "cmd"}`} entry={entry} />)
          ) : (
            <EmptyState message="When BOSS touches the terminal, the command and output will appear here." />
          )}
        </div>
      </article>
    </section>
  );
}

function TerminalCard(props: { entry: TerminalCommandRecord }) {
  const output = trimBlock(props.entry.stdout || props.entry.stderr || "");
  return (
    <article className={`terminal-card tone-${terminalTone(props.entry)}`}>
      <div className="terminal-head">
        <strong>{props.entry.command || "Command"}</strong>
        <span>{typeof props.entry.exit_code === "number" ? `Exit ${props.entry.exit_code}` : "Running"}</span>
      </div>
      {props.entry.workdir ? <p className="terminal-workdir">{props.entry.workdir}</p> : null}
      {output ? <pre className="terminal-output">{output}</pre> : <p className="terminal-empty">No captured output.</p>}
    </article>
  );
}

function SummaryListCard(props: {
  eyebrow: string;
  title: string;
  items: SummaryListItem[];
  empty: string;
}) {
  return (
    <article className="info-card">
      <SectionHeader eyebrow={props.eyebrow} title={props.title} />
      <div className="stack-list">
        {props.items.length ? (
          props.items.map((item) => (
            <ListRow
              key={`${item.title}-${item.detail}-${item.meta || ""}`}
              title={item.title}
              detail={item.detail}
              meta={item.meta}
              tone={item.tone || "neutral"}
            />
          ))
        ) : (
          <EmptyState message={props.empty} />
        )}
      </div>
    </article>
  );
}

function FactCard(props: {
  eyebrow: string;
  title: string;
  facts: Array<{ label: string; value: string }>;
}) {
  return (
    <article className="info-card">
      <SectionHeader eyebrow={props.eyebrow} title={props.title} />
      <dl className="fact-list">
        {props.facts.map((fact) => (
          <div key={`${fact.label}-${fact.value}`}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function ListRow(props: {
  title: string;
  detail: string;
  meta?: string;
  tone: Tone;
}) {
  return (
    <article className={`list-row tone-${props.tone}`}>
      <div className="list-copy">
        <strong>{props.title}</strong>
        <p>{props.detail}</p>
      </div>
      {props.meta ? <span className="list-meta">{props.meta}</span> : null}
    </article>
  );
}

function ChatTurnCard(props: { turn: ChatTurn; backendUrl: string }) {
  const mode = turnMode(props.turn);
  const pending = "pending" in props.turn && props.turn.pending;
  const statusText = pending ? "Live" : mode === "executed" ? "Done" : formatStatus(mode);
  const result = turnResult(props.turn);

  return (
    <article className="chat-turn">
      <div className="chat-bubble chat-user">
        <span className="bubble-label">You</span>
        <p>{props.turn.message}</p>
      </div>

      <div className="chat-bubble chat-boss">
        <div className="bubble-header">
          <span className="bubble-label">BOSS</span>
          <span className="bubble-status">{statusText}</span>
        </div>
        <p>{props.turn.response || (pending ? "Working through it…" : "No response text.")}</p>
        {hasBuildRunDetails(result) ? (
          <InternalRunCard backendUrl={props.backendUrl} result={result} />
        ) : null}
      </div>
    </article>
  );
}

function ActionNoticeCard(props: { notice: ActionNotice }) {
  const { notice } = props;
  return (
    <article className={`action-notice-card tone-${notice.tone}`}>
      <div className="action-notice-copy">
        <strong>{notice.title}</strong>
        <p>{notice.detail}</p>
      </div>
      <div className="action-notice-buttons">
        {notice.secondaryLabel && notice.secondaryAction ? (
          <button
            type="button"
            className="ghost-action"
            disabled={notice.secondaryDisabled}
            onClick={() => void notice.secondaryAction?.()}
          >
            {notice.secondaryLabel}
          </button>
        ) : null}
        {notice.primaryLabel && notice.primaryAction ? (
          <button
            type="button"
            className="primary-action"
            disabled={notice.primaryDisabled}
            onClick={() => void notice.primaryAction?.()}
          >
            {notice.primaryLabel}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function InternalRunCard(props: {
  backendUrl: string;
  result: ChatResultPayload & {
    task_id: number;
    run_kind: string;
    internal_summary: InternalRunSummary;
  };
}) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [details, setDetails] = useState<RunDetailsResponse | null>(null);
  const [error, setError] = useState("");
  const summary = props.result.internal_summary;

  async function toggleExpanded() {
    const nextExpanded = !expanded;
    setExpanded(nextExpanded);
    if (!nextExpanded || details || loading) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await fetchRunDetails(props.backendUrl, props.result.task_id, props.result.run_kind);
      setDetails(payload);
    } catch (fetchError) {
      setError(String(fetchError));
    } finally {
      setLoading(false);
    }
  }

  const task = details?.task;
  const planSteps = Array.isArray(task?.plan?.steps) ? task?.plan?.steps : [];
  const steps = Array.isArray(task?.steps) ? task.steps : [];

  return (
    <section className="run-summary-card">
      <div className="run-summary-head">
        <div>
          <span className="bubble-status">Work summary</span>
          <strong>{summary.goal || "Execution run"}</strong>
        </div>
        <button type="button" className="detail-toggle" onClick={() => void toggleExpanded()}>
          {expanded ? "Hide worklog" : "Show worklog"}
        </button>
      </div>

      <p className="run-summary-copy">{buildRunSummaryCopy(summary)}</p>

      <div className="run-summary-facts">
        <span>{formatStatus(summary.status)}</span>
        <span>{summary.total_steps || 0} steps</span>
        <span>{formatFileSummary(summary.changed_files, 2)}</span>
      </div>

      {expanded ? (
        <div className="run-detail-shell">
          {loading ? <p className="run-detail-note">Loading worklog…</p> : null}
          {error ? <p className="run-detail-note tone-bad">{error}</p> : null}

          {!loading && !error && planSteps.length ? (
            <div className="run-detail-block">
              <span className="run-detail-label">Planned steps</span>
              <ol className="run-plan-list">
                {planSteps.map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
            </div>
          ) : null}

          {!loading && !error && steps.length ? (
            <div className="run-detail-block">
              <span className="run-detail-label">Execution log</span>
              <div className="run-step-list">
                {steps.map((step) => (
                  <article
                    key={`${step.step_index ?? "step"}-${step.title || "untitled"}`}
                    className={`run-step-card tone-${statusTone(step.status)}`}
                  >
                    <div className="run-step-head">
                      <strong>{step.title || "Untitled step"}</strong>
                      <span>{formatStatus(step.status)}</span>
                    </div>

                    <div className="run-step-meta">
                      <span>Attempt {step.iterations || 0}</span>
                      <span>{formatFileSummary(step.files_changed, 2)}</span>
                    </div>

                    <p className="run-step-copy">{testOutputSummary(step)}</p>

                    {trimText(step.audit_output) ? (
                      <p className="run-step-copy">Review: {trimText(step.audit_output)}</p>
                    ) : null}

                    {step.errors?.length ? (
                      <p className="run-step-copy">Errors: {trimText(step.errors.join(" "))}</p>
                    ) : null}
                  </article>
                ))}
              </div>
            </div>
          ) : null}

          {!loading && !error && !planSteps.length && !steps.length ? (
            <p className="run-detail-note">No additional worklog was recorded for this run.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DetailsDrawer(props: {
  onClose: () => void;
  onOpenLegacyUi: () => void;
  suggestedItems: SummaryListItem[];
  watchItems: SummaryListItem[];
  recentOutcomeItems: SummaryListItem[];
  rawActivityItems: SummaryListItem[];
  runItems: SummaryListItem[];
  recentEditItems: SummaryListItem[];
  openFileItems: SummaryListItem[];
  rootItems: SummaryListItem[];
  health: HealthSnapshot;
  permissions: PermissionSnapshot;
  metrics: MetricSnapshot;
  workspace: WorkspaceSnapshot;
}) {
  return (
    <>
      <button type="button" className="drawer-backdrop" aria-label="Close details" onClick={props.onClose} />
      <aside className="details-drawer hud-panel">
        <div className="drawer-head">
          <div>
            <p className="section-eyebrow">Details</p>
            <h3>Project and runtime details</h3>
          </div>
          <button type="button" className="ghost-action" onClick={props.onClose}>
            Close
          </button>
        </div>

        <div className="drawer-scroll">
          <SummaryListCard
            eyebrow="Up next"
            title="Suggested next steps"
            items={props.suggestedItems}
            empty="Ask BOSS for the next best move."
          />

          <SummaryListCard
            eyebrow="Watchouts"
            title="Things to keep an eye on"
            items={props.watchItems}
            empty="No blockers recorded."
          />

          <SummaryListCard
            eyebrow="Recent"
            title="Recent outcomes"
            items={props.recentOutcomeItems}
            empty="No recent outcomes recorded."
          />

          <SummaryListCard
            eyebrow="Internal"
            title="Raw agent activity"
            items={props.rawActivityItems}
            empty="No raw agent activity recorded."
          />

          <SummaryListCard
            eyebrow="Runs"
            title="Recent runs"
            items={props.runItems}
            empty="No runs recorded."
          />

          <SummaryListCard
            eyebrow="Workspace"
            title="Recent edits"
            items={props.recentEditItems}
            empty="No recent edits captured."
          />

          <SummaryListCard
            eyebrow="Workspace"
            title="Open files"
            items={props.openFileItems}
            empty="No open files captured."
          />

          <SummaryListCard
            eyebrow="Roots"
            title="Reachable filesystem scope"
            items={props.rootItems}
            empty="No workspace roots configured."
          />

          <FactCard
            eyebrow="Runtime"
            title="Health and access"
            facts={[
              { label: "Health", value: formatStatus(props.health.status) },
              { label: "Success rate", value: formatPercent(props.health.autonomous_success_rate) },
              { label: "Eval failures", value: formatCount(props.health.recent_eval_failures) },
              { label: "Watchers", value: props.health.workspace_watchers || "Unknown" },
              {
                label: "Web research",
                value: props.permissions.allow_web_research ? "Enabled" : "Off",
              },
              { label: "MCP", value: props.permissions.allow_mcp ? "Enabled" : "Off" },
            ]}
          />

          <FactCard
            eyebrow="Workspace"
            title="Machine state"
            facts={[
              {
                label: "Last terminal",
                value: props.workspace.last_terminal_command || "No terminal command captured.",
              },
              { label: "Last test result", value: workspaceTestSummary(props.workspace) },
              { label: "Git diff", value: props.workspace.last_git_diff || "No git diff captured." },
              {
                label: "Writable roots",
                value: props.permissions.writable_roots.join(", ") || "None configured",
              },
              {
                label: "Task runs",
                value: formatCount(props.metrics.task_runs_recorded),
              },
              {
                label: "Token usage",
                value: formatCount(props.metrics.token_usage?.total_tokens),
              },
            ]}
          />
        </div>

        <div className="drawer-footer">
          <button type="button" className="ghost-action" onClick={props.onOpenLegacyUi}>
            Open legacy web view
          </button>
        </div>
      </aside>
    </>
  );
}

function EmptyState(props: { message: string }) {
  return <div className="empty-state">{props.message}</div>;
}
