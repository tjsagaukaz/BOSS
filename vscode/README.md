# BOSS Workspace Bridge

This VS Code extension forwards live workspace events into the local BOSS backend.

It sends:

- `file_opened`
- `file_closed`
- `file_saved`
- `cursor_moved`
- `selection_changed`
- `workspace_changed`

to `POST /workspace/events`.

## Setup

1. Start BOSS web:

```bash
boss web
```

2. Install extension dependencies:

```bash
cd /Users/tj/BOSS/vscode
npm install
npm run compile
```

3. In VS Code:

- `Extensions: Install from VSIX...` after packaging, or
- use `Run Extension` from the extension host in this folder

## Configuration

- `boss.endpoint`
- `boss.enableSelectionEvents`
- `boss.enableSaveEvents`

## Commands

- `BOSS: Open Command Center`
- `BOSS: Push Workspace State`
