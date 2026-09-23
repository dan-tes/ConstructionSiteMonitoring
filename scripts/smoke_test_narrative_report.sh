#!/usr/bin/env bash
# Smoke test for blocks 5/6 (backend/report.py's GPT narrative reports):
# drives the real HTTP API end to end — register, create a project, upload
# a journal entry, wait for the real vision->phase->delay->narrative
# pipeline to finish — the same way a user (or the frontend) would, not by
# calling backend Python functions directly. Requires the stack already
# running (see backend/docker-compose.yml: `docker compose up -d`).
#
# Usage: scripts/smoke_test_narrative_report.sh [media_file] [base_url]
#   media_file  Video/photo to upload as the journal entry. Defaults to
#               cv/your_video.mp4 (the real clip referenced throughout
#               backend/integrations/README.md) if present.
#   base_url    Backend base URL. Defaults to http://localhost:8000.
#
# Exits non-zero if the backend never comes up, analysis fails, or the
# entry doesn't reach "ready" within POLL_TIMEOUT_S.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MEDIA_FILE="${1:-$REPO_ROOT/cv/your_video.mp4}"
BASE_URL="${2:-http://localhost:8000}"
POLL_TIMEOUT_S="${POLL_TIMEOUT_S:-300}"
POLL_INTERVAL_S=5

if [[ ! -f "$MEDIA_FILE" ]]; then
  echo "media file not found: $MEDIA_FILE" >&2
  exit 1
fi

# Small helpers for talking to the JSON API without depending on jq (not
# assumed to be installed) — python3's stdlib json is always available
# here, same interpreter the backend itself runs on.
json_get() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[sys.argv[1]])' "$1"; }
sha256_hex() { python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }

echo "== waiting for backend at $BASE_URL =="
for _ in $(seq 1 30); do
  if curl -sf "$BASE_URL/docs" -o /dev/null; then
    break
  fi
  sleep 2
done
curl -sf "$BASE_URL/docs" -o /dev/null || { echo "backend never came up" >&2; exit 1; }
echo "backend is up"

USERNAME="smoke_$(date +%s)"
PASSWORD="smoke-test-password"

echo "== registering $USERNAME =="
salt="$(curl -sf -X POST "$BASE_URL/auth/salt" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\"}" | json_get salt)"
password_hash="$(sha256_hex "$salt$PASSWORD")"
token="$(curl -sf -X POST "$BASE_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"passwordHash\":\"$password_hash\",\"salt\":\"$salt\"}" \
  | json_get token)"
auth_header="Authorization: Bearer $token"

echo "== creating project =="
project_id="$(curl -sf -X POST "$BASE_URL/projects" \
  -H "$auth_header" -H 'Content-Type: application/json' \
  -d '{"name":"Smoke test object","description":"scripts/smoke_test_narrative_report.sh"}' \
  | json_get id)"
echo "project: $project_id"

echo "== uploading journal entry ($MEDIA_FILE) =="
entry_id="$(curl -sf -X POST "$BASE_URL/projects/$project_id/entries" \
  -H "$auth_header" \
  -F "author=$USERNAME" \
  -F "date=$(date +%Y-%m-%d)" \
  -F "files=@$MEDIA_FILE" \
  | json_get id)"
echo "entry: $entry_id"

echo "== waiting for analysis (up to ${POLL_TIMEOUT_S}s) =="
elapsed=0
status="pending"
while [[ "$status" != "ready" && "$status" != "failed" && "$elapsed" -lt "$POLL_TIMEOUT_S" ]]; do
  sleep "$POLL_INTERVAL_S"
  elapsed=$((elapsed + POLL_INTERVAL_S))
  entry_json="$(curl -sf "$BASE_URL/projects/$project_id/entries/$entry_id" -H "$auth_header")"
  status="$(printf '%s' "$entry_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("insight") or {}).get("status","pending"))')"
  echo "  [$elapsed s] status=$status"
done

if [[ "$status" != "ready" ]]; then
  echo "analysis did not finish in time (last status: $status)" >&2
  exit 1
fi

echo
echo "== entry insight =="
printf '%s' "$entry_json" | python3 -c '
import json, sys
insight = json.load(sys.stdin)["insight"]
print("stageSummary:     ", insight.get("stageSummary"))
print("equipmentSummary: ", insight.get("equipmentSummary"))
narrative = insight.get("narrativeReport")
print("narrativeReport:  ", narrative or "(none — no YANDEX_CLOUD_* key configured, or the call failed; see backend/report.py)")
'

project_json="$(curl -sf "$BASE_URL/projects/$project_id" -H "$auth_header")"
echo
echo "== project status (block 6) =="
printf '%s' "$project_json" | json_get planStatus

echo
echo "OK — project $project_id / entry $entry_id"
