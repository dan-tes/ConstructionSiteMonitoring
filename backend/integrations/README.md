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
would close the gap. **visual_phase** is the one exception worth flagging up
front: it's real, wired code too (not a stub), but its classifier is
explicitly experimental (9 of the 10 canonical phases, ~51% frame accuracy
on held-out timelapses) — see
the Services section and `services/visual_phase/worker.py`'s docstring
before treating its output as equivalent to `phase`'s.

## Services

Five services sit behind the backend, which is the pipeline's orchestrator
(it decides what runs next; it never calls a service directly):

| Service | Diagram block | Responsibility |
| --- | --- | --- |
| **planner** | 1 — план объекта (LLM fallback) | Given a free-form uploaded plan the canonical parser rejected, classifies it activity-by-activity onto a 43-item closed vocabulary (`services/planner/data/canonical_plan.xlsx`) via `plan_normalizer.py`, then aggregates onto the 10 canonical phases |
| **vision** | 2 — детекция и подсчёт | Runs YOLO over an uploaded video/photo, counts equipment on site by class |
| **visual_phase** | extra, alongside 3 | Reads the construction phase directly off a photo/video frame via an unsupervised visual classifier — no equipment detection involved. Runs in parallel with vision/phase/delay, not as a step in that chain; see below |
| **phase** | 3 — понять фазу проекта | Given the plan schedule + equipment counts, decides the current construction phase |
| **delay** | 4 — прогноз отставания | Given the plan schedule + current phase + today's date, forecasts schedule lag |

Block 1 (uploaded plan → `plan_stages`) has two paths, both landing in the
same `PlanStage` table (see `models.py`) so `analysis.py`'s `_load_plan`
never needs to know which one a given project's plan took:

- **Fast path** (`plan_parser.py`): the uploaded `.xlsx` is already the real
  canonical format — a fixed 21-column activities table (see
  phase_determination/data/
  Требования_к_каноническому_формату_плана_v2.docx) — parsed synchronously,
  in the `upload_plan` request itself, no fuzzy column matching.
- **LLM fallback** (`planner` service, `plan.command`/`plan.result`): anything
  that doesn't parse as canonical (any `.csv`/`.xls`, or an `.xlsx` with the
  wrong shape) goes through `plan_normalizer.py` instead
  (`services/planner/plan_normalizer.py`): an LLM only does the two things
  that need "meaning" — finding which column in the free-form source table
  holds what, and classifying each row's own work item into a CLOSED
  43-activity vocabulary (`services/planner/data/canonical_plan.xlsx`,
  activity -> phase -> expected equipment) — while ordinary code does
  everything else (units, durations, validation, phase aggregation). This
  replaced an earlier design that asked the model to guess whole-phase
  durations directly from a single prompt; classifying per activity and
  summing in code is both more accurate and auditable
  (`plan_normalizer.normalize()`'s per-row log explains every row's fate).
  The one thing this classifier can't derive — the overall project
  critical-path duration, since a free-form plan has no dependency graph —
  is still a single, narrow LLM estimate (`worker.py`'s
  `_estimate_project_duration`). Unlike the fast path this is async — plan
  upload returns immediately with the plan asset's `analysis_status` at
  `"analyzing"`, same lifecycle as a video/photo, and the frontend polls for
  `"ready"`/`"failed"`.

**visual_phase** (`visual_phase.command`/`visual_phase.result`) stands apart
from the vision→phase→delay chain the same way plan's fast/LLM paths stand
apart from each other: `run_entry_analysis()` fires one `visual_phase.command`
per journal entry, against one representative file (see `analysis.py`), and
its result is just persisted (`JournalEntry.visual_phase_name`/
`visual_phase_confidence`) by `handle_visual_phase_result` — it never
blocks, gates or feeds `phase`'s or `delay`'s commands. That's deliberate,
not a missing wire-up: the model
behind it (`services/visual_phase/model.py` — a DINOv2 backbone
self-supervised fine-tuned on ~10k unlabeled site photos, see
`phase_determination/visual_state_pretraining.ipynb`) has no labeled
phase data to learn from, so its classifier is 8 k-means clusters over the
resulting embeddings, hand-labeled by inspecting each cluster's closest
photos (`phase_determination/build_visual_phase_classifier.py`). That photo
set itself skews toward earthwork/foundation/structural-frame scenes, so the
8 clusters only ever land on 4 of the 10 canonical phases (Earthwork,
Foundation, Structural Frame, External Works) — a photo from any of the
other six phases still gets forced onto whichever of those 4 looks closest,
because there's no "unknown" class. **Update:** `weights/classifier.json` is
now a linear head over the same embeddings, trained on frames of 14
phase-labeled timelapses (`phase_determination/visual_phase_timelapse.py`):
9 of 10 phases (no Preconstruction), 50.6% frame accuracy leave-one-video-out
vs 24.8% for the k-means classifier on the same frames (kept as
`classifier_kmeans.json`). Fused 50/50 with the equipment-based phase it
lifts held-out timelapse accuracy from ~54% to ~61%, and that ensemble is
what `JournalEntry.phase_name` now is (`backend/phase_ensemble.py`,
`analysis._maybe_finalize_phase`: the backend waits for both `phase.result`
and `visual_phase.result` — or `VISUAL_PHASE_WAIT_SECONDS` — before the delay forecast). **Update 2:** retrained on 60 labeled
timelapse sites (46 more from YouTube, `phase_determination/eval_all.py`);
`visual_phase` is now an ensemble of the DINOv2 head, a SigLIP head and
SigLIP zero-shot, fused 0.6 visual / 0.4 equipment — 66.4% daily accuracy
held-out by site (10 folds), vs 53.2% for the previous 14-site version on the
46 new sites it never saw. On its own it is
still a secondary signal, not a replacement for the equipment-based phase —
see `services/visual_phase/worker.py`'s docstring and *Open items* below.

Blocks 5/6 (GPT narrative reports) are implemented — see `backend/report.py`
and *Design decisions* below for why they're in-process rather than a sixth
broker leg.

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
   backend  ──plan.command───▶  plan.command.q     ──▶ planner service
   backend  ◀──plan.result───   backend.plan.result.q   ◀── planner service

   backend  ──vision.command──▶  vision.command.q   ──▶ vision service
   backend  ◀──vision.result──   backend.vision.result.q  ◀── vision service

   backend  ──visual_phase.command──▶  visual_phase.command.q   ──▶ visual_phase service
   backend  ◀──visual_phase.result──   backend.visual_phase.result.q  ◀── visual_phase service
   (fired alongside vision.command, not chained after it — see below)

   backend  ──phase.command──▶  phase.command.q     ──▶ phase service
   backend  ◀──phase.result──   backend.phase.result.q   ◀── phase service

   backend  ──delay.command──▶  delay.command.q     ──▶ delay service
   backend  ◀──delay.result──   backend.delay.result.q   ◀── delay service
```

`plan.command`/`plan.result` stand apart from the other four: they're not
part of `run_entry_analysis()`'s per-entry choreography, but a one-off
triggered from `upload_plan` (routers/projects.py) whenever the fast path
(`plan_parser.py`) can't parse what was uploaded. `handle_plan_result` in
`analysis.py` persists the outcome to `PlanStage` the same way the fast path
does, and that's the end of it — no further command follows.

A journal entry (`JournalEntry` — the photos/videos one site-visit upload
produced, together) is where the choreography starts, and it runs once for
the WHOLE entry, not once per file: vision is the one exception, since
equipment detection is genuinely per-file work. `run_entry_analysis()`
publishes one `vision.command` per file in the entry (plus the entry's one
`visual_phase.command`, see above) → each file's own `vision.result`
persists that file's equipment counts and marks that file's own status; once
every file in the entry has reported in, `_maybe_advance_entry()` publishes
the entry's ONE `phase.command` off the combined counts → on `phase.result`
persist the phase and publish the entry's ONE `delay.command` → on
`delay.result` persist the forecast, mark the entry `ready`, and move on to
GPT summaries (blocks 5/6, TBD). `visual_phase.result` is consumed
independently and just persists its two fields, on its own schedule,
whenever it arrives. Each stage transition happens in the backend's result
consumer, keyed by `correlation_id` — the entry's id for
visual_phase/phase/delay, but the individual file's id for vision (see the
`correlation_id` design-decision bullet below).

This replaced an earlier design where phase/delay (and the original,
per-file visual_phase) ran once per individual photo/video: three photos
uploaded together used to fire three separate, slightly-redundant phase
calls instead of the one phase the site visit actually represents.

### Topology

- **Exchange**: `csm.analysis`, topic, durable.
- **Routing keys**: `plan.command`, `plan.result`, `vision.command`,
  `vision.result`, `visual_phase.command`, `visual_phase.result`,
  `phase.command`, `phase.result`, `delay.command`, `delay.result`.
- **Queues**: one per consumer — `vision.command.q` (bound to
  `vision.command`, consumed by the vision service), `backend.vision.result.q`
  (bound to `vision.result`, consumed by the backend), and the same pair for
  visual_phase/phase/delay. Splitting command and result queues per step,
  rather than one shared queue, means each service only ever sees the
  messages meant for it.
- All queues durable, messages published persistent, publisher confirms on.
- Backend and service consumers ack only after their own write (DB commit /
  result publish) succeeds — at-least-once delivery, so every consumer
  handler must be idempotent on `correlation_id` (e.g. skip if that entry's
  phase is already recorded for this run).

### Message envelope

Every message is `Envelope[T]` (see `schemas.py`): `correlation_id` (a
`MediaAsset.id` for `plan`/`vision`, a `JournalEntry.id` for
`visual_phase`/`phase`/`delay` — see the `correlation_id` design-decision
bullet below), `published_at`, and `payload`. The payload is one of the
`*Command`/`*Result` models. A `*Result.status` of
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
- **Counts, not a timestamped event log, out of vision.** `DetectResult`
  carries `EquipmentCount` (`equipment_class`, `count`) — how much of each
  class vision saw in this video/photo, full stop. There's no per-detection
  timestamp and no arrival/departure distinction, because there's nothing to
  hang them on yet: a video/photo is one moment, and the construction phase
  doesn't change within it, so nothing downstream needs to know *when*
  within that clip a unit showed up, only that it did. Video still runs
  ByteTrack so the same physical unit isn't counted once per frame — each
  track contributes to its class's count exactly once — but the track ids
  themselves don't leave the service. `as_of_date`/`planned_start` (phase and
  delay's own calendar anchors) are unrelated to this and are resolved by
  the backend before either command goes out — see the `plan_stages` item
  below for where `planned_start` comes from. If a later requirement needs
  presence-over-time (long-running footage where the phase itself might
  shift), that's a reason to reintroduce a timestamped event stream, not to
  retrofit one onto counts.
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
- **A per-project daily equipment history, not just per-video counts.**
  `analysis.py` persists each day's equipment counts to
  `EquipmentObservation` (merged, max per class, across every video/photo
  analysed that day), and sends the phase service the project's real
  history (`PhaseCommand.history`, sparse and NOT necessarily consecutive —
  see `DailyEquipmentCounts`) instead of only the counts from whichever
  video triggered this run. The phase model was trained on real multi-day
  windows; a single video's counts alone turned out to be enough out of
  that distribution to produce wrong predictions — see
  `services/phase/worker.py`'s docstring.
- **Backend owns persistence.** Services are stateless message handlers; the
  equipment counts and the phase/delay results are stored on
  `MediaAsset`/`JournalEntry`/`Project`/`EquipmentObservation` by the
  backend (see `models.py`), not by the services themselves.
- **`correlation_id` is the file's id for vision, the entry's id for
  everything else.** Equipment detection is genuinely per-file work, so
  `vision.command`/`vision.result` are keyed by `MediaAsset.id` — the join
  key `handle_vision_result` uses to persist that file's own status and
  counts, and to know (via `_maybe_advance_entry`) when every file in an
  entry has reported in. `visual_phase`/`phase`/`delay` are keyed by
  `JournalEntry.id` instead, since their result is one combined answer for
  the whole entry, not one per file — no separate run-id table needed for
  either.
- **visual_phase over the same broker, same envelope shape, fired in
  parallel rather than chained.** Still one connection style for every
  service (`consistent-service-integration-pattern` — queue everywhere,
  never bolt on a direct call for the new one), but a *parallel* leg rather
  than an extra link in the vision→phase→delay chain, because its result
  isn't trustworthy enough yet to gate anything downstream — see the
  Services section above and `services/visual_phase/worker.py`'s docstring.
  If its classifier ever gets real coverage of all 10 phases, promoting it
  into (or blending it with) the equipment-based `phase` step is the
  natural next move; today it's deliberately a sidecar, not a dependency.
- **Blocks 5/6 (`backend/report.py`) are an in-process call, not a sixth
  broker leg.** Nothing outside `analysis.py` ever invokes them, so there's
  no second service to keep consistent with the broker pattern (see
  `consistent-service-integration-pattern`) — this is the same shape as
  planner's own in-process `_estimate_project_duration` call, just one level
  up, in the backend instead of inside a service. Triggered from
  `handle_delay_result`, once an entry has everything else (phase, delay,
  every file's equipment counts) persisted.
- **Grounded citations, not free-form links.** The narrative model is never
  asked to produce a URL — it's given a closed list of this entry's files
  (id/name/url/kind/equipment, see `report.EntryFacts`/`FileEvidence`) and
  writes prose that cites one via a `[[file:ID]]` marker chosen from that
  list; `report._resolve_citations` is what turns a marker into the real
  `[name](url)` markdown link the frontend renders, dropping any id that
  isn't actually in the list. Same closed-vocabulary discipline
  `plan_normalizer.py` uses for activity classification, applied to
  evidence instead — a claim like "no equipment is visible in this photo"
  can only ever point at a photo that was really part of this entry.

## Open items

- **vision**: resolved — `weights/best.pt` is confirmed (by loading it and
  checking `model.names`) to be a real MOCS-trained detector, not the
  COCO-pretrained one this bullet used to describe; it natively reports all
  13 `EquipmentClass` values, not just `truck`/`worker`. Verified against
  real construction photos (`phase_determination/data/Строительная
  техника/`): varied, high-confidence detections across most classes. See
  `services/vision/worker.py`'s docstring.
- **vision**: video tracking can over-count on small/distant subjects in an
  aerial shot — ByteTrack repeatedly loses and re-acquires the same person
  instead of holding one track, so a naive "one track, one count" rule
  measured 175 distinct `worker`s on a 15-second real clip
  (`cv/your_video.mp4`) that clearly doesn't have that many. `MIN_TRACK_SECONDS`
  filters out tracks shorter than that (175 → 68 on the same clip) — a
  mitigation, not a precise fix; see that module's docstring for why there's
  no clean cutoff between "flicker" and "genuinely brief real detection".
- **vision**: no zone splitting. `cv/a.ipynb` assigns each detection to a
  site zone (`ZONES`/`assign_zone`) and checks per-zone deviations against a
  schedule; this service reports "this equipment class is present somewhere
  in frame" with no location on site. Needs a per-project floor plan /
  camera-to-site mapping that doesn't exist yet.
- **phase**: still skips the training data's simulated detector noise
  (`visible`/`confidence` are always 1.0 rather than the miss/false-
  positive-perturbed values training simulated) — a reasonable
  live-inference stand-in, not a faithful reproduction. The bigger gap this
  bullet used to describe — a single-timestep observation instead of the
  multi-day windowed history (`build_project_timeline()` in the training
  notebook) the model was actually trained on — is closed: `analysis.py`
  now persists a real per-project daily equipment history
  (`EquipmentObservation`, merged per class across same-day videos), and
  `services/phase/worker.py`'s `_build_window()` feeds the model a real
  `WINDOW_SIZE`-day sequence, forward-filling days with no analysed video.
  See that module's docstring for why the single-timestep version was found
  (by direct testing against the real checkpoint, not just suspected) to
  produce systematically wrong predictions for equipment whose phase
  association isn't overwhelmingly one-sided.
- **phase**: resolved — `best.pt` had a real accuracy limitation for phases
  with overlapping equipment sets, confirmed by direct testing (not just
  suspected): fed the real counts detected from `cv/your_video.mp4`
  (tower_crane, hanging_hook, worker, other_vehicle, concrete_mixer,
  excavator, vehicle_crane), it predicted `"MEP"` at 90-99% confidence —
  confidence *rising* with more history, so this wasn't the sequence-length
  issue above — despite `concrete_mixer` never once appearing in an MEP
  training activity (100% Foundation/Structural Frame) and
  `tower_crane`/`hanging_hook` being present in only 21% of MEP's activities
  vs. 91% of Structural Frame's. Root cause: `_build_phase_meta()`'s
  per-phase "expected equipment" was a binary set (any activity of that
  phase ever using class X counted the same as every activity using it),
  washing out exactly the signal needed to tell equipment-overlapping phases
  apart. Fixed by retraining, not an inference-side patch — `worker.py`
  deliberately reproduces the training feature construction, so the binary-
  set behavior was baked into what `best.pt` had learned.
  `phase_determination/retrain_weighted_equipment.py` retrains the same
  architecture with each phase's actual equipment *prevalence fraction*
  instead; the new `best.pt` (deployed, old one is in git history) scores
  89.3% test accuracy — see that script's docstring and its printed
  classification report — and correctly predicts `"Foundation"` for the
  same real video counts instead of `"MEP"`. Not perfect: an artificially
  long run of *identical* daily counts (128 straight days of the exact same
  numbers — not a pattern any real project produces) still drifts back
  toward `"MEP"`, so this is a real improvement on realistic inputs, not a
  guarantee against every synthetic edge case.
- **visual_phase**: now a linear head covering 9 of 10 phases (see above);
  still trained only on fixed-camera timelapse frames, not phone photos, and
  fused with `phase` in the backend (see above). The original 8-cluster classifier's gap was
  4 of 10 phases — closing that needed either more/better-
  distributed unlabeled photos across all 10 phases before reclustering
  (`phase_determination/build_visual_phase_classifier.py`), or real labels
  (steps 2-3 the pretraining notebook's own "Дальше" section sketches:
  zero-shot vision-language pseudo-labeling, or weak labels from a project's
  own plan dates) feeding an actual trained classification head instead of
  nearest-centroid. Until then this stays a secondary signal, not something
  `phase`/`delay` should ever read.
- **visual_phase**: `weights/backbone_best.pt` (~250MB) is gitignored the
  same way `services/vision/weights/best.pt` already is — too large to
  commit — so a fresh checkout needs it copied in manually from
  `phase_determination/visual_pretraining_checkpoints/best.pt` before
  `docker compose build visual_phase` will produce a working image.
  `weights/classifier.json` (the cluster centroids + phase labels, small) is
  committed normally.
- **visual_phase**: no history kept — each new result overwrites
  `JournalEntry.visual_phase_name`/`visual_phase_confidence` for that entry,
  there's no per-project trend the way `EquipmentObservation` gives the
  equipment-based phase model. Not needed for a one-photo-per-entry signal
  today, but worth knowing before building anything that expects a time
  series out of it.
- **resolved** — **delay**: `current_phase_started_at` used to be
  simplified to `planned_start +` the matched phase's offset (assumes the
  phase started on time), which zeroed the forecast every time a new phase
  was detected. Now `DelayForecastCommand.current_phase_started_at` carries
  an estimate from the project's own phase history (every entry's
  `phase_name`/`phase_confidence`, persisted since the narrative-reports
  migration) — see `progress.py`'s `estimate_phase_start`: midpoint between
  the last confident reading of the previous phase and the first reading of
  the current one, low-confidence off-phase readings ignored as noise. The
  planned-start fallback only remains for a phase observed since the
  project's first entry.
- **delay**: `planned_start` (the project start anchor) is the project's
  *earliest journal entry date*, not a real "project kickoff" date — a
  project whose first uploaded video is well into construction will
  under-count elapsed time. A real `Project.planned_start_date` (or reading
  it from wherever the canonical plan's source system tracks it) is the
  fix; nothing in the canonical format carries it today.
- Block 1 fast path (`plan_parser.py`): `.xlsx` only, exact canonical
  21-column header, no fuzzy column matching. Exactly one `project_id` per
  workbook is required — no support yet for a plan upload covering multiple
  projects.
- Block 1 LLM fallback (`services/planner`): only handles a *table* upload
  (`.csv`/`.xlsx`/`.xls` — what `is_allowed_plan` in `media.py` already
  restricts to), not an arbitrary document like a PDF or a scanned image of
  a Gantt chart; there's no OCR/document-layout step. `plan_normalizer.py`'s
  own classification stage does retry/self-correct on a malformed or
  incomplete batch response (up to 3 passes, see `normalize()`), but the
  structure-detection stage forwards only the first 30 rows to the model, and
  the separate `_estimate_project_duration` call (`MAX_SOURCE_ROWS`)
  truncates an unusually large source table rather than chunking it, so a
  plan with far more than 500 line items loses whatever's past the cut for
  that one estimate.
- `services/planner/data/phase_equipment_reference.csv` is unused now that
  `plan_normalizer.py` sources its activity → phase → equipment mapping from
  `canonical_plan.xlsx` instead; left in place rather than deleted in case
  anything else still reads it.
- **resolved** — No structured phase/delay columns: `handle_phase_result`/
  `handle_delay_result` used to only render `PhaseResult`/
  `DelayForecastResult` straight into `JournalEntry.stage_summary`, one text
  blob. `phase_name`/`phase_confidence`/`delay_days`/`expected_completion`
  are now their own columns too (migration `a1e6c9d2b4f0`) — `stage_summary`
  still exists and still renders the same prose, but blocks 5/6's narrative
  generator (`report.py`) needed the numbers as structured facts, not
  re-parsed out of that prose, so this stopped being optional.
- Blocks 5/6: the per-entry narrative (block 5) always regenerates before
  the project-level one (block 6), and both call the LLM once per ready
  entry — no caching/reuse between them (a project with many entries makes
  one call per entry, same as `phase`/`delay` already do per entry). Fine at
  today's volume; a cost/latency concern later is a reason to cache
  `generate_project_report`'s facts rather than change block 5.
- Block 5/6 evidence only covers a journal entry's own files, not frames
  extracted from a video (`ProjectFileOut.photos` is still `[]` — see
  `media.py`'s `asset_to_file_out` — frame extraction isn't implemented).
  Once it is, `report.FileEvidence` is the natural place to add extracted
  frames alongside the entry's uploaded files.
- Dead-letter handling: a poison message (a handler that keeps failing) has
  no defined destination yet — likely a per-queue DLX once implementation
  starts, not designed in detail here.
