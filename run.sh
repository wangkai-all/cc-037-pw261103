#!/usr/bin/env bash
# Start the whole stack: FastAPI (mock data source + collector + static UI).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi

if [ ! -d frontend/dist ]; then
  (cd frontend && npm install --cache /tmp/npm-cache && npm run build)
fi

exec .venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
