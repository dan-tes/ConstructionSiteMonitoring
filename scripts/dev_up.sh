#!/usr/bin/env bash
# Starts the whole dev stack: backend services via docker compose (db,
# rabbitmq, backend, planner, vision, phase, delay, visual_phase — see
# backend/docker-compose.yml) plus the frontend's Vite dev server, then
# follows every backend container's logs in this terminal.
#
# Usage: scripts/dev_up.sh [frontend_port]
#
# Ctrl+C stops watching the logs and stops the frontend dev server this
# script started; the backend containers are left running in the
# background (docker compose's normal detached lifecycle — `docker compose
# down` in backend/ to actually stop them, same as
# scripts/smoke_test_narrative_report.sh expects them left up for reuse).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_PORT="${1:-5173}"
FRONTEND_LOG="${TMPDIR:-/tmp}/csm-frontend-dev.log"
BACKEND_URL="http://localhost:8000"

echo "== backend: docker compose up =="
(cd "$REPO_ROOT/backend" && docker compose up -d --build)

echo "== waiting for backend API at $BACKEND_URL =="
for _ in $(seq 1 60); do
  # `curl ... && break` here would trip `set -e` the moment curl first
  # fails (the classic gotcha: in an AND-list, curl's own non-zero status
  # counts as "the command" failing, not the list as a whole) — wrapped in
  # `if` instead, same as scripts/smoke_test_narrative_report.sh.
  if curl -sf "$BACKEND_URL/docs" -o /dev/null; then
    break
  fi
  sleep 2
done
curl -sf "$BACKEND_URL/docs" -o /dev/null \
  || echo "warning: backend API not responding yet at $BACKEND_URL — check 'docker compose logs backend'"

# A previous run's frontend dev server (or anything else) squatting on the
# port would make `npm run dev` fail to bind — free it first. npm doesn't
# forward signals to the vite process it spawns, so killing by port is
# what actually works, not the npm wrapper's own pid. `|| true`: under
# `pipefail`, lsof finding nothing listening (the common case) makes the
# whole pipeline "fail" even though there's nothing wrong — set -e would
# otherwise abort the script right here.
lsof -ti:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill || true

echo "== frontend: starting dev server on :$FRONTEND_PORT (log: $FRONTEND_LOG) =="
(cd "$REPO_ROOT/frontend" && npm run dev -- --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1) &

echo "== waiting for frontend at http://localhost:$FRONTEND_PORT =="
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$FRONTEND_PORT" -o /dev/null; then
    break
  fi
  sleep 1
done
curl -sf "http://localhost:$FRONTEND_PORT" -o /dev/null \
  || echo "warning: frontend not responding yet on :$FRONTEND_PORT — check $FRONTEND_LOG"

cleanup() {
  echo
  echo "== stopping frontend dev server (backend containers keep running) =="
  lsof -ti:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill || true
}
# EXIT alone is enough — bash still runs it when the script is terminated by
# a signal (Ctrl+C's SIGINT included), so a separate INT/TERM trap would
# just run cleanup a second time.
trap cleanup EXIT

echo
echo "backend:       $BACKEND_URL  (docs: $BACKEND_URL/docs)"
echo "frontend:      http://localhost:$FRONTEND_PORT"
echo "frontend logs: $FRONTEND_LOG"
echo
echo "== following backend service logs — Ctrl+C to stop watching =="
(cd "$REPO_ROOT/backend" && docker compose logs -f --tail=50)
