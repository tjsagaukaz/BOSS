import { invoke } from "@tauri-apps/api/core";
import { startTransition, useEffect, useRef, useState } from "react";

import { fetchRunDetails, loadCommandCenter, postJson, setActiveProject, streamChat } from "./api";
import type {
  ActivityItem,
  BrainSnapshot,
  BuildTaskStep,
  ChatResultPayload,
  ChatHistoryTurn,
  CommandCenterSnapshot,
  HealthSnapshot,
  InternalRunSummary,
  MetricSnapshot,
  PermissionSnapshot,
  ProjectCatalogItem,
  RecommendationItem,
  RiskItem,
  RunDetailsResponse,
  RootSnapshot,
  RunSummary,
  StreamEvent,
  StreamingTurn,
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

const QUICK_PROMPTS = [
  "Fix the highest-priority issue in the active project.",
  "Summarize what changed and tell me the next best move.",
  "Audit the current project for blockers.",
];

const REFRESH_INTERVAL_MS = 5000;
const INTERNAL_AGENTS = new Set(["architect", "engineer", "test", "auditor"]);

const EMPTY_WORKSPACE: WorkspaceSnapshot = {
  active_project: "__workspace__",
  open_files: [],
  recent_edits: [],
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

function turnMode(turn: ChatTurn): string {
  if ("pending" in turn) {
    return turn.metadata.mode || "chat";
  }
  const metadata = turn.metadata ?? {};
  const mode = metadata.mode;
  return typeof mode === "string" ? mode : "chat";
}

function healthTone(status: string): Tone {
  const normalized = status.toLowerCase();
  if (normalized === "stable" || normalized === "healthy") return "good";
  if (normalized === "degraded" || normalized === "warning") return "warn";
  if (normalized === "failed" || normalized === "critical") return "bad";
  return "neutral";
}

function runTone(status?: string | null): Tone {
  const normalized = String(status || "unknown").toLowerCase();
  if (normalized === "completed" || normalized === "healthy") return "good";
  if (normalized === "running" || normalized === "queued") return "warn";
  if (normalized === "failed" || normalized === "error") return "bad";
  return "neutral";
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
  const lastEvent = timeline[0];
  if (lastEvent?.title || lastEvent?.message) {
    return lastEvent.message || lastEvent.title || "Waiting for the next task.";
  }
  return "Waiting for the next task.";
}

function latestWorkSummary(runs: RunSummary[], timeline: TimelineEvent[]): string {
  const running = runs.find((item) => {
    const status = String(item.status || "").toLowerCase();
    return status === "running" || status === "queued";
  });
  if (running) {
    return `BOSS is working on ${running.title || "the active run"}.`;
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

function trimText(value: string | undefined, limit = 240): string {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trimEnd()}…`;
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
    return passed ? "Tests passed." : failure || message || "Tests failed.";
  }
  return message || failure || "No dedicated test result captured.";
}

function buildRunSummaryCopy(summary: InternalRunSummary): string {
  const totalSteps = Number(summary.total_steps || 0);
  const completedSteps = Number(summary.completed_steps || 0);
  const failedSteps = Number(summary.failed_steps || 0);
  const retries = Number(summary.retries || 0);
  const changedFilesCount = Number(summary.changed_files_count || 0);
  const parts: string[] = [];
  if (totalSteps) {
    parts.push(`Architect planned ${totalSteps} step${totalSteps === 1 ? "" : "s"}.`);
    parts.push(`Execution completed ${completedSteps}/${totalSteps} step${totalSteps === 1 ? "" : "s"}.`);
  } else {
    parts.push("Execution details are available for this run.");
  }
  if (failedSteps) {
    parts.push(`${failedSteps} step${failedSteps === 1 ? "" : "s"} still need follow-up.`);
  }
  if (retries) {
    parts.push(`${retries} retr${retries === 1 ? "y" : "ies"} across the run.`);
  }
  if (changedFilesCount) {
    parts.push(`Changed ${changedFilesCount} file${changedFilesCount === 1 ? "" : "s"}.`);
  }
  if (summary.shipping_status) {
    parts.push(`Shipping is ${summary.shipping_status.replace(/_/g, " ")}.`);
  }
  return parts.join(" ");
}

export function App() {
  const [backendUrl, setBackendUrl] = useState("");
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("starting");
  const [backendMessage, setBackendMessage] = useState("Starting the BOSS backend...");
  const [projects, setProjects] = useState<ProjectCatalogItem[]>([]);
  const [activeProject, setActiveProjectState] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot>(EMPTY_WORKSPACE);
  const [brain, setBrain] = useState<BrainSnapshot>(EMPTY_BRAIN);
  const [recommendations, setRecommendations] = useState<RecommendationItem[]>([]);
  const [risks, setRisks] = useState<RiskItem[]>([]);
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
  const streamAbortRef = useRef<AbortController | null>(null);
  const activeStreamIdRef = useRef<string | null>(null);

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
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [backendStatus, backendUrl]);

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
      setRecommendations(snapshot.next.recommendations ?? []);
      setRisks(snapshot.risks.risks ?? []);
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

  function openLegacyUi() {
    if (!backendUrl) return;
    window.open(`${backendUrl}/`, "_blank", "noopener,noreferrer");
  }

  function usePrompt(prompt: string) {
    setComposerValue(prompt);
  }

  const activeProjectMeta = projects.find((project) => project.key === activeProject) ?? null;
  const chatTurns: ChatTurn[] = streamingTurn ? [...chatHistory, streamingTurn] : chatHistory;
  const latestActivity = latestWorkSummary(runs, timeline);
  const focusHeadline =
    brain.brain.current_focus || brain.brain.mission || "Tell BOSS what you want done.";
  const focusDescription =
    brain.brain.current_focus && brain.brain.mission
      ? brain.brain.mission
      : "Ask for a fix, an audit, or a summary. System detail stays tucked away unless you open it.";

  const suggestedItems: SummaryListItem[] = recommendations.length
    ? recommendations.slice(0, 3).map((item) => ({
        title: item.title,
        detail: item.reason || item.source || "Suggested next step",
      }))
    : (brain.brain.next_priorities || []).slice(0, 3).map((item) => ({
        title: item,
        detail: "Project priority",
      }));

  const watchItems: SummaryListItem[] = risks.slice(0, 3).map((item) => ({
    title: item.title,
    detail: item.reason || item.severity || "Reported risk",
    tone: item.severity?.toLowerCase() === "high" ? "bad" : "warn",
  }));

  const recentWorkItems: SummaryListItem[] = runs.length
    ? runs.slice(0, 4).map((run) => ({
        title: run.title || String(run.identifier || "Untitled run"),
        detail: [formatStatus(run.status), run.project_name || "workspace"].filter(Boolean).join(" · "),
        meta: formatTimestamp(run.timestamp),
        tone: runTone(run.status),
      }))
    : timeline
        .filter((event) => !isInternalTimelineEvent(event))
        .slice(0, 4)
        .map((event) => ({
          title: event.title || "Event",
          detail: event.message || event.status || "Recent activity",
          meta: formatTimestamp(event.timestamp),
          tone:
            event.status === "failed"
              ? "bad"
              : event.status === "completed"
                ? "good"
                : "neutral",
        }));

  const agentActivityItems: SummaryListItem[] = activities.length
    ? activities.slice(0, 4).map((item) => ({
        title: item.agent,
        detail: item.message || item.status || "Idle",
        meta: item.project_name || "workspace",
        tone: item.status === "running" ? "warn" : item.status === "failed" ? "bad" : "neutral",
      }))
    : timeline
        .filter((event) => isInternalTimelineEvent(event))
        .slice(0, 4)
        .map((event) => ({
          title: event.agent || event.title || "Agent",
          detail: event.message || event.status || "Idle",
          meta: formatTimestamp(event.timestamp),
          tone:
            event.status === "failed"
              ? "bad"
              : event.status === "completed"
                ? "good"
                : "warn",
        }));

  const runItems: SummaryListItem[] = runs.slice(0, 5).map((run) => ({
    title: run.title || String(run.identifier || "Untitled run"),
    detail: [run.kind || "run", run.project_name || "workspace"].filter(Boolean).join(" · "),
    meta: `${formatStatus(run.status)} · ${formatTimestamp(run.timestamp)}`,
    tone: runTone(run.status),
  }));

  const openFileItems: SummaryListItem[] = (workspace.open_files || []).slice(0, 5).map((file) => ({
    title: file,
    detail: "Open in the tracked workspace",
  }));

  const recentEditItems: SummaryListItem[] = (workspace.recent_edits || []).slice(0, 5).map((item, index) => ({
    title: item.file || item.path || `Edit ${index + 1}`,
    detail: item.summary || item.type || "Recorded workspace edit",
  }));

  const openProblemItems: SummaryListItem[] = (brain.brain.open_problems || []).slice(0, 5).map((item) => ({
    title: item,
    detail: "Needs attention",
  }));

  const rootItems: SummaryListItem[] = (roots.roots || []).slice(0, 5).map((root) => ({
    title: root.name,
    detail: root.path,
    meta: `${root.mode}${root.enabled ? "" : " · disabled"}`,
    tone: root.enabled ? "neutral" : "warn",
  }));

  if (backendStatus !== "ready") {
    const startupTone: Tone = backendStatus === "failed" ? "bad" : "warn";
    return (
      <main className="startup-shell">
        <section className="startup-card">
          <p className="app-kicker">BOSS</p>
          <h1>Starting your workspace</h1>
          <p className="startup-copy">
            BOSS is bringing the local backend online. Once it is ready, the main screen stays
            focused on the conversation and keeps the system internals out of the way.
          </p>
          <div className={`status-chip tone-${startupTone}`}>
            <span className="status-dot" />
            <span>{backendMessage}</span>
          </div>
          <div className="startup-actions">
            <button type="button" className="primary-action" onClick={retryLaunch}>
              Retry launch
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="workspace-shell">
      <div className="app-backdrop" />

      <header className="app-header">
        <div className="app-brand">
          <p className="app-kicker">BOSS</p>
          <div>
            <h1>Work with BOSS</h1>
            <p>Ask for work, get a clear response, and keep the machinery in the background.</p>
          </div>
        </div>

        <div className="header-controls">
          <label className="project-picker">
            <span>Project</span>
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

          <button type="button" className="ghost-action" onClick={() => void refreshAll()}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>

          <button
            type="button"
            className="ghost-action"
            onClick={() => setShowDetails((current) => !current)}
          >
            {showDetails ? "Hide internal details" : "Show internal details"}
          </button>
        </div>
      </header>

      <div className="workspace-grid">
        <section className="main-column">
          <section className="panel focus-card">
            <div className="focus-head">
              <div>
                <p className="section-eyebrow">Current focus</p>
                <h2>{focusHeadline}</h2>
              </div>
              <span className={`health-pill tone-${healthTone(health.status || "unknown")}`}>
                {formatStatus(health.status)}
              </span>
            </div>

            <p className="supporting-copy">{focusDescription}</p>

            <div className="focus-meta">
              <span>Project: {projectLabel(activeProject)}</span>
              <span>Updated {formatTimestamp(workspace.updated_at)}</span>
            </div>

            <p className="focus-note">Now: {latestActivity}</p>
          </section>

          {surfaceError ? (
            <section className="notice-banner tone-bad">
              <span>Runtime notice</span>
              <strong>{surfaceError}</strong>
            </section>
          ) : null}

          <section className="panel composer-card">
            <SectionHeader
              eyebrow="Ask"
              title="What should BOSS do?"
              detail={executeMode ? "Changes enabled" : "Plan only"}
            />

            <div className="quick-prompts">
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
                rows={5}
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
              <label className="simple-toggle">
                <input
                  type="checkbox"
                  checked={executeMode}
                  onChange={(event) => setExecuteMode(event.target.checked)}
                />
                <span>{executeMode ? "BOSS can make changes" : "Reply only"}</span>
              </label>

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
                  {sending ? "Working..." : executeMode ? "Start work" : "Ask BOSS"}
                </button>
              </div>
            </div>

            <p className="helper-copy">Project: {projectLabel(activeProject)}. Press Cmd/Ctrl+Enter to send.</p>
          </section>

          <section className="panel conversation-card">
            <SectionHeader
              eyebrow="Conversation"
              title={chatTurns.length ? "Latest thread" : "Ready when you are"}
              detail={chatTurns.length ? `${chatTurns.length} turns` : undefined}
            />

            <div className="chat-scroll">
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
          </section>

          {showDetails ? (
            <section className="panel internal-card">
              <SectionHeader
                eyebrow="Internal"
                title="System details"
                detail="Hidden by default"
              />

              <div className="internal-grid">
                <SummaryListCard
                  eyebrow="Internal"
                  title="Agent activity"
                  items={agentActivityItems}
                  empty="No active internal work right now."
                />

                <SummaryListCard
                  eyebrow="Runs"
                  title="Recent runs"
                  items={runItems}
                  empty="No runs recorded yet."
                />

                <SummaryListCard
                  eyebrow="Workspace"
                  title="Recent edits"
                  items={recentEditItems}
                  empty="No recent edits captured."
                />

                <SummaryListCard
                  eyebrow="Workspace"
                  title="Open files"
                  items={openFileItems}
                  empty="No open files captured."
                />

                <SummaryListCard
                  eyebrow="Brain"
                  title="Open problems"
                  items={openProblemItems}
                  empty="No open problems recorded."
                />

                <SummaryListCard
                  eyebrow="Roots"
                  title="Reachable filesystem scope"
                  items={rootItems}
                  empty="No workspace roots configured."
                />

                <FactCard
                  eyebrow="Runtime"
                  title="Health and access"
                  facts={[
                    { label: "Health", value: formatStatus(health.status) },
                    { label: "Success rate", value: formatPercent(health.autonomous_success_rate) },
                    { label: "Eval failures", value: formatCount(health.recent_eval_failures) },
                    { label: "Watchers", value: health.workspace_watchers || "Unknown" },
                    {
                      label: "Web research",
                      value: permissions.allow_web_research ? "Enabled" : "Off",
                    },
                    { label: "MCP", value: permissions.allow_mcp ? "Enabled" : "Off" },
                  ]}
                />

                <FactCard
                  eyebrow="Workspace"
                  title="Recent machine state"
                  facts={[
                    {
                      label: "Last terminal",
                      value: workspace.last_terminal_command || "No terminal command captured.",
                    },
                    { label: "Last test result", value: workspaceTestSummary(workspace) },
                    { label: "Git diff", value: workspace.last_git_diff || "No git diff captured." },
                    {
                      label: "Writable roots",
                      value: permissions.writable_roots.join(", ") || "None configured",
                    },
                    {
                      label: "Task runs",
                      value: formatCount(metrics.task_runs_recorded),
                    },
                    {
                      label: "Token usage",
                      value: formatCount(metrics.token_usage?.total_tokens),
                    },
                  ]}
                />
              </div>

              <div className="internal-actions">
                <button type="button" className="ghost-action" onClick={openLegacyUi}>
                  Open legacy web view
                </button>
              </div>
            </section>
          ) : null}
        </section>

        <aside className="side-column">
          <section className="panel side-card project-card">
            <SectionHeader
              eyebrow="Project"
              title={projectLabel(activeProject)}
              detail={activeProjectMeta?.source_root || "workspace"}
            />

            <p className="supporting-copy">
              {activeProjectMeta?.root || roots.primary_root || "Working across the full workspace."}
            </p>

            <div className="project-highlights">
              <div>
                <span>Mission</span>
                <strong>{brain.brain.mission || "No mission summary yet."}</strong>
              </div>
              <div>
                <span>Next update</span>
                <strong>{formatTimestamp(brain.brain.updated_at || workspace.updated_at)}</strong>
              </div>
            </div>
          </section>

          <SummaryListCard
            eyebrow="Up next"
            title="Suggested next steps"
            items={suggestedItems}
            empty="Ask BOSS for the next best move."
          />

          {watchItems.length ? (
            <SummaryListCard
              eyebrow="Watchouts"
              title="Things to keep an eye on"
              items={watchItems}
              empty="No blockers reported."
            />
          ) : null}

          {recentWorkItems.length ? (
            <SummaryListCard
              eyebrow="Recent"
              title="Recent outcomes"
              items={recentWorkItems}
              empty="No active work right now."
            />
          ) : null}
        </aside>
      </div>
    </main>
  );
}

function SectionHeader(props: { eyebrow: string; title: string; detail?: string }) {
  return (
    <div className="section-header">
      <div>
        <p className="section-eyebrow">{props.eyebrow}</p>
        <h3>{props.title}</h3>
      </div>
      {props.detail ? <span className="section-detail">{props.detail}</span> : null}
    </div>
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
  const statusText = pending ? "Working" : formatStatus(mode);
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
        <p>{props.turn.response || (pending ? "Thinking..." : "No response text.")}</p>
        {hasBuildRunDetails(result) ? (
          <InternalRunCard backendUrl={props.backendUrl} result={result} />
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
          <span className="bubble-status">Internal run</span>
          <strong>{summary.goal || "Build run"}</strong>
        </div>
        <button type="button" className="detail-toggle" onClick={() => void toggleExpanded()}>
          {expanded ? "Hide details" : "Show details"}
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
          {loading ? <p className="run-detail-note">Loading internal run details…</p> : null}
          {error ? <p className="run-detail-note tone-bad">{error}</p> : null}

          {!loading && !error && planSteps.length ? (
            <div className="run-detail-block">
              <span className="run-detail-label">Architect plan</span>
              <ol className="run-plan-list">
                {planSteps.map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
            </div>
          ) : null}

          {!loading && !error && steps.length ? (
            <div className="run-detail-block">
              <span className="run-detail-label">Child runs</span>
              <div className="run-step-list">
                {steps.map((step) => (
                  <article
                    key={`${step.step_index ?? "step"}-${step.title || "untitled"}`}
                    className={`run-step-card tone-${runTone(step.status)}`}
                  >
                    <div className="run-step-head">
                      <strong>{step.title || "Untitled step"}</strong>
                      <span>{formatStatus(step.status)}</span>
                    </div>

                    <div className="run-step-meta">
                      <span>Engineer</span>
                      <span>Attempt {step.iterations || 0}</span>
                      <span>{formatFileSummary(step.files_changed, 2)}</span>
                    </div>

                    <p className="run-step-copy">{testOutputSummary(step)}</p>

                    {trimText(step.audit_output) ? (
                      <p className="run-step-copy">Auditor: {trimText(step.audit_output)}</p>
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
            <p className="run-detail-note">No additional internal detail was recorded for this run.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function EmptyState(props: { message: string }) {
  return <div className="empty-state">{props.message}</div>;
}
