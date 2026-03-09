# BOSS UI

Native macOS shell for the BOSS conversation command center.

## What it does

- starts the local BOSS FastAPI backend on `http://127.0.0.1:8097`
- embeds the existing BOSS web chat/dashboard in a native window
- gives you a Dock-launchable `BOSS.app` once bundled

## Development

```bash
cd /Users/tj/BOSS/apps/boss-ui
npm install
npm run tauri dev
```

## Build

```bash
cd /Users/tj/BOSS/apps/boss-ui
npm install
npm run tauri build
```

## Notes

- the app looks for the BOSS repo root automatically by walking up from the app directory
- if that fails, set `BOSS_ROOT=/Users/tj/BOSS`
- the shell prefers `/Users/tj/BOSS/.venv/bin/boss`; if that does not exist it falls back to `python3 main.py web`
