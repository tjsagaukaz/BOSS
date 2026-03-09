const state = {
  runs: [],
  tasks: [],
  agents: [],
  activities: [],
  timeline: [],
  workspace: {},
  memory: {},
  evolution: {},
  health: {},
  metrics: {},
  runLedger: [],
  loopStatus: null,
  logs: [],
  chatHistory: [],
  activeProject: null,
  projectCatalog: [],
  projectsStatus: {},
  brain: {},
  roadmap: {},
  risks: [],
  recommendations: [],
  portfolio: {},
  permissions: {},
  mcp: {},
  roots: {},
  streamingTurn: null,
  activeStreamId: null,
  selectedRun: null,
  selectedRunDetails: null,
  diffViewer: {
    open: false,
    loading: false,
    runId: null,
    kind: "build",
    files: [],
    activePath: null,
    error: null,
  },
  centerView: "chat",
  collapsedPanels: {},
  commandPaletteOpen: false,
  lastRefreshAt: null,
  chatSessions: [],
  activeChatSessionId: null,
  leftRailCollapsed: false,
  rightRailCollapsed: true,
};

const urlParams = new URLSearchParams(window.location.search);
const tauriMode = urlParams.get("app") === "tauri";
const focusChatMode = urlParams.get("focus") === "chat";
const chatViewMode = urlParams.get("view") === "chat";
let chatComposerArmed = focusChatMode || chatViewMode;
const CHAT_SESSION_STORAGE_KEY = "boss.ui.chatSessions.v1";
const UI_PREFS_STORAGE_KEY = "boss.ui.preferences.v1";

function readStorage(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch (_error) {
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (_error) {
    // Ignore storage write failures.
  }
}

function loadUiPrefs() {
  const prefs = readStorage(UI_PREFS_STORAGE_KEY, {});
  state.leftRailCollapsed = Boolean(prefs.leftRailCollapsed);
  state.rightRailCollapsed = prefs.rightRailCollapsed !== false;
}

function persistUiPrefs() {
  writeStorage(UI_PREFS_STORAGE_KEY, {
    leftRailCollapsed: state.leftRailCollapsed,
    rightRailCollapsed: state.rightRailCollapsed,
  });
}

function qs(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatMultiline(value) {
  return escapeHtml(value || "").replaceAll("\n", "<br>");
}

function chatProjectKey() {
  return isWorkspaceProject(state.activeProject) ? "__workspace__" : String(state.activeProject || "__workspace__");
}

function newSessionId() {
  return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function defaultSessionName(count = 1) {
  return count <= 1 ? "Current thread" : `Thread ${count}`;
}

function loadChatSessionStore() {
  return readStorage(CHAT_SESSION_STORAGE_KEY, {});
}

function saveChatSessionStore(store) {
  writeStorage(CHAT_SESSION_STORAGE_KEY, store);
}

function currentSessionStore() {
  const store = loadChatSessionStore();
  const key = chatProjectKey();
  if (!store[key]) {
    store[key] = {
      active_id: null,
      sessions: [],
    };
  }
  return { store, key, bucket: store[key] };
}

function ensureChatSessionState() {
  const { store, bucket } = currentSessionStore();
  if (!Array.isArray(bucket.sessions)) {
    bucket.sessions = [];
  }

  if (!bucket.sessions.length) {
    const sessionId = newSessionId();
    bucket.sessions.push({
      id: sessionId,
      name: defaultSessionName(1),
      pinned: false,
      deleted: false,
      turn_ids: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
    bucket.active_id = sessionId;
  }

  const visibleSessions = bucket.sessions.filter((session) => !session.deleted);
  if (!visibleSessions.some((session) => session.id === bucket.active_id)) {
    bucket.active_id = visibleSessions[0]?.id || null;
  }

  const assigned = new Set(
    visibleSessions.flatMap((session) => (session.turn_ids || []).map((turnId) => String(turnId))),
  );
  const fallbackSession = visibleSessions.find((session) => session.id === bucket.active_id) || visibleSessions[0];
  for (const turn of state.chatHistory) {
    const turnId = String(turn.id);
    if (!assigned.has(turnId) && fallbackSession) {
      fallbackSession.turn_ids.push(turnId);
      fallbackSession.updated_at = new Date().toISOString();
      assigned.add(turnId);
    }
  }

  bucket.sessions.sort((left, right) => {
    if (left.deleted !== right.deleted) return left.deleted ? 1 : -1;
    if (left.pinned !== right.pinned) return left.pinned ? -1 : 1;
    return new Date(right.updated_at || 0).getTime() - new Date(left.updated_at || 0).getTime();
  });

  saveChatSessionStore(store);
  state.chatSessions = bucket.sessions.filter((session) => !session.deleted);
  state.activeChatSessionId = bucket.active_id;
}

function updateCurrentSession(mutator) {
  const { store, bucket } = currentSessionStore();
  const session = (bucket.sessions || []).find((item) => item.id === bucket.active_id && !item.deleted);
  if (!session) return;
  mutator(session, bucket);
  session.updated_at = new Date().toISOString();
  saveChatSessionStore(store);
  ensureChatSessionState();
}

function assignTurnToActiveSession(turnId) {
  if (turnId == null) return;
  updateCurrentSession((session) => {
    const value = String(turnId);
    if (!session.turn_ids.includes(value)) {
      session.turn_ids.push(value);
    }
  });
}

function createChatSession() {
  const { store, bucket } = currentSessionStore();
  const sessionId = newSessionId();
  const visibleCount = (bucket.sessions || []).filter((session) => !session.deleted).length + 1;
  bucket.sessions.push({
    id: sessionId,
    name: defaultSessionName(visibleCount),
    pinned: false,
    deleted: false,
    turn_ids: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  });
  bucket.active_id = sessionId;
  saveChatSessionStore(store);
  ensureChatSessionState();
  renderChatSessionList();
  renderChat();
  focusChatComposer(true);
}

function renameActiveChatSession() {
  const session = state.chatSessions.find((item) => item.id === state.activeChatSessionId);
  if (!session) return;
  const nextName = window.prompt("Rename this chat", session.name || "Current thread");
  if (!nextName) return;
  updateCurrentSession((current) => {
    current.name = nextName.trim() || current.name;
  });
  renderChatSessionList();
}

function togglePinActiveChatSession() {
  updateCurrentSession((session) => {
    session.pinned = !session.pinned;
  });
  renderChatSessionList();
}

function deleteActiveChatSession() {
  if (!state.chatSessions.length) return;
  const confirmed = window.confirm("Delete this chat thread from the sidebar?");
  if (!confirmed) return;
  const { store, bucket } = currentSessionStore();
  const session = (bucket.sessions || []).find((item) => item.id === bucket.active_id && !item.deleted);
  if (!session) return;
  session.deleted = true;
  session.updated_at = new Date().toISOString();
  const fallback = (bucket.sessions || []).find((item) => !item.deleted && item.id !== session.id);
  bucket.active_id = fallback?.id || null;
  saveChatSessionStore(store);
  ensureChatSessionState();
  renderChatSessionList();
  renderChat();
}

function currentTurns() {
  const sessionIds = new Set(
    (state.chatSessions.find((session) => session.id === state.activeChatSessionId)?.turn_ids || []).map((id) => String(id)),
  );
  const turns = !sessionIds.size
    ? state.chatHistory
    : state.chatHistory.filter((turn) => sessionIds.has(String(turn.id)));

  if (state.streamingTurn && state.streamingTurn.metadata?.session_id === state.activeChatSessionId) {
    return [...turns, state.streamingTurn];
  }
  return turns;
}

function isWorkspaceProject(projectName) {
  return !projectName || projectName === "__workspace__" || projectName === "workspace";
}

function activeScopeLabel() {
  return isWorkspaceProject(state.activeProject) ? "Workspace" : state.activeProject;
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return `${Math.round(Number(value) * 100)}%`;
}

function formatNumber(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return Number(value).toFixed(digits);
}

function formatTimestamp(value) {
  if (!value) {
    return "just now";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatRelativeTime(value) {
  if (!value) return "just now";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "just now";
  const deltaSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (deltaSeconds < 60) return `${deltaSeconds}s ago`;
  if (deltaSeconds < 3600) return `${Math.round(deltaSeconds / 60)}m ago`;
  if (deltaSeconds < 86400) return `${Math.round(deltaSeconds / 3600)}h ago`;
  return `${Math.round(deltaSeconds / 86400)}d ago`;
}

function truncate(value, maxLength = 140) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function statusColor(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "ready", "passed", "stable", "healthy", "approved", "idle"].includes(value)) return "text-neutral-200";
  if (["failed", "error", "aborted", "degraded", "blocked", "cancelled", "cancel_requested", "timeout"].includes(value)) return "text-rose-300";
  if (["retrying", "paused", "warning", "degraded", "suggested"].includes(value)) return "text-amber-300";
  if (["running", "planning", "chat", "reviewing", "executed", "thinking"].includes(value)) return "text-rose-200";
  return "text-neutral-400";
}

function healthPillClasses(status) {
  const value = String(status || "").toLowerCase();
  if (["stable", "healthy"].includes(value)) {
    return "border border-neutral-800 bg-neutral-950 text-neutral-200";
  }
  if (["degraded", "warning", "unstable"].includes(value)) {
    return "border border-amber-700/60 bg-amber-500/10 text-amber-200";
  }
  if (["failed", "error", "critical"].includes(value)) {
    return "border border-rose-700/60 bg-rose-500/10 text-rose-200";
  }
  return "border border-neutral-800 text-neutral-300";
}

function dotClasses(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "passed", "healthy", "stable", "idle"].includes(value)) {
    return "bg-neutral-200";
  }
  if (["failed", "error", "aborted", "timeout"].includes(value)) {
    return "bg-rose-400";
  }
  if (["retrying", "paused", "waiting", "queued"].includes(value)) {
    return "bg-amber-400";
  }
  return "bg-rose-300";
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  return response.json();
}

function focusChatComposer(force = false) {
  if (!force && !chatComposerArmed) {
    return;
  }
  const input = qs("chatInput");
  if (!input) {
    return;
  }
  window.requestAnimationFrame(() => {
    autoResizeComposer(input);
    input.focus({ preventScroll: false });
    input.scrollIntoView({ block: "nearest", behavior: tauriMode ? "smooth" : "auto" });
    chatComposerArmed = false;
  });
}

function autoResizeComposer(node = qs("chatInput")) {
  if (!node) return;
  node.style.height = "0px";
  node.style.height = `${Math.max(132, Math.min(node.scrollHeight, 320))}px`;
}

function setCenterView(view) {
  state.centerView = "chat";
  const chatNode = qs("chatWorkspace");
  if (chatNode) {
    chatNode.classList.remove("hidden");
  }
  renderLeftNav();
  chatComposerArmed = true;
  focusChatComposer(true);
}

function normalizeProjectCatalog() {
  const catalog = Array.isArray(state.projectCatalog) ? state.projectCatalog : [];
  return catalog
    .map((item) => {
      if (typeof item === "string") {
        return { key: item, display_name: item, root: "", source_root: "workspace" };
      }
      return {
        key: item.key || item.name || item.display_name,
        display_name: item.display_name || item.key || item.name,
        root: item.root || "",
        source_root: item.source_root || "workspace",
      };
    })
    .filter((item) => item.key);
}

function portfolioProjectMap() {
  const projects = state.portfolio?.projects || [];
  return Object.fromEntries(
    projects.map((project) => [String(project.display_name || project.key || "").toLowerCase(), project]),
  );
}

function currentBrain() {
  return state.brain?.brain || {};
}

function currentRoadmap() {
  return state.roadmap || {};
}

function suggestedFocus() {
  const brain = currentBrain();
  if (isWorkspaceProject(state.activeProject)) {
    return "Portfolio orchestration";
  }
  return brain.current_focus || brain.focus || "Operator mode";
}

function displayNextPriority() {
  const brain = currentBrain();
  if (isWorkspaceProject(state.activeProject)) {
    return state.recommendations?.[0]?.title || "Find the highest leverage move across the portfolio";
  }
  return (brain.next_priorities || [])[0] || "Keep pushing the current focus";
}

function displayKnownRisk() {
  const brain = currentBrain();
  if (isWorkspaceProject(state.activeProject)) {
    return state.risks?.[0]?.title || "No major portfolio risk is surfaced right now";
  }
  return (brain.known_risks || [])[0] || "No major blockers recorded";
}

function emptyStatePrompts() {
  if (isWorkspaceProject(state.activeProject)) {
    return [
      "Find the best project for us to work on next",
      "Search my Mac for the repo we should work from",
      "Create a new project folder and switch us there",
    ];
  }
  return [
    "Inspect this repo and tell me what matters",
    "What should we build next here?",
    "Create a fresh project and switch us there",
  ];
}

function buildMetricCard(label, value, tone = "text-slate-100", hint = "") {
  return `
    <div class="rounded-2xl border border-slate-800 bg-slate-900/72 px-4 py-3">
      <p class="text-[10px] uppercase tracking-[0.28em] text-slate-500">${escapeHtml(label)}</p>
      <p class="mt-2 text-xl font-semibold ${tone}">${escapeHtml(value)}</p>
      ${hint ? `<p class="mt-1 text-xs text-slate-500">${escapeHtml(hint)}</p>` : ""}
    </div>
  `;
}

function currentHeadline() {
  const health = state.health || {};
  const metrics = state.metrics || {};
  const liveActivity = (state.activities || []).find((activity) =>
    ["running", "planning", "reviewing", "thinking", "chat"].includes(String(activity.status || "").toLowerCase()),
  );
  if (liveActivity) {
    return `${liveActivity.agent || "BOSS"} ${liveActivity.message || "is working"}`;
  }
  const activeTask = (state.tasks || []).find((task) =>
    ["running", "planning", "retrying", "queued"].includes(String(task.status || "").toLowerCase()),
  );
  if (activeTask) {
    return activeTask.title || activeTask.agent_type || "Running the next step";
  }
  const healthStatus = String(health.status || "unknown").toLowerCase();
  if (healthStatus === "healthy" || healthStatus === "stable") {
    return `Focus: ${suggestedFocus()}`;
  }
  if (healthStatus === "degraded") {
    return `Attention: ${(health.status_reasons || [])[0] || "Something needs attention"}`;
  }
  return `Success ${formatPercent(health.autonomous_success_rate)} • Parallel ${(metrics.run_graph?.parallel_mode || "off").toUpperCase()}`;
}

function renderStatusBar() {
  const bar = qs("statusBar");
  if (!bar) return;
  const health = state.health || {};
  const projectLabel = activeScopeLabel();
  const healthStatus = String(health.status || "unknown");
  const statusText = healthStatus === "unknown" ? "Starting" : healthStatus.charAt(0).toUpperCase() + healthStatus.slice(1);
  const statusIcon = healthStatus === "stable" ? "🟢" : healthStatus === "degraded" ? "🟡" : healthStatus === "failed" ? "🔴" : "⚪";
  const timeoutCount = Number(health.step_timeouts || 0);
  const alertText = timeoutCount > 0 ? `${timeoutCount} timeout${timeoutCount === 1 ? "" : "s"}` : ((health.status_reasons || [])[0] || currentHeadline());

  bar.innerHTML = `
    <div class="flex h-full min-w-0 w-full items-center justify-between gap-4 px-4">
      <div class="min-w-0 flex items-center gap-3 text-sm">
        <p class="text-[11px] uppercase tracking-[0.34em]" style="color:#FF5252;">BOSS</p>
        <span class="text-neutral-800">|</span>
        <p class="text-sm font-medium text-neutral-100">${escapeHtml(projectLabel)}</p>
      </div>
      <div class="min-w-0 flex items-center gap-3 text-sm">
        <p class="${healthStatus === "degraded" || healthStatus === "failed" ? "text-rose-300" : "text-neutral-300"}">${escapeHtml(`${statusIcon} ${statusText}`)}</p>
        <span class="text-neutral-800">|</span>
        <p class="truncate text-neutral-500">${escapeHtml(alertText)}</p>
      </div>
    </div>
  `;
}

function renderShellThinkingState() {
  const centerRail = qs("centerRail");
  if (!centerRail) return;
  const isThinking = Boolean(state.streamingTurn?.pending && !state.streamingTurn?.cancelling);
  centerRail.classList.toggle("shell-thinking", isThinking);
}

function renderPulseBar() {
  const pulse = qs("pulseBar");
  if (!pulse) return;
  const workspace = state.workspace || {};
  const health = state.health || {};
  const activeCount = (state.activities || []).filter((activity) =>
    ["running", "planning", "reviewing", "thinking", "chat"].includes(String(activity.status || "").toLowerCase()),
  ).length;
  const latestTest = workspace.last_test_results?.summary || workspace.last_test_results?.message || "No test signal yet";
  pulse.innerHTML = `
    <div class="flex min-w-0 items-center gap-3">
      <span class="text-[10px] uppercase tracking-[0.34em]" style="color:#FF5252;">BOSS</span>
      <span class="text-neutral-800">|</span>
      <span class="truncate text-xs text-neutral-300">${escapeHtml(currentHeadline())}</span>
    </div>
    <div class="flex items-center gap-4 text-xs">
      <span class="${statusColor(health.status)}">${escapeHtml(String(health.status || "starting").toUpperCase())}</span>
      <span>${escapeHtml(`${activeCount} agents active`)}</span>
      <span class="max-w-[320px] truncate text-neutral-500">${escapeHtml(truncate(latestTest, 70))}</span>
      <span class="text-neutral-600">${escapeHtml(formatRelativeTime(state.lastRefreshAt))}</span>
    </div>
  `;
}

function renderLeftNavButtons(container, items) {
  container.innerHTML = items
    .map((item) => {
      const active = item.active ? "active" : "";
      return `
        <button
          type="button"
          title="${escapeHtml(item.label)}"
          data-nav-action="${escapeHtml(item.action)}"
          data-nav-value="${escapeHtml(item.value || "")}"
          class="sidebar-row ${active}"
        >
          <span class="sidebar-icon">${escapeHtml(item.icon || "•")}</span>
          <span class="sidebar-copy min-w-0">
            <strong>${escapeHtml(item.label)}</strong>
            ${item.description ? `<span>${escapeHtml(item.description)}</span>` : ""}
          </span>
        </button>
      `;
    })
    .join("");
}

function renderProjectList() {
  const container = qs("projectList");
  if (!container) return;
  const portfolioMap = portfolioProjectMap();
  const active = isWorkspaceProject(state.activeProject) ? "__workspace__" : state.activeProject;
  const projectItems = [
    {
      key: "__workspace__",
      display_name: "Workspace",
      root: state.roots?.primary_root || state.workspace?.workspace_root || "/Users/tj",
      source_root: "workspace",
    },
    ...normalizeProjectCatalog(),
  ];

  container.innerHTML = projectItems
    .map((project) => {
      const projectKey = project.key;
      const isActive = active === projectKey;
      const portfolio = portfolioMap[String(project.display_name || project.key || "").toLowerCase()] || {};
      return `
        <button
          type="button"
          title="${escapeHtml(project.display_name)}"
          data-project-key="${escapeHtml(projectKey)}"
          class="sidebar-row ${isActive ? "active" : ""}"
        >
          <span class="sidebar-icon">${isActive ? "•" : ""}</span>
          <span class="sidebar-copy min-w-0">
            <strong>${escapeHtml(project.display_name)}</strong>
            ${(portfolio.focus || portfolio.next_priority) ? `<span>${escapeHtml(portfolio.focus || portfolio.next_priority || "")}</span>` : ""}
          </span>
        </button>
      `;
    })
    .join("");

  container.querySelectorAll("button[data-project-key]").forEach((button) => {
    button.addEventListener("click", async () => {
      const projectKey = button.dataset.projectKey;
      if (!projectKey) return;
      await setActiveProject(projectKey);
    });
  });
}

function renderChatSessionList() {
  const container = qs("chatSessionList");
  if (!container) return;
  const sessions = state.chatSessions || [];
  container.innerHTML = sessions.length
    ? sessions
        .map(
          (session) => `
        <button
          type="button"
          title="${escapeHtml(session.name)}"
          data-chat-session-id="${escapeHtml(session.id)}"
          class="sidebar-row ${session.id === state.activeChatSessionId ? "active" : ""}"
        >
          <span class="sidebar-icon">${session.pinned ? "★" : "◦"}</span>
          <span class="sidebar-copy min-w-0">
            <strong>${escapeHtml(session.name || "Thread")}</strong>
            <span>${escapeHtml(formatRelativeTime(session.updated_at))}</span>
          </span>
        </button>
      `,
        )
        .join("")
    : '<div class="px-2 py-1 text-sm text-slate-500">Start a chat to create your first thread.</div>';

  container.querySelectorAll("button[data-chat-session-id]").forEach((button) => {
    button.onclick = () => {
      state.activeChatSessionId = button.dataset.chatSessionId || null;
      const { store, bucket } = currentSessionStore();
      bucket.active_id = state.activeChatSessionId;
      saveChatSessionStore(store);
      renderChatSessionList();
      renderChat();
    };
  });
}

function renderLeftNav() {
  const sidebarNav = qs("sidebarNav");
  if (!sidebarNav) return;

  renderLeftNavButtons(sidebarNav, [
    {
      icon: "⌕",
      label: "Files",
      description: "Search this workspace",
      action: "prefill",
      value: "search my mac for ",
    },
    {
      icon: "▦",
      label: "Runs",
      description: "",
      action: "prefill",
      value: "show me the recent runs and what matters",
    },
    {
      icon: "⚖",
      label: "Benchmarks",
      description: "",
      action: "prefill",
      value: "run the next reliability benchmark",
    },
    {
      icon: "◨",
      label: "Artifacts",
      description: "",
      action: "prefill",
      value: "show me the latest artifacts and what they say",
    },
    {
      icon: "◫",
      label: "Project Map",
      description: "",
      action: "prefill",
      value: "map the current project and give me the architecture summary",
    },
  ]);

  document.querySelectorAll("button[data-nav-action]").forEach((button) => {
    button.onclick = async () => {
      const action = button.dataset.navAction;
      const value = button.dataset.navValue || "";
      if (action === "view") {
        setCenterView(value);
        return;
      }
      if (action === "prefill") {
        seedPrompt(value);
        return;
      }
    };
  });
}

function renderBrainBanner() {
  const panel = qs("brainBanner");
  if (!panel) return;
  const brain = currentBrain();
  const activeSession = state.chatSessions.find((session) => session.id === state.activeChatSessionId);
  const projectLabel = activeScopeLabel();
  const mission = brain.mission || "Engineering command center";
  const focus = suggestedFocus();
  const nextPriority = displayNextPriority();
  panel.innerHTML = `
    <p class="brain-kicker">${escapeHtml(projectLabel)}</p>
    <p class="brain-title">${escapeHtml(activeSession?.name || mission)}</p>
    <p class="brain-subtitle">Focus ${escapeHtml(focus)} · Next ${escapeHtml(nextPriority)}</p>
  `;
}

function renderSuggestions() {
  const panel = qs("suggestionsPanel");
  if (!panel) return;
  const recommendations = state.recommendations || [];
  if (!recommendations.length) {
    panel.innerHTML = `
      <button type="button" data-suggestion="what's the highest leverage thing for us today?" class="suggestion-chip">
        Ask what matters most right now
      </button>
    `;
  } else {
    panel.innerHTML = recommendations
      .slice(0, 6)
      .map(
        (item) => `
          <button
            type="button"
            data-suggestion="${escapeHtml(item.title || "")}"
            class="suggestion-chip"
          >
            ${escapeHtml(truncate(item.title || "Next move", 42))}
          </button>
        `,
      )
      .join("");
  }
  panel.querySelectorAll("button[data-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      seedPrompt(button.dataset.suggestion || "");
    });
  });
}

function renderExecutionStrip() {
  const panel = qs("executionStrip");
  if (!panel) return;
  const tasks = (state.tasks || []).filter((task) =>
    ["running", "planning", "retrying", "queued", "waiting"].includes(String(task.status || "").toLowerCase()),
  );
  const activities = (state.activities || []).filter((activity) =>
    ["running", "planning", "reviewing", "thinking", "chat", "waiting"].includes(String(activity.status || "").toLowerCase()),
  );
  const nodes = tasks.length
    ? tasks.slice(0, 5).map((task) => ({
        label: task.agent_type || "Agent",
        detail: task.title || task.status || "working",
        status: task.status || "running",
      }))
    : activities.slice(0, 5).map((activity) => ({
        label: activity.agent || "BOSS",
        detail: activity.message || activity.status || "working",
        status: activity.status || "running",
      }));

  if (!nodes.length && !state.streamingTurn?.pending) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }

  if (!nodes.length && state.streamingTurn?.pending) {
    nodes.push({
      label: "BOSS",
      detail: state.streamingTurn.cancelling ? "stopping generation" : "thinking",
      status: state.streamingTurn.cancelling ? "waiting" : "thinking",
    });
  }

  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="execution-strip-inner">
      ${nodes
        .map((node) => {
          const status = String(node.status || "").toLowerCase();
          const toneClass = ["running", "planning", "reviewing", "thinking", "chat"].includes(status) ? "active" : status === "waiting" || status === "queued" ? "waiting" : "";
          return `
            <div class="execution-chip ${toneClass}">
              <span class="dot"></span>
              <strong>${escapeHtml(node.label)}</strong>
              <span>${escapeHtml(truncate(node.detail, 54))}</span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderModeBadge() {
  const badge = qs("modeBadge");
  if (!badge) return;
  const turns = currentTurns();
  const lastTurn = turns[turns.length - 1];
  const mode = lastTurn?.metadata?.mode || "chat";
  badge.textContent = `Mode: ${String(mode).toUpperCase()}`;
  badge.className = `rounded-full border px-3 py-1 text-xs uppercase tracking-[0.25em] ${healthPillClasses(mode)}`;
}

function diffLineClass(line) {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "";
}

function activeDiffFile() {
  const files = state.diffViewer.files || [];
  if (!files.length) return null;
  return files.find((file) => file.path === state.diffViewer.activePath) || files[0];
}

function renderDiffViewer() {
  const modal = qs("diffViewerModal");
  const fileList = qs("diffViewerFiles");
  const content = qs("diffViewerContent");
  const title = qs("diffViewerTitle");
  if (!modal || !fileList || !content || !title) return;

  modal.classList.toggle("hidden", !state.diffViewer.open);
  if (!state.diffViewer.open) {
    return;
  }

  if (state.diffViewer.loading) {
    title.textContent = "Loading diff…";
    fileList.innerHTML = "";
    content.innerHTML = '<div class="diff-empty">Loading diff bundle…</div>';
    return;
  }

  if (state.diffViewer.error) {
    title.textContent = "Diff unavailable";
    fileList.innerHTML = "";
    content.innerHTML = `<div class="diff-empty">${escapeHtml(state.diffViewer.error)}</div>`;
    return;
  }

  const files = state.diffViewer.files || [];
  const selected = activeDiffFile();
  title.textContent = selected?.path || `Run ${state.diffViewer.runId || ""} diff`;

  fileList.innerHTML = files.length
    ? files
        .map(
          (file) => `
            <button
              type="button"
              data-diff-select="${escapeHtml(file.path)}"
              class="diff-file-row ${file.path === state.diffViewer.activePath ? "active" : ""}"
            >
              <span class="diff-file-name">${escapeHtml(file.path)}</span>
              <span class="diff-file-status">${escapeHtml(file.status || "modified")}</span>
            </button>
          `,
        )
        .join("")
    : '<div class="diff-empty">No changed files recorded.</div>';

  if (!selected) {
    content.innerHTML = '<div class="diff-empty">Pick a file to inspect its diff.</div>';
  } else if (!selected.diff || !String(selected.diff).trim()) {
    content.innerHTML = `<div class="diff-empty">No diff text recorded for <code>${escapeHtml(selected.path)}</code>.</div>`;
  } else {
    content.innerHTML = `
      <div class="diff-code">
        ${String(selected.diff)
          .split("\n")
          .map(
            (line) => `
              <div class="diff-line ${diffLineClass(line)}"><span>${escapeHtml(line || " ")}</span></div>
            `,
          )
          .join("")}
      </div>
    `;
  }

  fileList.querySelectorAll("button[data-diff-select]").forEach((button) => {
    button.addEventListener("click", () => {
      state.diffViewer.activePath = button.dataset.diffSelect || null;
      renderDiffViewer();
    });
  });
}

async function openDiffViewer(runId, kind = "build", preferredPath = "") {
  if (!runId) return;
  state.diffViewer = {
    open: true,
    loading: true,
    runId,
    kind,
    files: [],
    activePath: preferredPath || null,
    error: null,
  };
  renderDiffViewer();
  try {
    const payload = await fetchJson(`/runs/${encodeURIComponent(runId)}/diff?kind=${encodeURIComponent(kind)}`);
    const files = payload.files || [];
    state.diffViewer = {
      open: true,
      loading: false,
      runId,
      kind,
      files,
      activePath: preferredPath && files.some((file) => file.path === preferredPath) ? preferredPath : (files[0]?.path || null),
      error: null,
    };
  } catch (error) {
    state.diffViewer = {
      open: true,
      loading: false,
      runId,
      kind,
      files: [],
      activePath: null,
      error: error?.message || "Unable to load diff.",
    };
  }
  renderDiffViewer();
}

function closeDiffViewer() {
  state.diffViewer = {
    open: false,
    loading: false,
    runId: null,
    kind: "build",
    files: [],
    activePath: null,
    error: null,
  };
  renderDiffViewer();
}

async function updateCommitGate(runId, action) {
  if (!runId) return;
  const endpoint = action === "approve" ? "commit" : "commit/reject";
  const body = {
    kind: "build",
    project_name: state.activeProject,
  };
  if (action === "reject") {
    body.reason = "Commit rejected. Revise the changes before trying again.";
  }
  await fetchJson(`/runs/${encodeURIComponent(runId)}/${endpoint}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  await refreshLoopStatus();
  await refreshAll();
}

function chatActionButtons(actions, turnId) {
  return (actions || [])
    .map(
      (action, index) => `
        <button
          type="button"
          data-chat-action="true"
          data-turn-id="${escapeHtml(turnId || "pending")}"
          data-index="${index}"
          data-intent="${escapeHtml(action.intent || "")}"
          data-message="${encodeURIComponent(action.message || "")}"
          data-execute="${action.execute ? "true" : "false"}"
          data-auto-approve="${action.auto_approve ? "true" : "false"}"
          class="action-pill"
        >
          ${escapeHtml(action.label || "Run")}
        </button>
      `,
    )
    .join("");
}

function renderChat() {
  const panel = qs("chatMessages");
  if (!panel) return;
  const turns = currentTurns();
  const totalTurns = turns.length;
  const visibleTurns = totalTurns > 120 ? turns.slice(-120) : turns;
  const activeSession = state.chatSessions.find((session) => session.id === state.activeChatSessionId);
  if (!visibleTurns.length) {
    const projectLabel = activeScopeLabel();
    const prompts = emptyStatePrompts();
    panel.innerHTML = `
      <section class="chat-empty message-fade-in">
        <p class="chat-label">${escapeHtml(projectLabel)}</p>
        <h2 class="chat-empty-title">${escapeHtml(activeSession?.name || "What are we building?")}</h2>
        <p class="chat-empty-copy">
          Ask for strategy, research, code, or just tell me what to do. I’ll keep the conversation moving and execute when it makes sense.
        </p>
        <div class="chat-empty-actions">
          ${prompts
            .map(
              (prompt) => `
                <button type="button" data-suggestion="${escapeHtml(prompt)}" class="suggestion-chip">
                  ${escapeHtml(prompt)}
                </button>
              `,
            )
            .join("")}
        </div>
      </section>
    `;
    panel.querySelectorAll("button[data-suggestion]").forEach((button) => {
      button.addEventListener("click", () => {
        seedPrompt(button.dataset.suggestion || "");
      });
    });
    renderModeBadge();
    renderShellThinkingState();
    return;
  }

  panel.innerHTML = visibleTurns
    .map((turn) => {
      const actions = chatActionButtons(turn.metadata?.actions || [], turn.id);
      const result = turn.metadata?.result || {};
      const artifactPath = result.artifact_path || result.path || "";
      const summary = result.summary || result.message || "";
      const isPending = Boolean(turn.pending);
      const isReplying = isPending && Boolean((turn.response || "").trim());
      const thinkingLabel = turn.cancelling ? "Stopping" : isReplying ? "Replying" : "Thinking";
      return `
        <article class="chat-turn message-fade-in">
          <p class="chat-label text-emerald-200">TJ</p>
          <div class="chat-body chat-user">${formatMultiline(turn.message)}</div>
        </article>
        <article class="chat-turn message-fade-in">
          <p class="chat-label">BOSS</p>
          ${
            isPending
              ? `
                <div class="thinking-shell">
                  <div class="thinking-status">
                    <span>${escapeHtml(thinkingLabel)}</span>
                    <span class="thinking-dots"><span></span><span></span><span></span></span>
                  </div>
                  <div class="chat-body">${formatMultiline(turn.response || "Thinking…")}</div>
                </div>
              `
              : `<div class="chat-body">${formatMultiline(turn.response || "")}</div>`
          }
          ${turn.pending ? renderLoopBlock(state.loopStatus, { pending: true }) : ""}
          ${summary ? `<div class="result-block">${escapeHtml(truncate(summary, 220))}</div>` : ""}
          ${artifactPath ? `<p class="chat-meta">Artifact: ${escapeHtml(artifactPath)}</p>` : ""}
          ${actions ? `<div class="action-pills">${actions}</div>` : ""}
        </article>
      `;
    })
    .join("");

  if (!state.streamingTurn?.pending && state.loopStatus && ["running", "planning", "retrying", "queued", "completed", "failed", "stopped", "aborted"].includes(String(state.loopStatus.status || "").toLowerCase())) {
    panel.innerHTML += `
      <article class="chat-turn message-fade-in">
        <p class="chat-label">AUTONOMOUS BUILD</p>
        ${renderLoopBlock(state.loopStatus, { pending: false })}
      </article>
    `;
  }

  if (totalTurns > 120) {
    panel.innerHTML = `
      <div class="mb-4 rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-3 text-xs text-slate-500">
        Showing the latest 120 messages for speed. Older chat history is still saved in this thread.
      </div>
    ` + panel.innerHTML;
  }

  panel.querySelectorAll("button[data-chat-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const message = decodeURIComponent(button.dataset.message || "");
      await sendChatStream(message, {
        execute: button.dataset.execute === "true",
        autoApprove: button.dataset.autoApprove === "true",
        intent: button.dataset.intent || "",
      });
    });
  });
  panel.querySelectorAll("button[data-run-diff]").forEach((button) => {
    button.addEventListener("click", async () => {
      await openDiffViewer(
        button.dataset.runDiff || "",
        button.dataset.kind || "build",
        button.dataset.file || "",
      );
    });
  });
  panel.querySelectorAll("button[data-commit-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.commitAction || "";
      if (action === "reject") {
        const confirmed = window.confirm("Reject this commit and keep the changes uncommitted?");
        if (!confirmed) return;
      }
      await updateCommitGate(button.dataset.runId || "", action);
    });
  });

  panel.scrollTop = panel.scrollHeight;
  renderModeBadge();
  renderShellThinkingState();
}

function agentRowStatus(agent, task) {
  const activity = (state.activities || []).find((item) => {
    if (String(item.agent || "").toLowerCase() !== agent) return false;
    if (task?.id != null && item.task_id != null) {
      return Number(item.task_id) === Number(task.id);
    }
    return true;
  });
  if (activity) {
    return {
      status: String(activity.status || "running").toLowerCase(),
      message: activity.message || "",
    };
  }
  const taskStatus = String(task?.status || "").toLowerCase();
  if (agent === "architect") {
    if (taskStatus === "completed") return { status: "completed", message: "planned the build" };
    if (task?.plan?.steps?.length) return { status: "completed", message: "planned the execution" };
  }
  if (agent === "engineer" && taskStatus === "completed") {
    return { status: "completed", message: "implemented the requested steps" };
  }
  if ((agent === "test" || agent === "auditor") && ["running", "planning", "retrying", "queued"].includes(taskStatus)) {
    return { status: "waiting", message: "waiting" };
  }
  return { status: "waiting", message: "waiting" };
}

function loopStatusGlyph(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "passed", "healthy", "stable", "idle"].includes(value)) return "✓";
  if (["failed", "error", "aborted", "timeout"].includes(value)) return "✕";
  if (["running", "planning", "reviewing", "thinking", "chat"].includes(value)) return "→";
  return "•";
}

function renderLoopBlock(task, { pending = false } = {}) {
  if (!task) return "";
  const totalSteps = Number(task.total_steps || (task.steps || []).length || 0);
  const completedSteps = (task.steps || []).filter((step) => String(step.status || "").toLowerCase() === "completed").length;
  const currentStep = (task.steps || []).find((step) => String(step.status || "").toLowerCase() === "running")
    || (task.steps || []).find((step) => !["completed", "failed", "stopped", "aborted"].includes(String(step.status || "").toLowerCase()));
  const changedFiles = (task.files_changed || []).slice(0, 4);
  const taskId = task.id || task.task_id || "";
  const commitGate = task.metadata?.commit_gate || {};
  const agents = [
    { key: "architect", label: "Architect" },
    { key: "engineer", label: "Engineer" },
    { key: "test", label: "Tester" },
    { key: "auditor", label: "Auditor" },
  ];
  return `
    <div class="result-block">
      <div class="space-y-2">
        <p><strong>Goal</strong><br>${escapeHtml(task.task || task.goal || "Autonomous build")}</p>
        <p><strong>Iteration</strong><br>${escapeHtml(`${completedSteps} / ${totalSteps || "?"}`)}</p>
        <div class="space-y-1">
          ${agents
            .map((agent) => {
              const row = agentRowStatus(agent.key, task);
              const detail = row.message ? ` ${row.message}` : "";
              return `<div>${escapeHtml(`${agent.label} ${loopStatusGlyph(row.status)}${detail ? ` ${detail}` : ""}`)}</div>`;
            })
            .join("")}
        </div>
        ${currentStep ? `<p><strong>Current</strong><br>${escapeHtml(currentStep.title || currentStep.step_title || "Working")}</p>` : ""}
        ${
          changedFiles.length
            ? `<div><strong>Files changed</strong><div class="action-pills mt-2">${changedFiles
                .map((file) =>
                  taskId
                    ? `<button type="button" data-run-diff="${escapeHtml(taskId)}" data-kind="build" data-file="${escapeHtml(file)}" class="action-pill">${escapeHtml(file)}</button>`
                    : `<span class="action-pill">${escapeHtml(file)}</span>`,
                )
                .join("")}</div></div>`
            : ""
        }
        ${
          !pending && taskId && String(commitGate.status || "").toLowerCase() === "pending"
            ? `
              <div class="mt-2">
                <p><strong>Ready to commit</strong><br>${escapeHtml(commitGate.message || "Review the changes before committing.")}</p>
                <div class="action-pills mt-2">
                  <button type="button" data-commit-action="approve" data-run-id="${escapeHtml(taskId)}" class="action-pill">Approve Commit</button>
                  <button type="button" data-commit-action="reject" data-run-id="${escapeHtml(taskId)}" class="action-pill">Reject</button>
                </div>
              </div>
            `
            : ""
        }
        ${
          !pending && taskId && String(commitGate.status || "").toLowerCase() === "committed"
            ? `<p><strong>Commit</strong><br>${escapeHtml(commitGate.commit || commitGate.message || "Committed")}</p>`
            : ""
        }
        ${
          !pending && taskId && ["rejected", "failed", "skipped"].includes(String(commitGate.status || "").toLowerCase())
            ? `<p><strong>Commit gate</strong><br>${escapeHtml(commitGate.message || String(commitGate.status || "updated"))}</p>`
            : ""
        }
        ${pending ? `<p class="chat-meta">Live loop updates will continue here.</p>` : ""}
      </div>
    </div>
  `;
}

function renderComposerState() {
  const sendButton = qs("sendButton");
  const stopButton = qs("stopStreamButton");
  const input = qs("chatInput");
  const streaming = Boolean(state.streamingTurn?.pending);
  if (sendButton) {
    sendButton.classList.toggle("hidden", streaming);
  }
  if (stopButton) {
    stopButton.classList.toggle("hidden", !streaming);
    stopButton.textContent = state.streamingTurn?.cancelling ? "Stopping…" : "Stop";
    stopButton.disabled = Boolean(state.streamingTurn?.cancelling);
  }
  if (input) {
    input.disabled = false;
  }
  renderShellThinkingState();
}

function renderRunsWorkspace() {
  const panel = qs("runsWorkspace");
  if (!panel) return;
  const selected = state.selectedRunDetails;
  const runCards = (state.runLedger || [])
    .slice(0, 10)
    .map(
      (item) => `
        <button
          type="button"
          data-run-ledger="true"
          data-id="${escapeHtml(item.identifier)}"
          data-kind="${escapeHtml(item.kind === "build_task" ? "build" : item.kind === "evaluation_run" ? "evaluation" : "experiment")}"
          class="w-full rounded-3xl border border-slate-800 bg-slate-950/45 p-4 text-left transition hover:border-slate-600"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="text-xs uppercase tracking-[0.25em] text-slate-500">${escapeHtml(item.project_name || "workspace")}</p>
              <p class="mt-1 text-sm font-semibold text-slate-100">${escapeHtml(item.title || "Untitled run")}</p>
              <p class="mt-2 text-xs text-slate-500">${escapeHtml(formatTimestamp(item.timestamp))}</p>
            </div>
            <div class="text-right">
              <p class="text-xs uppercase tracking-[0.2em] text-slate-500">${escapeHtml(item.kind || "run")}</p>
              <p class="mt-1 text-sm ${statusColor(item.status)}">${escapeHtml(item.status || "")}</p>
            </div>
          </div>
        </button>
      `,
    )
    .join("");

  const summary = selected?.summary || {};
  const details = selected
    ? `
      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">${escapeHtml(selected.kind || "run")}</p>
        <h3 class="mt-2 text-xl font-semibold">${escapeHtml(summary.task || summary.suite_name || selected.identifier || "Run details")}</h3>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          <div class="rounded-2xl border border-slate-800 bg-slate-900/55 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Status</p>
            <p class="mt-2 text-sm ${statusColor(selected.status)}">${escapeHtml(selected.status || "unknown")}</p>
          </div>
          <div class="rounded-2xl border border-slate-800 bg-slate-900/55 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Graph Nodes</p>
            <p class="mt-2 text-sm text-slate-100">${escapeHtml(summary.graph_nodes != null ? summary.graph_nodes : "n/a")}</p>
          </div>
          <div class="rounded-2xl border border-slate-800 bg-slate-900/55 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Retries</p>
            <p class="mt-2 text-sm text-slate-100">${escapeHtml(summary.retries != null ? summary.retries : "n/a")}</p>
          </div>
          <div class="rounded-2xl border border-slate-800 bg-slate-900/55 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Runtime</p>
            <p class="mt-2 text-sm text-slate-100">${escapeHtml(summary.runtime_seconds != null ? `${summary.runtime_seconds}s` : "n/a")}</p>
          </div>
        </div>
        <div class="mt-4 rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
          <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Artifact</p>
          <p class="mt-2 break-all text-sm text-slate-200">${escapeHtml(selected.artifact_path || "n/a")}</p>
        </div>
      </div>
    `
    : `
      <div class="rounded-3xl border border-dashed border-slate-700 bg-slate-950/40 p-5 text-sm text-slate-500">
        Pick a run to inspect its summary, artifact path, and execution footprint.
      </div>
    `;

  const liveSwarm = `
    <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
      <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Live Swarm</p>
      <div class="mt-4 space-y-3">
        ${state.runs.length
          ? state.runs
              .slice(0, 4)
              .map(
                (run) => `
                  <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
                    <div class="flex items-center justify-between gap-3">
                      <div>
                        <p class="text-sm font-medium text-slate-100">${escapeHtml(run.goal || "Run")}</p>
                        <p class="mt-1 text-xs text-slate-500">${escapeHtml(run.project_name || activeScopeLabel())}</p>
                      </div>
                      <span class="text-xs ${statusColor(run.status)}">${escapeHtml(run.status || "")}</span>
                    </div>
                  </div>
                `,
              )
              .join("")
          : '<div class="text-sm text-slate-500">No live swarm runs right now.</div>'}
      </div>
    </div>
  `;

  panel.innerHTML = `
    <div class="mb-5 flex items-center justify-between gap-3">
      <div>
        <p class="text-[10px] uppercase tracking-[0.28em] text-slate-500">Run Ledger</p>
        <h2 class="mt-1 text-xl font-semibold text-slate-50">Runs</h2>
      </div>
      <span class="rounded-full border border-slate-800 bg-slate-950/50 px-3 py-1 text-xs text-slate-300">${escapeHtml(String((state.runLedger || []).length))} recent</span>
    </div>
    <div class="grid gap-4 xl:grid-cols-[0.92fr,1.08fr]">
      <div class="space-y-4">${runCards || '<div class="rounded-3xl border border-dashed border-slate-700 p-5 text-sm text-slate-500">No runs recorded yet.</div>'}</div>
      <div class="space-y-4">${details}${liveSwarm}</div>
    </div>
  `;

  panel.querySelectorAll("button[data-run-ledger]").forEach((button) => {
    button.addEventListener("click", async () => {
      await loadRunDetails(button.dataset.id, button.dataset.kind);
    });
  });
}

function trendBars(entries, labelKey) {
  return entries
    .map((entry) => {
      const pct = Math.max(0, Math.min(100, Math.round((entry.success_rate || 0) * 100)));
      return `
        <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
          <div class="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.25em] text-slate-500">
            <span>${escapeHtml(labelKey(entry))}</span>
            <span>${pct}%</span>
          </div>
          <div class="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-800">
            <div class="h-full rounded-full bg-emerald-400/90" style="width:${pct}%"></div>
          </div>
          <p class="mt-3 text-xs text-slate-500">${entry.passed || 0}/${entry.attempted || entry.total || 0} successful</p>
        </div>
      `;
    })
    .join("");
}

function renderMetricsWorkspace() {
  const panel = qs("metricsWorkspace");
  if (!panel) return;
  const health = state.health || {};
  const metrics = state.metrics || {};
  const trends = metrics.trends || {};
  const roadmap = currentRoadmap();
  const risks = state.risks || [];

  panel.innerHTML = `
    <div class="mb-5 flex items-center justify-between gap-3">
      <div>
        <p class="text-[10px] uppercase tracking-[0.28em] text-slate-500">System</p>
        <h2 class="mt-1 text-xl font-semibold text-slate-50">Metrics</h2>
      </div>
      <span class="rounded-full border border-slate-800 bg-slate-950/50 px-3 py-1 text-xs text-slate-300">${escapeHtml(String((state.metrics?.task_runs_recorded || 0)))} runs recorded</span>
    </div>
    <div class="grid gap-4 xl:grid-cols-[1fr,1fr]">
    <div class="space-y-4">
      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">System Health</p>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          ${buildMetricCard("Status", String(health.status || "unknown").toUpperCase(), statusColor(health.status))}
          ${buildMetricCard("Success Rate", formatPercent(health.autonomous_success_rate), "text-slate-100")}
          ${buildMetricCard("Recent Failures", String(health.recent_eval_failures || 0), "text-slate-100")}
          ${buildMetricCard("Artifacts", String(health.artifact_store_size || 0), "text-slate-100")}
        </div>
        <ul class="mt-4 space-y-2 text-sm text-slate-300">
          ${(health.status_reasons || []).map((item) => `<li>• ${escapeHtml(item)}</li>`).join("") || "<li>• No health notes yet.</li>"}
        </ul>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Success Trends</p>
        <div class="mt-4 grid gap-3">
          ${trendBars(trends.tasks || [], (entry) => `Last ${entry.window} Tasks`) || '<div class="text-sm text-slate-500">No task trend data yet.</div>'}
        </div>
      </div>
    </div>

    <div class="space-y-4">
      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Runtime Metrics</p>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          ${buildMetricCard("Runs Recorded", String(metrics.task_runs_recorded || 0))}
          ${buildMetricCard("Benchmarks", String(metrics.benchmarks_executed || 0))}
          ${buildMetricCard("Experiments", String(metrics.experiments_executed || 0))}
          ${buildMetricCard("Avg Graph Nodes", formatNumber(metrics.run_graph?.avg_nodes_per_run, 1))}
        </div>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Strategic Risks</p>
        <div class="mt-4 space-y-3">
          ${risks.length
            ? risks
                .slice(0, 5)
                .map(
                  (risk) => `
                    <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
                      <div class="flex items-center justify-between gap-3">
                        <p class="text-sm font-medium text-slate-100">${escapeHtml(risk.title || "Risk")}</p>
                        <span class="text-xs ${statusColor(risk.severity || "warning")}">${escapeHtml(risk.severity || "MEDIUM")}</span>
                      </div>
                      <p class="mt-2 text-sm text-slate-400">${escapeHtml(risk.reason || "")}</p>
                    </div>
                  `,
                )
                .join("")
            : '<div class="text-sm text-slate-500">No strategic risks recorded right now.</div>'}
        </div>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Roadmap</p>
        <div class="mt-4 grid gap-3">
          <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Completed</p>
            <p class="mt-2 text-sm text-slate-200">${escapeHtml((roadmap.completed || []).join(" • ") || "None yet")}</p>
          </div>
          <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">In Progress</p>
            <p class="mt-2 text-sm text-slate-200">${escapeHtml((roadmap.in_progress || []).join(" • ") || "Nothing active")}</p>
          </div>
          <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
            <p class="text-[10px] uppercase tracking-[0.25em] text-slate-500">Future</p>
            <p class="mt-2 text-sm text-slate-200">${escapeHtml((roadmap.future || []).join(" • ") || "No future roadmap yet")}</p>
          </div>
        </div>
      </div>
    </div>
    </div>
  `;
}

function renderOperationsWorkspace() {
  const panel = qs("operationsWorkspace");
  if (!panel) return;
  const portfolio = state.portfolio || {};
  const roots = state.roots || {};
  const permissions = state.permissions || {};
  const mcp = state.mcp || {};
  const memory = state.memory || {};

  panel.innerHTML = `
    <div class="mb-5 flex items-center justify-between gap-3">
      <div>
        <p class="text-[10px] uppercase tracking-[0.28em] text-slate-500">Operations</p>
        <h2 class="mt-1 text-xl font-semibold text-slate-50">Command Center</h2>
      </div>
      <span class="rounded-full border border-slate-800 bg-slate-950/50 px-3 py-1 text-xs text-slate-300">${escapeHtml(`${portfolio.project_count || 0} visible projects`)}</span>
    </div>
    <div class="grid gap-4 xl:grid-cols-[1fr,1fr]">
    <div class="space-y-4">
      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Portfolio</p>
        <p class="mt-2 text-sm text-slate-300">${escapeHtml(`${portfolio.project_count || 0} visible projects`)}</p>
        <div class="mt-4 space-y-3">
          ${(portfolio.projects || [])
            .slice(0, 6)
            .map(
              (project) => `
                <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
                  <p class="text-sm font-medium text-slate-100">${escapeHtml(project.display_name || project.key || "Project")}</p>
                  <p class="mt-1 text-sm text-slate-400">${escapeHtml(project.focus || project.next_priority || project.root || "")}</p>
                </div>
              `,
            )
            .join("") || '<div class="text-sm text-slate-500">No portfolio entries yet.</div>'}
        </div>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Workspace Roots</p>
        <div class="mt-4 space-y-3">
          ${(roots.roots || [])
            .slice(0, 8)
            .map(
              (root) => `
                <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
                  <div class="flex items-center justify-between gap-3">
                    <p class="text-sm font-medium text-slate-100">${escapeHtml(root.name || "root")}</p>
                    <span class="text-xs text-slate-500">${escapeHtml(root.mode || "projects")}</span>
                  </div>
                  <p class="mt-2 break-all text-xs text-slate-400">${escapeHtml(root.path || "")}</p>
                </div>
              `,
            )
            .join("") || '<div class="text-sm text-slate-500">No roots configured.</div>'}
        </div>
      </div>
    </div>

    <div class="space-y-4">
      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Permissions</p>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          ${buildMetricCard("Workspace Writes", String(permissions.workspace_write_mode || "confirm").toUpperCase())}
          ${buildMetricCard("Project Writes", String(permissions.project_write_mode || "confirm").toUpperCase())}
          ${buildMetricCard("Destructive", String(permissions.destructive_mode || "confirm").toUpperCase())}
          ${buildMetricCard("Web Research", permissions.allow_web_research ? "ON" : "OFF", permissions.allow_web_research ? "text-emerald-200" : "text-amber-200")}
        </div>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">MCP Connectors</p>
        <div class="mt-4 space-y-3">
          ${(mcp.connectors || [])
            .slice(0, 6)
            .map(
              (connector) => `
                <div class="rounded-2xl border border-slate-800 bg-slate-900/45 p-4">
                  <div class="flex items-center justify-between gap-3">
                    <p class="text-sm font-medium text-slate-100">${escapeHtml(connector.name || "connector")}</p>
                    <span class="text-xs ${statusColor(connector.healthy ? "stable" : "degraded")}">${connector.healthy ? "healthy" : "offline"}</span>
                  </div>
                  <p class="mt-2 text-sm text-slate-400">${escapeHtml(connector.transport || "")} → ${escapeHtml(connector.target || "")}</p>
                </div>
              `,
            )
            .join("") || '<div class="text-sm text-slate-500">No MCP connectors registered yet.</div>'}
        </div>
      </div>

      <div class="rounded-3xl border border-slate-800 bg-slate-950/45 p-5">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Memory Insights</p>
        <p class="mt-2 text-sm text-slate-300">${escapeHtml(memory.project_profile?.description || "No project memory loaded in this scope.")}</p>
        <p class="mt-3 text-xs text-slate-500">${escapeHtml((memory.solutions || []).length)} reusable solutions • ${escapeHtml((memory.graph_insights || []).length)} graph insights</p>
      </div>
    </div>
    </div>
  `;
}

function renderActivities() {
  const panel = qs("activityPanel");
  if (!panel) return;
  const activities = state.activities || [];
  panel.innerHTML = activities.length
    ? activities
        .slice(0, 8)
        .map(
          (activity) => `
            <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
              <div class="flex items-center justify-between gap-3">
                <div class="flex items-center gap-3">
                  <span class="inline-flex h-8 w-8 items-center justify-center rounded-2xl bg-slate-800 text-xs font-semibold text-slate-200">${escapeHtml((activity.agent || "B").slice(0, 1).toUpperCase())}</span>
                  <div>
                    <p class="text-sm font-medium text-slate-100">${escapeHtml(activity.agent || "BOSS")}</p>
                    <p class="text-xs uppercase tracking-[0.2em] text-slate-500">${escapeHtml(activity.project_name || activeScopeLabel())}</p>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <span class="h-2.5 w-2.5 rounded-full ${dotClasses(activity.status)} ${["running", "planning", "thinking", "reviewing"].includes(String(activity.status || "").toLowerCase()) ? "animate-pulse-soft" : ""}"></span>
                  <span class="text-xs ${statusColor(activity.status)}">${escapeHtml(activity.status || "idle")}</span>
                </div>
              </div>
              <p class="mt-3 text-sm text-slate-300">${escapeHtml(activity.message || "Standing by")}</p>
              <p class="mt-2 text-xs text-slate-500">${escapeHtml(formatTimestamp(activity.updated_at))}</p>
            </div>
          `,
        )
        .join("")
    : '<div class="rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">No live agent activity yet.</div>';
}

function renderRunGraph() {
  const panel = qs("runGraphPanel");
  if (!panel) return;
  const tasks = state.tasks || [];
  const activities = state.activities || [];
  let html = "";

  if (tasks.length) {
    html = `
      <div class="space-y-3">
        ${tasks
          .slice(0, 6)
          .map(
            (task, index) => `
              <button type="button" class="relative block w-full rounded-2xl border border-slate-800 bg-slate-950/45 p-4 text-left transition hover:border-slate-600">
                ${index < tasks.length - 1 ? '<span class="absolute left-7 top-full h-4 w-px bg-slate-800"></span>' : ""}
                <div class="flex items-center gap-3">
                  <span class="h-3 w-3 rounded-full ${dotClasses(task.status)}"></span>
                  <div>
                    <p class="text-sm font-medium text-slate-100">${escapeHtml(task.title || task.agent_type || "Step")}</p>
                    <p class="mt-1 text-xs uppercase tracking-[0.2em] text-slate-500">${escapeHtml(task.agent_type || "agent")}</p>
                  </div>
                </div>
                <p class="mt-3 text-sm text-slate-400">${escapeHtml(task.status || "")}</p>
              </button>
            `,
          )
          .join("")}
      </div>
    `;
  } else if (activities.length) {
    html = `
      <div class="space-y-3">
        ${activities
          .slice(0, 5)
          .map(
            (activity, index) => `
              <div class="relative rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
                ${index < activities.length - 1 ? '<span class="absolute left-7 top-full h-4 w-px bg-slate-800"></span>' : ""}
                <div class="flex items-center gap-3">
                  <span class="h-3 w-3 rounded-full ${dotClasses(activity.status)}"></span>
                  <p class="text-sm font-medium text-slate-100">${escapeHtml(activity.agent || "agent")}</p>
                </div>
                <p class="mt-3 text-sm text-slate-400">${escapeHtml(activity.message || "")}</p>
              </div>
            `,
          )
          .join("")}
      </div>
    `;
  } else if (state.selectedRunDetails?.summary) {
    const summary = state.selectedRunDetails.summary;
    html = `
      <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
        <p class="text-sm font-medium text-slate-100">${escapeHtml(summary.task || summary.suite_name || "Run summary")}</p>
        <p class="mt-2 text-sm text-slate-400">Graph nodes: ${escapeHtml(summary.graph_nodes != null ? summary.graph_nodes : "n/a")}</p>
        <p class="mt-1 text-sm text-slate-400">Retries: ${escapeHtml(summary.retries != null ? summary.retries : "n/a")}</p>
      </div>
    `;
  } else {
    html = '<div class="rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">No live run graph yet.</div>';
  }

  panel.innerHTML = html;
}

function renderWorkspacePanel() {
  const panel = qs("workspacePanel");
  if (!panel) return;
  const workspace = state.workspace || {};
  const recentEdits = workspace.recent_edits || [];
  const openFiles = workspace.open_files || [];
  panel.innerHTML = `
    <div class="space-y-4">
      <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Open Files</p>
        <ul class="mt-3 space-y-2 text-sm text-slate-200">
          ${openFiles.slice(0, 5).map((file) => `<li>${escapeHtml(file)}</li>`).join("") || "<li class='text-slate-500'>No files open yet.</li>"}
        </ul>
      </div>
      <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Recent Edits</p>
        <ul class="mt-3 space-y-3 text-sm text-slate-200">
          ${recentEdits
            .slice(0, 4)
            .map(
              (edit) => `
                <li>
                  <span class="font-medium">${escapeHtml(edit.file || "file")}</span>
                  <span class="mt-1 block text-xs text-slate-500">${escapeHtml(edit.summary || edit.type || "updated")}</span>
                </li>
              `,
            )
            .join("") || "<li class='text-slate-500'>No recent edits recorded.</li>"}
        </ul>
      </div>
      <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Last Command</p>
        <p class="mt-3 text-sm text-slate-200">${escapeHtml(workspace.last_terminal_command || "None")}</p>
        <p class="mt-2 text-xs text-slate-500">${escapeHtml(workspace.last_test_results?.failure_summary || workspace.last_test_results?.message || "No failing test recorded")}</p>
      </div>
      <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
        <p class="text-xs uppercase tracking-[0.25em] text-slate-500">Git</p>
        <p class="mt-3 text-sm text-slate-200">${escapeHtml(workspace.last_git_status?.summary || "No git state recorded")}</p>
        <p class="mt-2 text-xs text-slate-500">${escapeHtml(truncate(workspace.last_git_diff || "No diff recorded", 180))}</p>
      </div>
    </div>
  `;
}

function renderTimelineAndLogs() {
  const timelinePanel = qs("timelinePanel");
  const logsPanel = qs("logsPanel");
  if (timelinePanel) {
    timelinePanel.innerHTML = (state.timeline || []).length
      ? state.timeline
          .slice(0, 8)
          .map(
            (event) => `
              <div class="rounded-2xl border border-slate-800 bg-slate-950/45 p-4">
                <div class="flex items-center justify-between gap-3">
                  <p class="text-sm font-medium text-slate-100">${escapeHtml(event.title || "Event")}</p>
                  <span class="text-xs ${statusColor(event.status)}">${escapeHtml(event.status || "")}</span>
                </div>
                <p class="mt-2 text-sm text-slate-400">${escapeHtml(event.message || "")}</p>
                <p class="mt-2 text-xs text-slate-500">${escapeHtml(formatTimestamp(event.timestamp))}</p>
              </div>
            `,
          )
          .join("")
      : '<div class="rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">No timeline yet.</div>';
  }
  if (logsPanel) {
    logsPanel.textContent = (state.logs || [])
      .slice(-80)
      .map((entry) => `[${entry.timestamp || ""}] ${entry.level || "info"} ${entry.agent || "boss"} ${entry.message || ""}`)
      .join("\n");
    logsPanel.scrollTop = logsPanel.scrollHeight;
  }
}

function renderRightRail() {
  renderActivities();
  renderRunGraph();
  renderWorkspacePanel();
  renderTimelineAndLogs();
}

function renderCommandPalette() {
  const palette = qs("commandPalette");
  const results = qs("commandPaletteResults");
  const input = qs("commandPaletteInput");
  if (!palette || !results || !input) return;
  palette.classList.toggle("hidden", !state.commandPaletteOpen);
  if (!state.commandPaletteOpen) {
    return;
  }

  const commands = [
    { type: "command", label: "Open chat", hint: "⌘1", icon: "⌘", action: () => setCenterView("chat") },
    { type: "command", label: "Refresh the command center", hint: "Fetch latest state", icon: "↻", action: () => refreshAll() },
    { type: "command", label: "Focus chat composer", hint: "Start typing", icon: "⌤", action: () => focusChatComposer(true) },
    { type: "command", label: "Plan the next move", hint: "Prefill", icon: "◫", action: () => seedPrompt("plan the next highest leverage move for me") },
    { type: "command", label: "Build current priority", hint: "⌘R", icon: "⚡", action: () => seedPrompt(`build ${suggestedFocus()}`) },
    { type: "command", label: "Run a reliability benchmark", hint: "Benchmark", icon: "⚖", action: () => seedPrompt("run the next reliability benchmark") },
    { type: "command", label: "Show strategic risks", hint: "Risk scan", icon: "⚠", action: () => seedPrompt("show me the top risks right now") },
    ...(tauriMode && chatViewMode
      ? [{ type: "command", label: "Open full command center", hint: "Expand layout", icon: "⇱", action: () => openFullDashboard() }]
      : []),
    ...state.chatSessions.slice(0, 8).map((session) => ({
      type: "chat",
      label: session.name || "Thread",
      hint: `Chat · ${formatRelativeTime(session.updated_at)}`,
      icon: session.pinned ? "★" : "✦",
      action: () => {
        state.activeChatSessionId = session.id;
        const { store, bucket } = currentSessionStore();
        bucket.active_id = session.id;
        saveChatSessionStore(store);
        setCenterView("chat");
        renderChatSessionList();
        renderChat();
      },
    })),
    ...(state.workspace.open_files || []).slice(0, 8).map((file) => ({
      type: "file",
      label: file,
      hint: "Open file",
      icon: "ƒ",
      action: () => seedPrompt(`open ${file}`),
    })),
    ...currentTurns().slice(-8).map((turn, index) => ({
      type: "chat",
      label: truncate(turn.message || `Message ${index + 1}`, 60),
      hint: "Reuse this prompt",
      icon: "💬",
      action: () => seedPrompt(turn.message || ""),
    })),
    ...(state.runLedger || []).slice(0, 8).map((run) => ({
      type: "run",
      label: run.title || run.identifier || "Run",
      hint: `${run.kind || "run"} · ${run.status || "unknown"}`,
      icon: "▣",
      action: () => seedPrompt(`show me the details for run ${run.identifier}`),
    })),
  ];

  const query = input.value.trim().toLowerCase();
  const filtered = commands.filter((command) => {
    if (!query) return true;
    return `${command.label} ${command.hint || ""}`.toLowerCase().includes(query);
  });

  results.innerHTML = filtered
    .map(
      (command, index) => `
        <button type="button" data-command-index="${index}" class="flex w-full items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-3 text-left transition hover:border-emerald-500 hover:bg-emerald-500/10">
          <span class="flex min-w-0 items-center gap-3">
            <span class="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-800 bg-slate-900/70 text-sm text-slate-200">${escapeHtml(command.icon || "•")}</span>
            <span class="min-w-0">
            <span class="block text-sm font-medium text-slate-100">${escapeHtml(command.label)}</span>
            <span class="mt-1 block text-xs text-slate-500">${escapeHtml(command.hint || "Command")}</span>
            </span>
          </span>
          <span class="text-[10px] uppercase tracking-[0.24em] text-slate-600">${escapeHtml(command.type || "command")}</span>
        </button>
      `,
    )
    .join("") || '<div class="rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">No commands match that query.</div>';

  results.querySelectorAll("button[data-command-index]").forEach((button) => {
    button.onclick = async () => {
      const command = filtered[Number(button.dataset.commandIndex)];
      closeCommandPalette();
      await command.action();
    };
  });
}

function openCommandPalette() {
  state.commandPaletteOpen = true;
  renderCommandPalette();
  window.requestAnimationFrame(() => qs("commandPaletteInput")?.focus());
}

function closeCommandPalette() {
  state.commandPaletteOpen = false;
  renderCommandPalette();
}

function seedPrompt(prefix) {
  setCenterView("chat");
  const input = qs("chatInput");
  if (!input) return;
  input.value = prefix;
  autoResizeComposer(input);
  focusChatComposer(true);
}

function openFullDashboard() {
  const url = new URL(window.location.href);
  url.searchParams.delete("view");
  url.searchParams.delete("focus");
  window.location.href = url.toString();
}

function applyTauriChatLayout() {
  if (!(tauriMode && chatViewMode)) {
    return;
  }
  const openDashboardButton = qs("openDashboardButton");
  openDashboardButton?.classList.remove("hidden");
  setCenterView("chat");
}

function applyShellLayout() {
  const layoutGrid = qs("layoutGrid");
  const leftRail = qs("leftRail");
  if (!layoutGrid || !leftRail || (tauriMode && chatViewMode)) {
    return;
  }
  layoutGrid.classList.toggle("left-collapsed", state.leftRailCollapsed);
  leftRail.classList.toggle("collapsed", state.leftRailCollapsed);
}

async function setActiveProject(projectKey) {
  await fetchJson("/projects/active", {
    method: "POST",
    body: JSON.stringify({ project_name: projectKey }),
  });
  state.selectedRun = null;
  state.selectedRunDetails = null;
  await refreshAll();
}

async function loadRunDetails(identifier, kind) {
  if (!identifier || !kind) return;
  state.selectedRun = { identifier, kind };
  state.selectedRunDetails = await fetchJson(`/runs/${encodeURIComponent(identifier)}?kind=${encodeURIComponent(kind)}`);
  renderRunsWorkspace();
  renderRunGraph();
}

async function refreshAll() {
  const responses = await Promise.all([
    fetchJson("/tasks"),
    fetchJson("/agents"),
    fetchJson("/projects"),
    fetchJson("/workspace"),
    fetchJson("/memory"),
    fetchJson("/evolution"),
    fetchJson("/logs"),
    fetchJson("/chat/history"),
    fetchJson("/activity"),
    fetchJson("/timeline"),
    fetchJson("/health"),
    fetchJson("/metrics"),
    fetchJson("/runs?limit=12"),
    fetchJson("/loop/status"),
    fetchJson("/brain"),
    fetchJson("/next?limit=6"),
    fetchJson("/roadmap"),
    fetchJson("/risks?limit=6"),
    fetchJson("/portfolio"),
    fetchJson("/permissions"),
    fetchJson("/mcp"),
    fetchJson("/roots"),
  ]);

  const [
    tasks,
    agents,
    projects,
    workspace,
    memory,
    evolution,
    logs,
    chat,
    activity,
    timeline,
    health,
    metrics,
    runs,
    loopStatus,
    brain,
    nextActions,
    roadmap,
    risks,
    portfolio,
    permissions,
    mcp,
    roots,
  ] = responses;

  state.runs = tasks.runs || [];
  state.tasks = tasks.tasks || [];
  state.agents = agents.agents || [];
  state.projectsStatus = projects.status || {};
  state.projectCatalog = projects.project_catalog || projects.projects || [];
  state.activeProject = projects.active_project || null;
  state.workspace = workspace || {};
  state.memory = memory || {};
  state.evolution = evolution || {};
  state.logs = logs.logs || [];
  state.chatHistory = chat.history || [];
  ensureChatSessionState();
  state.activities = activity.activities || [];
  state.timeline = timeline.events || [];
  state.health = health || {};
  state.metrics = metrics || {};
  state.runLedger = runs.runs || [];
  state.loopStatus = loopStatus.task || null;
  state.brain = brain || {};
  state.recommendations = nextActions.recommendations || [];
  state.roadmap = roadmap || {};
  state.risks = risks.risks || [];
  state.portfolio = portfolio || {};
  state.permissions = permissions || {};
  state.mcp = mcp || {};
  state.roots = roots || {};
  state.lastRefreshAt = new Date().toISOString();

  if (state.selectedRun) {
    const stillExists = state.runLedger.some((item) => String(item.identifier) === String(state.selectedRun.identifier));
    if (!stillExists) {
      state.selectedRun = null;
      state.selectedRunDetails = null;
    }
  }

  renderAll();
}

function renderAll() {
  applyShellLayout();
  renderStatusBar();
  renderProjectList();
  renderChatSessionList();
  renderLeftNav();
  renderBrainBanner();
  renderExecutionStrip();
  renderSuggestions();
  renderChat();
  renderComposerState();
  renderCommandPalette();
  renderModeBadge();
  renderDiffViewer();
  renderShellThinkingState();
  focusChatComposer();
}

async function cancelActiveChatStream() {
  if (!state.streamingTurn?.pending) return;
  state.streamingTurn.cancelling = true;
  renderChat();
  renderComposerState();
  try {
    await fetchJson("/chat/cancel", {
      method: "POST",
      body: JSON.stringify({
        project_name: state.activeProject,
        stream_id: state.activeStreamId || undefined,
      }),
    });
  } catch (error) {
    state.streamingTurn.cancelling = false;
    renderComposerState();
    throw error;
  }
}

async function sendChatStream(message, options = {}) {
  const sessionId = state.activeChatSessionId;
  state.activeStreamId = null;
  state.streamingTurn = {
    id: "pending",
    message,
    response: "",
    intent: options.intent || "conversation",
    metadata: {
      mode: options.execute ? "executing" : "chat",
      actions: [],
      session_id: sessionId,
    },
    pending: true,
    cancelling: false,
  };
  renderChat();
  renderExecutionStrip();
  renderComposerState();

  const payload = {
    message,
    execute: Boolean(options.execute),
    auto_approve: Boolean(options.autoApprove),
    project_name: state.activeProject,
  };
  if (options.intent) {
    payload.intent = options.intent;
  }

  const response = await fetch("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    const body = await response.text();
    state.streamingTurn.response = body || "The chat request failed.";
    state.streamingTurn.pending = false;
    renderChat();
    renderExecutionStrip();
    renderComposerState();
    throw new Error(body || `Request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "meta") {
        state.activeStreamId = event.stream_id || state.activeStreamId;
        state.streamingTurn.intent = event.intent || state.streamingTurn.intent;
        state.streamingTurn.metadata.mode = event.mode || state.streamingTurn.metadata.mode;
        state.streamingTurn.metadata.actions = event.actions || [];
        state.streamingTurn.metadata.stream_id = event.stream_id || state.streamingTurn.metadata.stream_id;
      } else if (event.type === "delta") {
        state.streamingTurn.response += event.delta || "";
      } else if (event.type === "interrupted") {
        state.streamingTurn.pending = false;
        state.streamingTurn.metadata.mode = "interrupted";
      } else if (event.type === "done") {
        finalResponse = event.response || null;
        if (finalResponse) {
          state.streamingTurn = {
            id: finalResponse.id || "pending-final",
            message,
            response: finalResponse.reply || "",
            intent: finalResponse.intent || "conversation",
            metadata: {
              mode: finalResponse.mode || "chat",
              actions: finalResponse.actions || [],
              result: finalResponse.result || null,
              session_id: sessionId,
              stream_id: state.activeStreamId,
            },
            pending: false,
          };
        }
      } else if (event.type === "error") {
        state.streamingTurn.response += `${state.streamingTurn.response ? "\n" : ""}${event.error || "Chat stream failed."}`;
        state.streamingTurn.pending = false;
        state.streamingTurn.metadata.mode = "blocked";
      }
      renderChat();
      renderExecutionStrip();
      renderComposerState();
    }
  }

  if (buffer.trim()) {
    try {
      const event = JSON.parse(buffer.trim());
      if (event.type === "done" && event.response) {
        finalResponse = event.response;
      }
    } catch (_error) {
      // ignore trailing parse failures from partial buffers
    }
  }

  if (finalResponse) {
    const turnId = finalResponse.id || Date.now();
    state.chatHistory.push({
      id: turnId,
      message,
      response: finalResponse.reply || "",
      intent: finalResponse.intent || "conversation",
      metadata: {
        mode: finalResponse.mode || "chat",
        actions: finalResponse.actions || [],
        result: finalResponse.result || null,
        session_id: sessionId,
      },
    });
    assignTurnToActiveSession(turnId);
  }

  state.streamingTurn = null;
  state.activeStreamId = null;
  renderChat();
  renderExecutionStrip();
  renderComposerState();
  await refreshAll();
}

function upsertActivity(activity) {
  const index = state.activities.findIndex((item) => item.agent === activity.agent);
  if (index >= 0) {
    state.activities[index] = activity;
  } else {
    state.activities.push(activity);
  }
}

function addTimelineEvent(event) {
  state.timeline.unshift(event);
  state.timeline = state.timeline.slice(0, 80);
}

async function refreshLoopStatus() {
  try {
    const payload = await fetchJson("/loop/status");
    state.loopStatus = payload.task || null;
    renderChat();
    renderExecutionStrip();
  } catch (error) {
    console.error(error);
  }
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "log" && payload.payload) {
      state.logs.push(payload.payload);
      renderTimelineAndLogs();
      return;
    }
    if (payload.type === "agent_activity" && payload.payload) {
      upsertActivity(payload.payload);
      renderActivities();
      renderRunGraph();
      if (state.streamingTurn?.pending || state.loopStatus) {
        refreshLoopStatus().catch(console.error);
      }
      return;
    }
    if (payload.type === "timeline" && payload.payload) {
      addTimelineEvent(payload.payload);
      renderTimelineAndLogs();
      renderRunGraph();
      if (state.streamingTurn?.pending || state.loopStatus) {
        refreshLoopStatus().catch(console.error);
      }
      return;
    }
    if (payload.type !== "heartbeat") {
      refreshAll().catch(console.error);
    }
  };
  socket.onclose = () => {
    window.setTimeout(connectWebSocket, 1500);
  };
}

function bindStaticEvents() {
  loadUiPrefs();

  qs("refreshButton")?.addEventListener("click", () => {
    refreshAll().catch(console.error);
  });

  qs("leftRailToggle")?.addEventListener("click", () => {
    state.leftRailCollapsed = !state.leftRailCollapsed;
    persistUiPrefs();
    applyShellLayout();
  });

  qs("newChatButton")?.addEventListener("click", createChatSession);
  qs("renameChatButton")?.addEventListener("click", renameActiveChatSession);
  qs("pinChatButton")?.addEventListener("click", togglePinActiveChatSession);
  qs("deleteChatButton")?.addEventListener("click", deleteActiveChatSession);

  qs("chatForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = qs("chatInput");
    const autoApprove = qs("autoApproveToggle")?.checked;
    const message = input?.value.trim();
    if (!message) return;
    input.value = "";
    autoResizeComposer(input);
    await sendChatStream(message, { execute: false, autoApprove });
    focusChatComposer(true);
  });

  qs("stopStreamButton")?.addEventListener("click", async () => {
    try {
      await cancelActiveChatStream();
    } catch (error) {
      console.error(error);
    }
  });

  qs("chatInput")?.addEventListener("input", (event) => {
    autoResizeComposer(event.target);
  });

  qs("chatInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      qs("chatForm")?.requestSubmit();
    }
  });

  qs("commandPaletteButton")?.addEventListener("click", openCommandPalette);
  qs("commandPalette")?.addEventListener("click", (event) => {
    if (event.target === qs("commandPalette")) {
      closeCommandPalette();
    }
  });
  qs("diffViewerModal")?.addEventListener("click", (event) => {
    if (event.target === qs("diffViewerModal")) {
      closeDiffViewer();
    }
  });
  qs("diffViewerClose")?.addEventListener("click", closeDiffViewer);
  qs("commandPaletteInput")?.addEventListener("input", renderCommandPalette);
  qs("openDashboardButton")?.addEventListener("click", openFullDashboard);

  document.addEventListener("keydown", (event) => {
    if (event.metaKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (state.commandPaletteOpen) {
        closeCommandPalette();
      } else {
        openCommandPalette();
      }
      return;
    }
    if (event.key === "Escape" && state.commandPaletteOpen) {
      closeCommandPalette();
      return;
    }
    if (event.key === "Escape" && state.diffViewer.open) {
      closeDiffViewer();
      return;
    }
    if (event.metaKey && !event.shiftKey) {
      const key = event.key.toLowerCase();
      if (key === "1") {
        event.preventDefault();
        setCenterView("chat");
      } else if (key === "2") {
        event.preventDefault();
        seedPrompt("show me the recent runs and what matters");
      } else if (key === "3") {
        event.preventDefault();
        seedPrompt("show me the current health and what needs attention");
      } else if (key === "4") {
        event.preventDefault();
        seedPrompt("show me recent logs and timeline");
      } else if (key === "r") {
        event.preventDefault();
        seedPrompt("build ");
      }
    }
  });
}

bindStaticEvents();
applyTauriChatLayout();
refreshAll().catch(console.error);
connectWebSocket();
window.setInterval(() => refreshAll().catch(console.error), 5000);
