#!/usr/bin/env bash
# Starts the local website: FastAPI on 127.0.0.1:8000 and Vite on 127.0.0.1:5173. Ctrl+C stops both.
# Run from anywhere. Nothing here is hardcoded to one laptop, and nothing listens on a public network.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv yet. See docs/LOCAL_OPERATOR_GUIDE.md, step 2."; exit 1; }
[ -d frontend/node_modules ] || { echo "No frontend/node_modules yet. Run: npm --prefix frontend ci"; exit 1; }
for model in models/threat_model/threat_agent.joblib models/anomaly_model/anomaly_agent.joblib; do
  [ -f "$model" ] || echo "WARNING: $model is missing. The Data Manager will say so and cannot run. See the guide."
done

"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
API=$!
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort &
WEB=$!
trap 'kill "$API" "$WEB" 2>/dev/null || true' EXIT INT TERM
echo "Open http://localhost:5173/data in Chrome. Press Ctrl+C here to stop."
wait
