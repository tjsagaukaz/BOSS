#!/bin/zsh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/.venv/bin/python"
BASE_URL=${BOSS_BASE_URL:-http://127.0.0.1:8321}
API_LOG="$ROOT/.release-check-api.log"
SERVER_PID=""
STARTED_SERVER=0

export BOSS_BASE_URL="$BASE_URL"
export BOSS_RELEASE_CHECK_ROOT="$ROOT"
export BOSS_RELEASE_CHECK_PYTHON="$PYTHON"

if [[ ! -x "$PYTHON" ]]; then
	echo "Missing interpreter at $PYTHON" >&2
	exit 1
fi

backend_ready() {
	"$PYTHON" -c 'import json, os, urllib.request; from pathlib import Path; base_url = os.environ.get("BOSS_BASE_URL", "http://127.0.0.1:8321"); expected_root = Path(os.environ["BOSS_RELEASE_CHECK_ROOT"]).resolve(); expected_python = Path(os.environ["BOSS_RELEASE_CHECK_PYTHON"]).resolve(); response = urllib.request.urlopen(f"{base_url}/api/system/status", timeout=2.0); payload = json.loads(response.read().decode("utf-8")); workspace_path = Path(payload.get("workspace_path", "")).resolve(); interpreter_path = Path(payload.get("interpreter_path", "")).resolve(); raise SystemExit(0 if response.status == 200 and workspace_path == expected_root and interpreter_path == expected_python and payload.get("git") and payload.get("diagnostics") else 1)' >/dev/null 2>&1
}

backend_listening() {
	"$PYTHON" -c 'import os, socket; from urllib.parse import urlparse; base_url = os.environ.get("BOSS_BASE_URL", "http://127.0.0.1:8321"); parsed = urlparse(base_url); host = parsed.hostname or "127.0.0.1"; port = parsed.port or (443 if parsed.scheme == "https" else 80); sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); sock.settimeout(0.5); result = sock.connect_ex((host, port)); sock.close(); raise SystemExit(0 if result == 0 else 1)' >/dev/null 2>&1
}

wait_for_existing_backend() {
	if ! backend_listening; then
		return 1
	fi
	for _ in {1..20}; do
		if backend_ready; then
			return 0
		fi
		sleep 1
	done
	return 1
}

cleanup() {
	if [[ $STARTED_SERVER -eq 1 && -n "$SERVER_PID" ]]; then
		kill "$SERVER_PID" >/dev/null 2>&1 || true
		wait "$SERVER_PID" 2>/dev/null || true
	fi
}
trap cleanup EXIT

echo "== Boss release check =="
echo "root: $ROOT"

if wait_for_existing_backend; then
	echo "Using existing backend at $BASE_URL"
else
	echo "Starting local backend for release check"
	cd "$ROOT"
	"$PYTHON" -m uvicorn boss.api:app --host 127.0.0.1 --port 8321 >"$API_LOG" 2>&1 &
	SERVER_PID=$!
	STARTED_SERVER=1
	for _ in {1..30}; do
		if backend_ready; then
			break
		fi
		sleep 1
	done
	if ! backend_ready; then
		echo "Backend did not become ready. Recent log output:" >&2
		tail -40 "$API_LOG" >&2 || true
		exit 1
	fi
fi

echo "-- Backend compile check"
cd "$ROOT"
"$PYTHON" -m compileall boss

echo "-- Backend regression harness"
"$PYTHON" -m unittest discover -s "$ROOT/tests" -p 'test_regression_harness.py'

echo "-- Local doctor"
"$PYTHON" "$ROOT/scripts/dev_doctor.py"

echo "-- Local smoke"
"$PYTHON" "$ROOT/scripts/smoke_local.py"

echo "-- Swift build"
cd "$ROOT/BossApp"
swift build

echo "Release check passed."