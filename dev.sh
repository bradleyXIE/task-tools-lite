#!/usr/bin/env bash
# Host dev mode: Postgres in compose, backend (uvicorn --reload) and web (next dev) on the host.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
API_PORT="${API_PORT:-8010}"
WEB_PORT="${WEB_PORT:-3010}"

busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
for p in "$API_PORT" "$WEB_PORT"; do
  if busy "$p"; then
    echo "Port $p is already in use. Stop whatever is listening there, or set API_PORT / WEB_PORT." >&2
    exit 1
  fi
done

export API_URL="http://127.0.0.1:${API_PORT}"
# Only the postgres service: never starts the api/web containers.
docker compose up -d --wait postgres
[ -d backend/.venv ] || python3 -m venv backend/.venv
backend/.venv/bin/pip install -q -e "backend[dev]"
trap 'kill 0' EXIT
(cd backend && .venv/bin/uvicorn app.main:app --port "$API_PORT" --reload --reload-dir app) &
if [ -d web ]; then
  (cd web && { [ -d node_modules ] || npm install; } && npm run dev -- -p "$WEB_PORT") &
fi
wait
