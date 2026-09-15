# Analysis pipeline — service architecture

Design notes for the multi-step video/plan analysis pipeline. Contracts live in
[`schemas.py`](./schemas.py); this file is the map of how the pieces fit
together. **Status: implemented, real compute.** RabbitMQ, the topology
below, `analysis.py`'s choreography, `../services/*` and block 1's canonical
plan parsing (`plan_parser.py`) are all real and wired end to end — verified
via `docker compose up` with a real video and a real canonical plan
workbook: `pending → analyzing → ready`, YOLOv8m actually detecting
equipment, the trained phase model actually classifying, the
Earned-Schedule model actually forecasting against the uploaded plan. Each
service's module docstring (`services/*/worker.py`) spells out where its
live-inference input still differs from what it saw at training time —
that's a modeling simplification, not a stub; see *Open items* for what
would close the gap.

## Services

Three services sit behind the backend, which is the pipeline's orchestrator
(it decides what runs next; it never calls a service directly):

| Service | Diagram block | Responsibility |
| --- | --- | --- |
| **vision** | 2 — детекция и трекинг | Runs YOLO over an uploaded video, tracks equipment, emits arrival/departure events |
| **phase** | 3 — понять фазу проекта | Given the plan schedule + equipment events, decides the current construction phase |
| **delay** | 4 — прогноз отставания | Given the plan schedule + current phase + today's date, forecasts schedule lag |

Block 1 (canonical plan workbook → `plan_stages`, see `plan_parser.py`) parses
the real canonical format — one `.xlsx` workbook, the activities table
(phase_determination/data/
Требования_к_каноническому_формату_плана_v2.docx) on one sheet, other sheets
ignored — but only the "ideal, already-clean workbook" case: no fuzzy column
matching, no GPT. Blocks 5/6 (GPT summaries) aren't started — see *Open
items* below.

## Integration pattern

One rule, applied everywhere: **every inter-service call goes over RabbitMQ,
no direct HTTP between backend and a service, for any step** — including the
fast ones (phase, delay). Introducing a broker for just the slow step
(vision) while phase/delay stay synchronous REST calls would mean two
connection styles in one system, which is the thing to avoid — see the
`consistent-service-integration-pattern` principle this follows: pick one
connection style, use it uniformly, never bolt on a second one for a single
step.

A useful side effect of going all-in on a broker: vision no longer needs the
job-id + polling shape a REST design would force on a long-running step.
Submitting a command and consuming a result message *is* the async
primitive, so every step — slow or fast — looks the same on the wire.

```
                    ┌───────────────────────────┐
                    │   Exchange: csm.analysis   │
                    │        (topic, durable)    │
                    └───────────────────────────┘
   backend  ──vision.command──▶  vision.command.q   ──▶ vision service
   backend  ◀──vision.result──   backend.vision.result.q  ◀── vision service

   backend  ──phase.command──▶  phase.command.q     ──▶ phase service
   backend  ◀──phase.result──   backend.phase.result.q   ◀── phase service

   backend  ──delay.command──▶  delay.command.q     ──▶ delay service
   backend  ◀──delay.result──   backend.delay.result.q   ◀── delay service
```

`run_analysis()` in `analysis.py` becomes a choreography driven by result
messages rather than a straight-line `await` chain: publish `vision.command`
→ on `vision.result` persist events and publish `phase.command` → on
`phase.result` persist the phase and publish `delay.command` → on
`delay.result` persist the forecast and move on to GPT summaries (blocks
5/6, TBD). Each stage transition happens in the backend's result consumer,
keyed by `correlation_id`.

### Topology

- **Exchange**: `csm.analysis`, topic, durable.
- **Routing keys**: `vision.command`, `vision.result`, `phase.command`,
  `phase.result`, `delay.command`, `delay.result`.
- **Queues**: one per consumer — `vision.command.q` (bound to
  `vision.command`, consumed by the vision service), `backend.vision.result.q`
  (bound to `vision.result`, consumed by the backend), and the same pair for
  phase/delay. Splitting command and result queues per step, rather than one
  shared queue, means each service only ever sees the messages meant for it.
- All queues durable, messages published persistent, publisher confirms on.
- Backend and service consumers ack only after their own write (DB commit /
  result publish) succeeds — at-least-once delivery, so every consumer
  handler must be idempotent on `correlation_id` (e.g. skip if that asset's
  phase is already recorded for this run).

### Message envelope

Every message is `Envelope[T]` (see `schemas.py`): `correlation_id` (the
`MediaAsset.id` this run belongs to), `published_at`, and `payload`. The
payload is one of the `*Command`/`*Result` models. A `*Result.status` of
`"failed"` (with `error` set) is how a service reports its own failure back
through the same channel — the backend's result consumer treats that the
same way `analysis_status = "failed"` works today, no separate error path.

## Design decisions and why

- **RabbitMQ, one exchange, per-step routing keys.** A single topic exchange
  keeps the whole pipeline's wiring in one place instead of one connection
  per service pair, and topic routing leaves room to add blocks 1/5/6 later
  without renegotiating the transport.
- **Broker replaces REST *and* polling, not just REST.** The old design used
  job-submission + polling specifically to avoid a broker; now that a broker
  is the standard connection, polling has no reason to exist — a result
  message arriving *is* the "job done" signal.
- **Absolute timestamps everywhere, not video-relative offsets.** The vision
  service receives `recorded_at` (the video's real-world start time) in
  `DetectCommand` and converts internally, so `EquipmentEvent.at` is a real
  `datetime`. Phase and delay never need to know a video's start time or do
  offset math themselves — `as_of_date` and `planned_start` are already
  resolved to real calendar dates by the backend before either command goes
  out (see the `plan_stages` item below for where `planned_start` actually
  comes from).
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
- **`plan_stages` is one shared shape** (`PlanPhaseIn`: `phase`,
  `phase_order`, `planned_duration_days`) fed to both phase and delay — one
  source of truth for the schedule, produced once by block 1 and reused, not
  re-derived per service. It mirrors the canonical plan format: durations
  only, no calendar dates (the canonical activities table has
  `planned_duration_days` per activity, not `planned_start`/`planned_end` —
  see phase_determination/data/
  Требования_к_каноническому_формату_плана_v2.docx §22). The one calendar
  date delay actually needs, `planned_start`, is carried separately on
  `DelayForecastCommand` — the backend resolves it from the project's
  earliest journal entry, since the canonical plan has nothing to give it.
- **Backend owns persistence.** Services are stateless message handlers; raw
  `events` and the phase/delay results are stored on
  `MediaAsset`/`Project` by the backend (see `models.py`), not by the
  services themselves.
- **`correlation_id` is the `MediaAsset.id`.** One analysis run == one
  uploaded journal video, so the asset's own id is a sufficient join key
  across all three result queues; no separate run-id table needed.

## Open items

- **vision**: only maps COCO's `truck`/`person` to the MOCS equipment set —
  every other class (excavator, crane, bulldozer, ...) has no COCO
  equivalent, so the pretrained detector can never report them. A
  MOCS-trained detector (`cv/b.ipynb` is the training pipeline for one) is
  what actually unlocks the rest of `EquipmentClass`.
- **vision**: emits `"arrival"` once per track and never `"departure"` — no
  presence/exit tracking yet.
- **vision**: no zone splitting. `cv/a.ipynb` assigns each detection to a
  site zone (`ZONES`/`assign_zone`) and checks per-zone deviations against a
  schedule; this service reports "this equipment class arrived somewhere in
  frame" with no location on site. Needs a per-project floor plan / camera-
  to-site mapping that doesn't exist yet.
- **phase**: fed a single-timestep observation (whatever's in this video's
  events) instead of the multi-day windowed history
  (`build_project_timeline()` in the training notebook) the model was
  trained on, and skips the training data's simulated detector noise
  (`visible`/`confidence` are always 1.0). Both are reasonable live-inference
  stand-ins, not a faithful reproduction — see `services/phase/worker.py`'s
  docstring. A real fix needs module 2 to persist a daily occupancy history
  per project, not just per-video events.
- **delay**: `current_phase_started_at` is simplified to `planned_start +`
  the matched phase's offset (assumes the phase started on time) — there's
  no history of when the phase detector first reported the current phase
  for a project to use instead. Same root cause as the phase item above.
- **delay**: `planned_start` (the project start anchor) is the project's
  *earliest journal entry date*, not a real "project kickoff" date — a
  project whose first uploaded video is well into construction will
  under-count elapsed time. A real `Project.planned_start_date` (or reading
  it from wherever the canonical plan's source system tracks it) is the
  fix; nothing in the canonical format carries it today.
- Block 1 (`plan_parser.py`): `.xlsx` only, exact canonical 21-column header
  (`is_allowed_plan` in `media.py` accepts `.csv`/`.xls` too, but those
  aren't parsed — the canonical format needs multiple sheets, which only
  `.xlsx` gives us), no fuzzy column matching, no GPT-assisted parsing of an
  arbitrary plan document. Exactly one `project_id` per workbook is
  required — no support yet for a plan upload covering multiple projects.
- Blocks 5/6: GPT contracts for the per-video report and the project-level
  status summary — likely in-process calls (already outside the broker
  pipeline, since nothing calls them as a service), but not yet specced.
- No new columns/migration added: `handle_phase_result`/`handle_delay_result`
  currently render `PhaseResult`/`DelayForecastResult` straight into the
  existing `stage_summary` text column instead of storing `current_phase`,
  `delay_days`, etc. as structured fields. Fine while the frontend only ever
  displays that text, but if a screen needs the phase name or delay-days
  number on their own later, that's an Alembic migration (`PlanStage` table,
  `MediaAsset.equipment` too, for the same reason on the vision side).
- Dead-letter handling: a poison message (a handler that keeps failing) has
  no defined destination yet — likely a per-queue DLX once implementation
  starts, not designed in detail here.
