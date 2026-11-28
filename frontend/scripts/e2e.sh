#!/usr/bin/env bash
# Build, start the backend on a free port with a throwaway data dir, run Playwright, clean up.
# Usage: npm run test:e2e [-- playwright args]   (BT_LIVE=1 also runs the real-YouTube test)
set -euo pipefail
cd "$(dirname "$0")/.."
FRONTEND="$PWD"
BACKEND="$FRONTEND/../backend"
DATA="$(mktemp -d -t bt-e2e-XXXXXX)"
PORT="${BT_E2E_PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')}"
[ -d build ] || npm run build
(cd "$BACKEND" && BT_AUTH=open BT_DATA_DIR="$DATA" BT_STATIC_DIR="$FRONTEND/build" exec .venv/bin/uvicorn bt.main:app --port "$PORT" >"$DATA/api.log" 2>&1) &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$DATA"' EXIT
for _ in $(seq 60); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.5; done
# Existing UI checks start with onboarding completed; welcome.spec.ts explicitly tests new installs.
curl -sf -X PUT -H 'Content-Type: application/json' -d '{"completed":true}' "http://127.0.0.1:$PORT/api/settings/setup" >/dev/null
BT_URL="http://127.0.0.1:$PORT" BT_E2E_DATA="$DATA" BT_E2E_BACKEND="$BACKEND" npx playwright test "$@"
