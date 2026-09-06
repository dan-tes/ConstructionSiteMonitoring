# СтройМонитор — backend

FastAPI + PostgreSQL API for authentication and construction-site projects with a
video journal. Site plans and journal videos are uploaded and stored on disk;
each uploaded video goes through a **stubbed** analysis pipeline that produces
placeholder "neural network" text (construction stage per the plan, machinery in
frame). Real frame extraction and a real model/RAG call are not implemented — see
*Media storage* and *Video analysis* below.

## Stack

- FastAPI, async SQLAlchemy 2.0, asyncpg
- PostgreSQL 17 (via Docker Compose for local dev)
- Alembic migrations
- `uv` for dependency management

## Getting started

```bash
cd backend
cp .env.example .env            # adjust SECRET_KEY etc.
```

### Option A — everything in Docker

```bash
docker compose up -d --build
```

Starts PostgreSQL **and** the API (`csm-backend`), runs `alembic upgrade head`
on boot, and serves with `--reload` against the bind-mounted source. The API
container talks to Postgres as `db:5432`; `DATABASE_URL` is overridden in
`docker-compose.yml` for that. The host `.env` value is only used when running
the app outside Docker.

### Option B — Postgres in Docker, API on the host

```bash
docker compose up -d db          # just PostgreSQL on localhost:5432
uv sync                          # install runtime deps
uv run alembic upgrade head      # create tables
uv run uvicorn main:app --reload
```

API docs: http://localhost:8000/docs

## Tests

```bash
uv sync --group dev
uv run pytest
```

Tests run against an in-memory SQLite database and never touch Postgres.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

The database URL is read from `DATABASE_URL` (see `config.py`), so Alembic and the
app always share one source of truth.

## Auth model

The frontend hashes the password client-side as `SHA-256(salt + password)` and
sends the hex digest. The flow:

1. `POST /auth/salt` `{ "username" }` → `{ "salt" }`
   Returns the user's stored salt, or a deterministic HMAC-derived salt for
   unknown usernames so the endpoint can't be used to probe which accounts exist.
2. `POST /auth/register` `{ "username", "passwordHash", "salt" }` → session
3. `POST /auth/login` `{ "username", "passwordHash" }` → session
4. `POST /auth/logout` — `Authorization: Bearer <token>` → `204`
5. `GET /auth/session` — `Authorization: Bearer <token>` → session (`401` if invalid/expired)

The server re-hashes the received digest with bcrypt before storing it. Session
tokens live in the `sessions` table and expire after `SESSION_TTL_DAYS` (default 7).

Auth errors use `{ "detail": { "code", "message" } }` with codes
`VALIDATION`, `USERNAME_TAKEN`, `INVALID_CREDENTIALS`.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/health` | liveness check |
| `POST` | `/auth/salt` | |
| `POST` | `/auth/register` | |
| `POST` | `/auth/login` | |
| `POST` | `/auth/logout` | bearer token |
| `GET` | `/auth/session` | bearer token |
| `GET` | `/projects` | current user's projects, newest first |
| `POST` | `/projects` | `{ name, description? }` |
| `GET` | `/projects/{id}` | includes journal entries, plan and `planStatus` |
| `PATCH` | `/projects/{id}` | `{ name?, description? }` |
| `DELETE` | `/projects/{id}` | also deletes the project's files |
| `POST` | `/projects/{id}/plan` | `multipart/form-data`, field `file` — PDF or image; replaces any existing plan |
| `DELETE` | `/projects/{id}/plan` | |
| `POST` | `/projects/{id}/entries` | `multipart/form-data`: `author`, `date` (`YYYY-MM-DD`), `comment?`, one or more `files` (videos) |
| `GET` | `/projects/{id}/videos/{videoId}` | one video + its analysis `insight` (for polling) |
| `DELETE` | `/projects/{id}/entries/{entryId}` | also deletes the entry's files |
| `GET` | `/files/{assetId}` | **no auth** — streams an uploaded file (supports `Range`) |

All `/projects*` routes require a valid bearer token and are scoped to the owner
(other users get `404`). `/files/{assetId}` is unauthenticated (see below).

## Media storage

Uploads are written to `MEDIA_ROOT` (default `backend/media/`, a Docker volume
`csm-media` in compose) as `<project_id>/<asset_id>.<ext>` and tracked in the
`media_assets` table (`role` = `plan` | `journal_video` | `frame`). Responses
carry a `url` of `{PUBLIC_BASE_URL}/files/{asset_id}`.

`GET /files/{assetId}` is **served without authentication** — the browser's
`<video>`/`<img>` tags can't attach a bearer token, so access is gated only by
the unguessable asset UUID. If that isn't sufficient, the next step is signed
URLs. Starlette's `FileResponse` handles `Range` requests, so video scrubbing
works.

## Video analysis

`analysis.py` runs as a FastAPI background task per uploaded video:
`pending` → `analyzing` → (`ANALYSIS_DELAY_SECONDS` wait) → `ready` / `failed`.
It is a **stub**: `analysis._analyze()` returns hard-coded Russian placeholder
text. To plug in a real model/RAG service, replace that function and keep its
return shape `(stage_summary, equipment_summary)`.

When a video becomes `ready`, the project's `plan_status` is set to that (most
recently analysed) video's `stage_summary`. Extracted frames (`photos`) are not
produced yet — the field is always `[]`. On startup, analyses left `pending`/
`analyzing` by a restart are re-queued (in-process, best effort).

## Wiring the frontend

The frontend already talks to this API:

- `frontend/src/api/client.ts` — `fetch` wrapper, reads `VITE_API_URL`
  (default `http://localhost:8000`), attaches the bearer token, normalises errors.
- `frontend/src/api/httpAuthApi.ts` — implements the `AuthApi` interface against
  `/auth/*` (`frontend/src/api/mockAuthApi.ts` is kept as an offline fallback).
- `frontend/src/api/projectsApi.ts` + `ProjectsContext` — load and mutate projects
  and journal entries via `/projects*`.

Set `VITE_API_URL` in `frontend/.env` if the backend isn't on `localhost:8000`.
The frontend uploads plans and videos via `multipart/form-data`, then polls
`GET /projects/{id}/videos/{videoId}` while a video's analysis is `analyzing`.
