# BOSS shell hook for zsh.
# Source this from ~/.zshrc after starting the BOSS web server.
#
# Example:
#   source /Users/tj/BOSS/examples/boss_shell_hook.zsh

export BOSS_WORKSPACE_ENDPOINT="${BOSS_WORKSPACE_ENDPOINT:-http://127.0.0.1:8080}"
export BOSS_WORKSPACE_PROJECT="${BOSS_WORKSPACE_PROJECT:-}"

boss_send_workspace_event() {
  local command="$1"
  [ -z "$command" ] && return 0

  local json
  json="$(python3 - "$command" "$PWD" "$BOSS_WORKSPACE_PROJECT" <<'PY'
import json
import sys

command = sys.argv[1]
workdir = sys.argv[2]
project_name = sys.argv[3] or None
payload = {
    "event": "terminal_command",
    "command": command,
    "workdir": workdir,
}
if project_name:
    payload["project_name"] = project_name
print(json.dumps(payload))
PY
)"

  curl -sS -X POST "${BOSS_WORKSPACE_ENDPOINT%/}/workspace/events" \
    -H "Content-Type: application/json" \
    -d "$json" >/dev/null 2>&1 || true
}

preexec() {
  boss_send_workspace_event "$1"
}
