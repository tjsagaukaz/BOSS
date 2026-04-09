# Local Development

## Bootstrap

Create the local virtual environment and install the package in editable mode:

```bash
cd /Users/tj/boss
python3 -m venv .venv
/Users/tj/boss/.venv/bin/python -m pip install -e .
```

Boss should be started and checked with the project venv interpreter:

```bash
cd /Users/tj/boss
/Users/tj/boss/.venv/bin/python -m uvicorn boss.api:app --host 127.0.0.1 --port 8321
```

The convenience launcher uses the same interpreter:

```bash
cd /Users/tj/boss
./start-server.sh
```

Build the macOS app from the native client directory:

```bash
cd /Users/tj/boss/BossApp
swift build
```

## Daily Workflow

Foreground chat:

- Start the backend, open the app, and use the chat surface normally.
- The app keeps the selected work mode local and visible.

Review a task:

- Use the Review surface for working tree, staged, branch diff, file, or project-summary review.
- Boss review mode stays read-only and follows `.boss/review.md` plus applicable `.boss/rules/*.md` guidance.

Background a task:

- From the chat input bar, use the background-job button to launch the current prompt asynchronously.
- Inspect progress from the Jobs surface, tail the local log, resume interrupted work, or take the job over into foreground chat.
- Task-branch behavior defaults from `.boss/config.toml` under `[jobs]`. Git repos can suggest or create a `boss/<slug>` branch through `scripts/task_branch.sh`.

Inspect diagnostics:

- Use the Diagnostics surface in the app to verify git state, selected mode, provider mode, pending memory, pending jobs, lock consistency, and Boss control health.
- The same high-signal data is available through `/api/system/status` for scripts and local tooling.

## Health Checks

Run the local doctor to verify the venv, runtime packages, lock/process agreement, git state, and live status payload:

```bash
cd /Users/tj/boss
/Users/tj/boss/.venv/bin/python scripts/dev_doctor.py
```

Run the safe local smoke checks:

```bash
cd /Users/tj/boss
/Users/tj/boss/.venv/bin/python scripts/smoke_local.py
```

Enable a live chat roundtrip only when you explicitly want it:

```bash
cd /Users/tj/boss
BOSS_SMOKE_CHAT=1 /Users/tj/boss/.venv/bin/python scripts/smoke_local.py
```

## Release Check

Run the repeatable release check before a checkpoint, audit pass, or merge:

```bash
cd /Users/tj/boss
./scripts/release_check.sh
```

The release check will:

- compile the backend
- run the regression harness in `.venv`
- run the local doctor and smoke checks
- build the macOS app with `swift build`

## Notes

- The doctor and smoke scripts are local-only and do not add hosted telemetry.
- Startup will report stale or mismatched lock/process state clearly, but it will not kill any process automatically.
- The app will try to start the local backend automatically when it can locate the workspace and `.venv` runtime. If a different server is already bound to `127.0.0.1:8321`, the app will warn instead of killing it.

## iOS Delivery

For archiving, exporting, and uploading iOS apps to TestFlight, see [docs/ios-delivery.md](ios-delivery.md). The dev doctor now checks for Xcode toolchain prerequisites and signing configuration as part of its standard run.
- Boss-native control files live in `BOSS.md`, `.boss/config.toml`, `.boss/review.md`, and `.boss/rules/*.md`. Keep those current before trusting a review or background run.