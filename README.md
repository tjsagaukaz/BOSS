# BOSS

Builder Orchestration System for Sagau is a local AI development environment for macOS that orchestrates multiple foundation models across a structured agent loop:

- Architect: planning and system design
- Engineer: code implementation
- Auditor: review and risk detection

The default routing is:

- Architecture and planning -> Claude Opus
- Coding and debugging -> GPT-5.4
- Code review -> Claude Opus

## Features

- Local CLI developer console with an active-project workflow
- Multi-provider model routing via `config/models.yaml`
- Tool-enabled agents that can read files, write code, search a codebase, run sandboxed terminal commands, and create git commits
- Persistent SQLite memory for project summaries, architecture notes, previous outputs, and code summaries
- Project intelligence with incremental indexing, file hashing, semantic search, and project maps
- Persistent AI brain for project memory, reusable solutions, style profiles, and knowledge graphs
- Agent loop: architect -> engineer -> auditor -> fix iteration
- Autonomous development engine with build plans, test runs, audit/fix loops, and task history
- Multi-agent swarm execution with engineer, test, security, and documentation workers
- VS Code integration with file open/jump controls and editor-state tracking
- Extensible plugin system and central tool registry for project-specific tooling
- Rich task dashboard for autonomous build progress plus a web command center
- Action logging to `logs/boss.log`

## Project Layout

```text
BOSS/
├── boss/
│   ├── orchestrator.py
│   ├── router.py
│   ├── agents/
│   ├── context/
│   ├── memory/
│   ├── models/
│   ├── prompts/
│   └── tools/
├── cli/
├── config/
├── data/
├── logs/
├── projects/
├── main.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add API keys to `.env`:

```bash
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
```

4. Optional: install the CLI entrypoint so `boss` is available on your shell `PATH`:

```bash
pip install -e .
```

## Running BOSS

Run the interactive console:

```bash
python main.py start
```

Or, if installed in editable mode:

```bash
boss start
```

Core commands:

```bash
boss project legion
boss index
boss search "billing stripe payments"
boss map
boss benchmark benchmarks/local_reliability.yaml
boss benchmark-sync benchmarks/external_repos.yaml
boss open main.py
boss jump BillingService
boss dashboard
boss tools
boss plugins
boss memory
boss solutions
boss learn
boss graph
boss swarm "build user authentication system"
boss agents
boss tasks
boss web
boss build "implement stripe billing api"
boss task-status
boss stop
boss plan "implement stripe billing api"
boss code "implement auth middleware"
boss audit
boss status
```

## AEL Benchmark Fixture

BOSS includes a small git-backed benchmark repo at [projects/ael_auth_benchmark](/Users/tj/BOSS/projects/ael_auth_benchmark) for exercising the Autonomous Engineering Lab on a real repository without touching your main projects.

Example:

```bash
boss project ael_auth_benchmark
boss lab start "Enable token validation caching by setting CACHE_ENABLED to True in auth_service.py without breaking tests." \
  --allow-path auth_service.py \
  --benchmark-command "python3 benchmark_auth.py" \
  --metric latency_ms \
  --auto-approve \
  --max-iterations 1
```

## Reliability Benchmark Corpus

BOSS also includes an internal multi-repo benchmark manifest at [local_reliability.yaml](/Users/tj/BOSS/benchmarks/local_reliability.yaml).

It currently covers:

- [ael_auth_benchmark](/Users/tj/BOSS/projects/ael_auth_benchmark): single-file latency optimization
- [ael_token_service_benchmark](/Users/tj/BOSS/projects/ael_token_service_benchmark): auth token helper implementation
- [ael_rate_limit_benchmark](/Users/tj/BOSS/projects/ael_rate_limit_benchmark): multi-file rate-limiter integration

Run the full corpus:

```bash
boss benchmark benchmarks/local_reliability.yaml
```

Run one suite only:

```bash
boss benchmark benchmarks/local_reliability.yaml --suite auth_latency
```

## External Benchmark Repos

BOSS includes a curated external repo catalog at [external_repos.yaml](/Users/tj/BOSS/benchmarks/external_repos.yaml).
It also includes an external benchmark manifest at [external_reliability.yaml](/Users/tj/BOSS/benchmarks/external_reliability.yaml).

Sync the starter corpus locally:

```bash
boss benchmark-sync benchmarks/external_repos.yaml
```

This clones the curated repos into `projects/` as:

- `ext_fastapi`
- `ext_django_rest_framework`
- `ext_flask`
- `ext_typer`
- `ext_rich`
- `ext_langchain`

The sync command is intentionally conservative: it clones missing repos and only updates existing repos if you pass `--update`.

Run the external benchmark manifest:

```bash
boss benchmark benchmarks/external_reliability.yaml
```

Repeat a suite for stability measurements:

```bash
boss benchmark benchmarks/external_reliability.yaml --suite rich_examples --repeat 5
```

The external corpus uses suite preflight checks and per-suite setup contracts.

- `python_bin` selects the interpreter for that suite
- `setup_create_venv: true` creates an isolated `.boss_benchmark_venv` inside the sandbox
- `setup_commands` run before the task and can use `{python_bin}` and `{project_root}` placeholders

If a synced repo needs a newer Python version or a missing interpreter, BOSS marks that suite as `skipped` with an explicit reason instead of counting it as a model failure.

## Deterministic Evaluation Contracts

Evaluation suites support explicit pass/fail contracts so benchmark results do not depend on LLM judgment alone.

Example:

```yaml
tasks:
  - name: add_rate_limiter
    mode: build
    description: "Add rate limiting middleware"
    expected_files:
      - middleware/rate_limit.py
    expected_imports:
      - middleware.rate_limit
    expected_symbols:
      - RateLimiter
    metric_targets:
      latency_ms:
        lte: 5
    validation_commands:
      - "{python_bin} -c \"print('latency_ms: 4')\""
```

Supported deterministic checks include:

- `expected_files`
- `expected_file_contains`
- `expected_imports`
- `expected_symbols`
- `required_changed_files`
- `forbidden_changed_files`
- `validation_commands`
- `metric_targets`
- `require_tests_passed`

## Artifacts

BOSS now persists run artifacts under [artifacts](/Users/tj/BOSS/artifacts).

Current artifact bundles include:

- evaluation runs and per-task outputs under `artifacts/evaluations/`
- autonomous build summaries, plans, run graphs, step telemetry, and changed-file snapshots under `artifacts/tasks/`

These bundles are designed for debugging, reproducibility, and future learning/promotion gates.

## VS Code Workspace Bridge

BOSS includes a minimal VS Code extension scaffold in [vscode](/Users/tj/BOSS/vscode).

It streams live workspace events into BOSS through `POST /workspace/events`, including:

- `file_opened`
- `file_closed`
- `file_saved`
- `cursor_moved`
- `selection_changed`
- `workspace_changed`

Build it locally:

```bash
cd vscode
npm install
npm run compile
```

Then load the extension in VS Code or package it as a VSIX. Configure the backend URL with:

```text
boss.endpoint = http://127.0.0.1:8080
```

This is the missing bridge for external editor activity that does not already flow through BOSS tools.

For terminal activity outside BOSS, there is also a starter zsh hook at [boss_shell_hook.zsh](/Users/tj/BOSS/examples/boss_shell_hook.zsh). It posts `terminal_command` events into the same workspace API.

## How It Works

### 1. Model Router

`boss/router.py` loads `config/models.yaml` and maps task categories and agent roles to providers and model IDs. The router is provider-agnostic and caches instantiated clients.

### 2. Agents

- `architect_agent.py` creates an execution plan
- `engineer_agent.py` edits the codebase through tools
- `auditor_agent.py` reviews the result and reports structured findings

### 3. Tools

The tool layer exposes:

- `read_file(path)`
- `write_file(path, content, overwrite=False)`
- `search_codebase(query)`
- `run_terminal(command)`
- `git_commit(message)`
- `list_files(directory)`
- `open_file(path)`
- `jump_to_symbol(symbol_name)`
- `highlight_lines(file, start_line, end_line)`
- `replace_code_block(file, start_line, end_line, new_code)`
- `append_to_file(file, content)`

`boss/tools/tool_registry.py` is the central registry for built-in and plugin-provided tools. The engineer receives write access. The architect and auditor are limited to read/search/list plus safe terminal access where appropriate.

### 4. Memory

Project memory is stored in `data/boss_memory.db`.

Stored records include:

- project summaries
- memory entries
- conversations
- code summaries
- structured project memory
- reusable solutions
- style profiles
- knowledge graph nodes and edges

Semantic lookup uses a deterministic local hashing embedder by default. You can switch to OpenAI embeddings in `config/models.yaml` if needed.

### 5. Project Intelligence

`boss/context/codebase_scanner.py` detects languages, key files, entry points, modules, and dependencies.

`boss/context/file_summarizer.py` summarizes changed files one at a time and never sends an entire repository to a model.

`boss/context/project_indexer.py` performs incremental indexing using file hashes and updates:

- indexed file summaries
- semantic vector documents
- project maps

New CLI commands:

```bash
boss index
boss search "auth middleware token refresh"
boss map
```

### 6. Persistent AI Brain

The long-term memory layer is split into:

- `boss/memory/project_memory.py` for structured project descriptions, frameworks, modules, and architecture
- `boss/memory/solution_library.py` for reusable implementation patterns and snippets
- `boss/memory/style_profile.py` for indentation, naming, test style, and error-handling preferences
- `boss/memory/knowledge_graph.py` for project, file, module, concept, and solution relationships
- `boss/memory/context_retriever.py` for prompt-time retrieval

These memories are injected into agent prompts automatically before planning, coding, and auditing.

User-facing commands:

```bash
boss memory
boss solutions
boss learn
boss graph
```

### 7. Agent Swarms

`boss/swarm/swarm_manager.py` coordinates a multi-agent run:

1. Architect plans the goal
2. Engineer implements the changes
3. Test, security, and documentation agents run in parallel
4. Engineer can receive follow-up feedback from test/security results
5. Outputs are merged and stored in memory

`boss/swarm/task_queue.py` handles task priority, retry, pause, resume, and cancel state.

`boss/swarm/agent_worker.py` runs specialized agents through a shared thread pool.

User-facing commands:

```bash
boss swarm "build user authentication system"
boss agents
boss tasks
```

### 8. IDE Integration

`boss/ide/vscode_controller.py` opens files and workspaces in VS Code.

`boss/context/editor_state.py` caches:

- active file
- recent files
- recent changes
- recent semantic searches

`boss/tools/editor_tools.py` gives agents editor-aware actions with diff previews before code replacement.

User-facing commands:

```bash
boss open main.py
boss jump BillingService
boss dashboard
boss tools
boss plugins
```

### 9. Autonomous Development Engine

`boss/engine/autonomous_loop.py` runs an end-to-end development loop:

1. Architect produces a structured plan
2. Engineer executes the current step
3. BOSS runs tests with `run_tests()`
4. Auditor reviews code plus test output
5. Engineer retries until the step passes or the iteration cap is reached
6. BOSS commits the step and advances

Task history is stored in the SQLite database and exposed through:

```bash
boss build "simple REST API with authentication"
boss task-status
boss stop
```

Safety behavior:

- max 10 fix iterations per step by default
- diff preview before autonomous file writes unless `--auto-approve` is used
- no full-repository payloads are sent to the model; only summaries, relevant snippets, and semantic hits

### 10. Web Command Center

`boss/web/server.py` starts a FastAPI command center and opens the dashboard in your browser by default.

`boss/web/routes.py` exposes:

- `/tasks`
- `/agents`
- `/projects`
- `/memory`
- `/logs`

The dashboard in `boss/web/ui/` provides:

- active swarm runs
- running agent status
- project memory insights
- live log streaming over WebSockets
- task controls for pause, resume, and cancel

Start it with:

```bash
boss web
```

### 11. Plugins

Plugins live under `boss/plugins/` and register tools into the shared registry at runtime.

Current sample plugins:

- Unreal Engine discovery helpers
- iOS/Xcode discovery helpers

This is the extension point for future Unreal, iOS, Docker, deployment, or asset-generation tooling.

## Security and Safety

- Existing file overwrites require confirmation unless `--auto-approve` is supplied.
- Terminal execution is sandboxed to a command allowlist and rejects shell control operators.
- All agent activity is logged to `logs/boss.log`.
- File operations are constrained to the active project root.

## Notes

- The default config uses the requested model names (`gpt-5.4` and `claude-opus-4-6`). If your provider account exposes different aliases, update `config/models.yaml`.
- `sqlite3` is intentionally not listed as an installable dependency because it ships with Python.
- The placeholder projects under `projects/` are ready to be replaced with real repositories.
