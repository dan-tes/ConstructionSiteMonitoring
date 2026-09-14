# Analysis pipeline — service architecture

Design notes for the multi-step video/plan analysis pipeline. Contracts live in
[`schemas.py`](./schemas.py); this file is the map of how the pieces fit
together. **Status: design agreed, not implemented yet** — `analysis.py`
still runs the single-step stub described in `backend/README.md`.

## Services

Three services sit behind the backend, which is the only orchestrator (no
service calls another service directly):

| Service | Diagram block | Responsibility |
| --- | --- | --- |
| **vision** | 2 — детекция и трекинг | Runs YOLO over an uploaded video, tracks equipment, emits arrival/departure events |
| **phase** | 3 — понять фазу проекта | Given the plan schedule + equipment events, decides the current construction phase |
| **delay** | 4 — прогноз отставания | Given the plan schedule + current phase + today's date, forecasts schedule lag |

Blocks 1 (CSV/Excel plan → `plan_stages`) and 5/6 (GPT summaries) are not
finalized yet — see *Open items* below.

## Integration pattern

One rule, applied everywhere: **plain REST over HTTP, no message queue or
broker**. Long-running work (vision) uses job-submission + polling, not an
event bus — introducing a queue for just one step while everything else
calls direct APIs would mean two different connection styles in one system,
which is the thing to avoid here.

```
Backend (orchestrator)
  │
  ├─ POST {VISION_SERVICE_URL}/detect   {asset_id, video_url, recorded_at} → 202 {job_id}
  │  GET  {VISION_SERVICE_URL}/detect/{job_id}   (poll)
  │       → {status: processing|done|failed, events?: EquipmentEvent[], error?}
  │
  ├─ POST {PHASE_SERVICE_URL}/phase     {plan_stages[], events[]}
  │       → {phase_name, confidence, matched_stage_index}
  │
  └─ POST {DELAY_SERVICE_URL}/delay-forecast   {plan_stages[], current_phase, as_of_date}
          → {delay_days, expected_completion, confidence}
```

`run_analysis()` in `analysis.py` calls these in sequence for each uploaded
journal video: vision → phase → delay → (GPT summaries, blocks 5/6, TBD).
Each service is a plain HTTP dependency; `*_SERVICE_URL` settings live in
`config.py` once clients are implemented.

## Design decisions and why

- **REST + job/poll, never a queue.** Keeps every inter-service connection
  the same shape. A queue would need to be the backbone for *all* steps to
  be introduced at all, not just the slow one — see the discussion that
  settled this.
- **Absolute timestamps everywhere, not video-relative offsets.** The vision
  service receives `recorded_at` (the video's real-world start time) in
  `DetectRequest` and converts internally, so `EquipmentEvent.at` is a real
  `datetime`. Phase and delay never need to know a video's start time or do
  offset math — they only ever reason about the project's real calendar
  (`plan_stages` dates, `as_of_date`).
- **Events, not aggregates, out of vision.** `EquipmentEvent` is a raw
  arrival/departure log (`track_id`, `equipment_class`, `event`, `at`), not a
  pre-aggregated per-class summary. This lets a track's re-appearances be
  told apart from a different unit of the same class, and lets any
  aggregation (occupancy at time T, idle ratio, counts) be derived later
  instead of baked into the vision contract.
- **`equipment_class` is the MOCS class set**, not a free string:
  `worker`, `tower_crane`, `hanging_hook`, `vehicle_crane`, `roller`,
  `bulldozer`, `excavator`, `truck`, `loader`, `pump_truck`,
  `concrete_mixer`, `pile_driver`, `other_vehicle`.
- **`plan_stages` is one shared shape** (`PlanStageIn`: `name`,
  `planned_start`, `planned_end`) fed to both phase and delay — one source of
  truth for the schedule, produced once by block 1 and reused, not
  re-derived per service.
- **Backend owns persistence.** Services are stateless calls; raw `events`
  and the phase/delay results are stored on `MediaAsset`/`Project` by the
  backend (see `models.py`), not by the services themselves.

## Open items

- Block 1: shape of `plan_stages` extraction from the uploaded CSV/Excel
  plan (`is_allowed_plan` in `media.py` now accepts `.csv/.xlsx/.xls`) — who
  parses it (GPT vs. a deterministic parser) and where the result is stored
  on `Project`.
- Blocks 5/6: GPT contracts for the per-video report and the project-level
  status summary — likely in-process calls (already "API"), not separate
  services, but not yet specced.
- `integrations/clients.py` (the actual `httpx` clients for vision/phase/
  delay) is not written yet.
- New `Project`/`MediaAsset` columns (`current_phase`, `delay_days`,
  `status_summary`, `PlanStage` table, `MediaAsset.equipment`) are proposed
  but no Alembic migration exists yet.
