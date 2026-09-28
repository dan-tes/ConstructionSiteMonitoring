"""Video-analysis pipeline — orchestrated over RabbitMQ.

One journal entry (`JournalEntry`) is one upload batch — the photos/videos a
site visit produced, uploaded together. Vision (equipment detection) still
runs once per file, because detection is genuinely a per-file operation, but
phase/delay/visual_phase run ONCE for the whole entry, not once per file:
`run_entry_analysis()` kicks off vision for every file in the entry, and
`_maybe_advance_entry()` fires the entry's one `phase.command` only once
every file has reported its own vision result — see that function's
docstring. The rest of the pipeline is a choreography driven by result
messages (see integrations/README.md for the full topology): each
`handle_*_result` below is registered as a consumer (see `start_consumers()`,
wired up from `main.py`) and, on success, persists its step's output and
publishes the next command. The final step (`handle_delay_result`) marks the
entry `ready`.

This replaced an earlier design where every step (vision *and*
phase/delay/visual_phase) ran per individual photo/video: uploading three
photos in one entry used to produce three separate, slightly-redundant phase
calls and three separate result pages, instead of the site visit's one
actual phase. `MediaAsset.analysis_status` still exists (needed for the
plan-normalization step, and to track each file's own vision progress), but
`stage_summary`/`equipment_summary`/`visual_phase_*` now live on
`JournalEntry` only.

`plan_stages` comes from `models.PlanStage`, populated once at plan-upload
time (see `routers/projects.py`'s `upload_plan`) rather than re-read from
disk on every analysis step: an already-canonical workbook is parsed
synchronously right there (`plan_parser.py`); anything else is normalized by
the `planner` service's LLM fallback, whose result `handle_plan_result`
below persists the same way. No plan uploaded (or nothing persisted yet for
it) just means phase/delay run with an empty schedule. The canonical format
carries no calendar dates, so `planned_start` (the delay forecast's one date
anchor) comes from the project's earliest journal entry instead — see
`_project_planned_start`.

vision/phase/delay (see `services/*/worker.py`) run real models now: YOLOv8m
+ ByteTrack for video, single-frame YOLOv8m detection for photos, the
trained ConstructionPhaseModel for phase, the Earned-Schedule forecast for
delay — see each worker's module docstring for what's faithful to training
and what's a live-inference simplification.

Visual phase: `services/visual_phase`'s per-entry phase probabilities are
persisted as JSON (`JournalEntry.visual_phase_details`) and sent — for every
entry in the equipment-history window — to `services/phase` as
`visual_history`, which fuses them with the equipment model over the
project's history (see services/phase/fusion.py). So `_maybe_advance_entry`
waits for BOTH every file's vision result AND the entry's visual-phase
result before firing phase.command; a visual result that never comes is
replaced by a `{"status": "timeout"}` marker after `VISUAL_PHASE_TIMEOUT_S`,
so a down visual service delays an entry but never blocks it. The entry's
phase_name is services/phase's answer as-is — no second fusion here.

`handle_delay_result` is also where blocks 5/6 (GPT narrative reports, see
`report.py`) run: once an entry is `ready`, it builds that entry's
structured facts (`_build_entry_facts`) and generates its own narrative
(block 5, `entry.narrative_report`), then `_refresh_plan_status` generates
the project-level one (block 6, `Project.plan_status`) from the same facts
for the most recently analysed entry. Both are best-effort — see
`report.py`'s module docstring for why a missing/failed narrative never
fails the entry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

import report
from config import settings
from database import SessionLocal
from integrations import broker
from integrations.schemas import (
    CANONICAL_PHASES,
    DailyEquipmentCounts,
    DailyVisualPhase,
    DelayForecastCommand,
    DelayForecastResult,
    DetectCommand,
    DetectResult,
    Envelope,
    EquipmentCount,
    PhaseCommand,
    PhaseResult,
    PlanNormalizeResult,
    PlanPhaseIn,
    VisualPhaseCommand,
    VisualPhaseResult,
)
from media import file_url
from models import EquipmentObservation, JournalEntry, MediaAsset, PlanStage, Project
from plan_parser import ParsedPlan
from progress import PhaseObservation, estimate_phase_start

# How many days of a project's equipment-observation history to feed the
# phase model — see services/phase/worker.py's WINDOW_SIZE (128) docstring;
# a little more than that so its forward-fill has something to look back on
# even near the start of the requested window.
EQUIPMENT_HISTORY_DAYS = 200

# How long `_maybe_advance_entry` waits for an entry's visual-phase result
# before giving up on it (see `_visual_phase_timeout`). Vision on a long
# video usually takes longer than this anyway, so in practice the wait only
# matters when services/visual_phase is down or backlogged.
VISUAL_PHASE_TIMEOUT_S = 600.0

# Strong refs to fire-and-forget tasks (asyncio only keeps weak ones).
_background_tasks: set[asyncio.Task] = set()

# Older than this, the summary warns that the phase estimate is stale.
STALE_OBSERVATION_DAYS = 14

log = logging.getLogger("csm.analysis")


def _envelope(correlation_id: uuid.UUID, payload) -> Envelope:
    return Envelope(
        correlation_id=correlation_id, published_at=datetime.now(timezone.utc), payload=payload
    )


def _summarize_equipment(counts: list[EquipmentCount]) -> str:
    if not counts:
        return "Техника в кадре не обнаружена."
    parts = ", ".join(f"{c.equipment_class} ({c.count})" for c in sorted(counts, key=lambda c: c.equipment_class))
    return f"В кадре обнаружена техника: {parts}."


def _summarize_phase(result: PhaseResult) -> str:
    confidence_pct = round((result.confidence or 0) * 100)
    text = f"Текущая фаза объекта: {result.phase_name} (уверенность {confidence_pct}%)."
    if result.equipment_phase or result.visual_phase:
        text += (
            f" По технике: {result.equipment_phase or 'нет данных'},"
            f" по изображению: {result.visual_phase or 'нет данных'}."
        )
        if result.equipment_phase and result.visual_phase and result.equipment_phase != result.visual_phase:
            text += " Сигналы расходятся — оценку стоит проверить."
    if result.days_since_last_observation and result.days_since_last_observation > STALE_OBSERVATION_DAYS:
        text += f" Последнее наблюдение — {result.days_since_last_observation} дн. назад."
    return text


def _summarize_delay(stage_summary: str | None, result: DelayForecastResult) -> str:
    if result.delay_days is None:
        addition = "Прогноз отставания недоступен."
    elif result.delay_days > 0:
        addition = f"Отставание от графика: {result.delay_days} дн. (ожидаемое завершение — {result.expected_completion})."
    elif result.delay_days < 0:
        addition = f"Опережение графика: {-result.delay_days} дн. (ожидаемое завершение — {result.expected_completion})."
    else:
        addition = f"Соответствует графику (ожидаемое завершение — {result.expected_completion})."
    return f"{stage_summary} {addition}" if stage_summary else addition


async def _load_plan(db, project_id: uuid.UUID) -> ParsedPlan | None:
    """The project's persisted plan schedule, if any (see `PlanStage` /
    `handle_plan_result` below and `upload_plan` in routers/projects.py for
    where these rows come from). No plan uploaded, or one still being
    parsed/normalized, just means phase/delay run with an empty schedule
    (None, not raised — a missing/pending plan isn't an analysis failure)."""
    project = await db.get(Project, project_id)
    if project is None or project.plan_duration_days is None:
        return None
    rows = (
        await db.execute(
            select(PlanStage)
            .where(PlanStage.project_id == project_id)
            .order_by(PlanStage.phase_order)
        )
    ).scalars().all()
    if not rows:
        return None
    phases = [
        PlanPhaseIn(phase=r.phase, phase_order=r.phase_order, planned_duration_days=r.planned_duration_days)
        for r in rows
    ]
    return ParsedPlan(phases=phases, project_duration_days=project.plan_duration_days)


async def _upsert_equipment_observation(
    db, project_id: uuid.UUID, obs_date: date, counts: list[EquipmentCount]
) -> None:
    """Merge this video/photo's counts into the project's daily aggregate
    for `obs_date` — max per class, not sum: two videos shot the same day
    are two partial views of the same day's site, not two additions (summing
    would double-count a crane simply because it's visible in both clips).
    Feeds the phase model's real multi-day history — see
    services/phase/worker.py. Note this merges across every entry on
    `obs_date`, not just one entry's own files — two separate entries
    uploaded for the same calendar date share one row here (and so, via
    `_maybe_advance_entry`, one combined `equipment_summary` text) — a
    pre-existing simplification, not new to entry-level combination."""
    existing = (
        await db.execute(
            select(EquipmentObservation).where(
                EquipmentObservation.project_id == project_id,
                EquipmentObservation.date == obs_date,
            )
        )
    ).scalar_one_or_none()
    merged = {c.equipment_class: c.count for c in counts}
    if existing is not None:
        for cls, n in json.loads(existing.counts).items():
            merged[cls] = max(merged.get(cls, 0), n)
        existing.counts = json.dumps(merged)
    else:
        db.add(EquipmentObservation(project_id=project_id, date=obs_date, counts=json.dumps(merged)))


async def _load_equipment_history(db, project_id: uuid.UUID) -> list[DailyEquipmentCounts]:
    """The project's real day-by-day equipment counts, chronological — sparse
    (only days with an analysed video/photo) and capped to the last
    `EQUIPMENT_HISTORY_DAYS` rows. See services/phase/worker.py's
    `_build_window()` for how the phase service turns this into the dense
    daily sequence its model expects."""
    rows = (
        await db.execute(
            select(EquipmentObservation)
            .where(EquipmentObservation.project_id == project_id)
            .order_by(EquipmentObservation.date.desc())
            .limit(EQUIPMENT_HISTORY_DAYS)
        )
    ).scalars().all()
    return [
        DailyEquipmentCounts(
            date=r.date,
            counts=[
                EquipmentCount(equipment_class=cls, count=n) for cls, n in json.loads(r.counts).items()
            ],
        )
        for r in reversed(rows)
    ]


async def _load_visual_history(db, project_id: uuid.UUID, as_of_date: date) -> list[DailyVisualPhase]:
    """Every journal entry's visual-phase probabilities (see
    `handle_visual_phase_result`) within the same look-back as the equipment
    history, up to and including `as_of_date`. Entries without a usable
    result (failed, timed out, or analysed before visual_phase sent
    `phase_probs`) are skipped; several entries on one date are sent
    separately and averaged by services/phase."""
    since = as_of_date - timedelta(days=EQUIPMENT_HISTORY_DAYS)
    rows = (
        await db.execute(
            select(JournalEntry.date, JournalEntry.visual_phase_details)
            .where(
                JournalEntry.project_id == project_id,
                JournalEntry.visual_phase_details.is_not(None),
                JournalEntry.date <= as_of_date,
                JournalEntry.date >= since,
            )
            .order_by(JournalEntry.date)
        )
    ).all()
    out = []
    for d, raw in rows:
        try:
            details = json.loads(raw)
        except (TypeError, ValueError):
            continue
        probs = details.get("phase_probs") if details.get("status") == "done" else None
        if probs:
            out.append(DailyVisualPhase(date=d, phase_probs=probs))
    return out


def _on_background_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error("background analysis task failed", exc_info=task.exception())


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)


async def _visual_phase_timeout(entry_id: uuid.UUID, delay_s: float | None = None) -> None:
    """If the entry's visual-phase result hasn't arrived after `delay_s`
    (default: `VISUAL_PHASE_TIMEOUT_S`, read at call time), record a timeout
    marker so `_maybe_advance_entry` stops waiting for it. Lost on restart
    like any in-process timer — `requeue_pending` re-arms it."""
    if delay_s is None:
        delay_s = VISUAL_PHASE_TIMEOUT_S
    await asyncio.sleep(delay_s)
    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is None or entry.analysis_status != "analyzing" or entry.visual_phase_details is not None:
            return
        log.warning("visual phase result for entry %s timed out after %ss", entry_id, delay_s)
        entry.visual_phase_details = json.dumps({"status": "timeout"})
        await db.commit()
    await _maybe_advance_entry(entry_id)


async def _project_planned_start(db, project_id: uuid.UUID) -> date | None:
    """Project start anchor for the delay forecast. The canonical plan
    carries no calendar dates (see plan_parser.py), so this is the earliest
    journal entry date recorded for the project instead — a project with no
    entries yet (shouldn't happen mid-analysis, since analysing an entry
    means it already exists) has no anchor to forecast from."""
    return (
        await db.execute(
            select(func.min(JournalEntry.date)).where(JournalEntry.project_id == project_id)
        )
    ).scalar_one_or_none()


async def _current_phase_started_at(
    db, project_id: uuid.UUID, current_phase: str, as_of_date: date
) -> date | None:
    """The delay model's `current_phase_started_at`, estimated from every
    phase reading this project has up to `as_of_date` — see
    progress.estimate_phase_start. Includes the entry being analysed right
    now, so its phase_name must already be flushed/committed."""
    rows = (
        await db.execute(
            select(JournalEntry.date, JournalEntry.phase_name, JournalEntry.phase_confidence).where(
                JournalEntry.project_id == project_id,
                JournalEntry.phase_name.is_not(None),
                JournalEntry.date <= as_of_date,
            )
        )
    ).all()
    return estimate_phase_start(
        [PhaseObservation(date=d, phase=p, confidence=c) for d, p, c in rows], current_phase
    )


async def _build_entry_facts(db, entry: JournalEntry) -> report.EntryFacts:
    """Assembles blocks 5/6's structured input (see report.py's EntryFacts)
    from what's already persisted on this entry and its files: the phase/
    delay numbers `stage_summary` renders into prose, plus each file's own
    equipment_counts (not the day-merged EquipmentObservation — the
    narrative needs to point at a specific photo/video, see
    report.FileEvidence). Called once the entry's phase_name/delay_days
    columns are set, so callers should commit those first if this is
    running inside the same session."""
    project = await db.get(Project, entry.project_id)
    media = (
        await db.execute(select(MediaAsset).where(MediaAsset.entry_id == entry.id))
    ).scalars().all()
    files = [
        report.FileEvidence(
            asset_id=asset.id,
            name=asset.original_name,
            url=file_url(asset.id),
            kind=asset.kind,
            equipment=json.loads(asset.equipment_counts) if asset.equipment_counts else {},
        )
        for asset in media
    ]
    return report.EntryFacts(
        project_name=project.name if project else "",
        entry_date=entry.date,
        phase_name=entry.phase_name,
        phase_confidence=entry.phase_confidence,
        delay_days=entry.delay_days,
        expected_completion=entry.expected_completion,
        files=files,
    )


async def _mark_entry_failed(entry_id: uuid.UUID, step: str, error: str | None) -> None:
    log.error("analysis step %r failed for entry %s: %s", step, entry_id, error)
    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is not None:
            entry.analysis_status = "failed"
            await db.commit()


async def _refresh_plan_status(project_id: uuid.UUID) -> None:
    """Block 6 (диаграмма: блок 6) — project-level status, grounded in the
    most recently analysed journal entry's structured facts (same facts
    block 5 uses for that entry's own narrative, see report.py and
    _build_entry_facts). Falls back to that entry's plain stage_summary
    when narrative generation is unavailable or fails (no YANDEX_CLOUD_*
    key configured, or the call errored) rather than leaving plan_status
    blank — report.generate_project_report already returns None in exactly
    that case, see its docstring.

    Runs its own sessions (rather than reusing a caller's) because it makes
    a network call to the LLM in between reading the facts and writing the
    result — same reason handle_delay_result splits its own work across two
    sessions around that call."""
    async with SessionLocal() as db:
        latest = (
            await db.execute(
                select(JournalEntry)
                .where(
                    JournalEntry.project_id == project_id,
                    JournalEntry.analysis_status == "ready",
                )
                .order_by(JournalEntry.analyzed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is None:
            project = await db.get(Project, project_id)
            if project is not None:
                project.plan_status = None
                await db.commit()
            return
        facts = await _build_entry_facts(db, latest)
        fallback = latest.stage_summary

    narrative = await asyncio.to_thread(report.generate_project_report, facts)

    async with SessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is not None:
            project.plan_status = narrative or fallback
            await db.commit()


async def handle_plan_result(envelope: Envelope[PlanNormalizeResult]) -> None:
    """Block 1's LLM fallback finishing: persist the normalized phases the
    same way the canonical-workbook fast path does (see `upload_plan` in
    routers/projects.py), so `_load_plan` never needs to know which path a
    given project's plan took."""
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed" or not result.phases or result.project_duration_days is None:
        log.error("plan normalization failed for asset %s: %s", asset_id, result.error)
        async with SessionLocal() as db:
            asset = await db.get(MediaAsset, asset_id)
            if asset is not None:
                asset.analysis_status = "failed"
                await db.commit()
        return

    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None or asset.role != "plan":
            return
        project = await db.get(Project, asset.project_id)
        await db.execute(delete(PlanStage).where(PlanStage.project_id == asset.project_id))
        for phase in result.phases:
            db.add(
                PlanStage(
                    project_id=asset.project_id,
                    phase=phase.phase,
                    phase_order=CANONICAL_PHASES.index(phase.phase) + 1,
                    planned_duration_days=phase.planned_duration_days,
                    expected_equipment=json.dumps(phase.expected_equipment),
                )
            )
        project.plan_duration_days = result.project_duration_days
        asset.analysis_status = "ready"
        await db.commit()


async def run_analysis(asset_id: uuid.UUID) -> None:
    """Kick off one file's equipment detection (vision) — publishes
    `vision.command` and returns; `handle_vision_result` picks up from
    there. The entry-level steps (visual_phase/phase/delay) are orchestrated
    by `run_entry_analysis`/`_maybe_advance_entry`, not here — vision alone
    still runs per file because detection is genuinely per-file work, while
    the rest of the pipeline runs once for the whole entry."""
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None or asset.role not in ("journal_video", "journal_photo"):
            return
        asset.analysis_status = "analyzing"
        await db.commit()

    command = DetectCommand(
        asset_id=asset_id,
        media_url=f"{settings.internal_url}/files/{asset_id}",
        kind=asset.kind,
    )
    body = _envelope(asset_id, command).model_dump_json().encode()
    await broker.publish(broker.VISION_COMMAND, body)


async def run_entry_analysis(entry_id: uuid.UUID) -> None:
    """Kick off one journal entry's (one upload batch's) analysis: mark it
    in-flight, start vision on every one of its files, and fire the one
    visual_phase.command for the whole entry. `_maybe_advance_entry` (called
    from `handle_vision_result`) fires the entry's one phase.command once
    every file's vision result is in."""
    async with SessionLocal() as db:
        entry = (
            await db.execute(
                select(JournalEntry)
                .where(JournalEntry.id == entry_id)
                .options(selectinload(JournalEntry.media))
            )
        ).scalar_one_or_none()
        if entry is None or not entry.media:
            return
        entry.analysis_status = "analyzing"
        # Re-analysis must wait for a fresh visual result, not reuse the old one.
        entry.visual_phase_details = None
        media = list(entry.media)
        await db.commit()

    for asset in media:
        await run_analysis(asset.id)

    # One representative file for the visual-phase signal. A photo is
    # preferred: the visual classifier was evaluated on photos, and video
    # frames are lower-res/blurrier (a video still gets several frames
    # averaged, see services/visual_phase/worker.py). Sending every file and
    # averaging would be a possible next step.
    representative = next((a for a in media if a.kind == "image"), media[0])
    visual_command = VisualPhaseCommand(
        # Not a MediaAsset id here — the visual-phase result is entry-level,
        # so this (and the envelope's correlation_id below) is the entry's
        # id. The field only ever reaches this service's own logging.
        asset_id=entry_id,
        media_url=f"{settings.internal_url}/files/{representative.id}",
        kind=representative.kind,
    )
    visual_body = _envelope(entry_id, visual_command).model_dump_json().encode()
    await broker.publish(broker.VISUAL_PHASE_COMMAND, visual_body)
    _spawn(_visual_phase_timeout(entry_id))


async def handle_vision_result(envelope: Envelope[DetectResult]) -> None:
    """Persists this one file's own equipment counts/status, then checks
    whether its whole entry is now ready to advance to phase/delay — see
    `_maybe_advance_entry`."""
    asset_id = envelope.correlation_id
    result = envelope.payload

    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None:
            return
        entry_id = asset.entry_id
        if result.status == "failed":
            log.error("vision failed for asset %s: %s", asset_id, result.error)
            asset.analysis_status = "failed"
        else:
            entry = await db.get(JournalEntry, entry_id) if entry_id else None
            # Which calendar day this video/photo's equipment counts belong
            # to — shouldn't be missing (a journal_video/photo always has an
            # entry), but falls back to today rather than raising if it is.
            obs_date = entry.date if entry is not None else date.today()
            await _upsert_equipment_observation(db, asset.project_id, obs_date, result.counts or [])
            # This file's OWN counts, kept separately from the day-merged
            # EquipmentObservation above — see MediaAsset.equipment_counts
            # and report.py's FileEvidence: the block-5 narrative cites a
            # specific photo/video's own findings, not just the day total.
            asset.equipment_counts = json.dumps(
                {c.equipment_class: c.count for c in (result.counts or [])}
            )
            asset.analysis_status = "ready"
        await db.commit()

    if entry_id is not None:
        await _maybe_advance_entry(entry_id)


async def _maybe_advance_entry(entry_id: uuid.UUID) -> None:
    """Fires the entry's ONE phase.command once every one of its files has
    reported its own vision result (ready or failed) AND the entry's visual-
    phase result is in (done, failed or timed out) — this is what turns
    N files' worth of vision results back into a single combined phase call
    instead of N redundant ones. Re-entrant and lock-free: harmless (if a
    little wasteful) to call more than once for the same entry, since it
    only acts while `analysis_status` is still "analyzing"; a rare race
    between two files' results finishing at the same instant could still
    fire phase.command twice with no lock around the check — that just means
    two phase.result messages come back and the second one wins, consistent
    with the at-least-once delivery every consumer here already has to
    tolerate (see integrations/README.md)."""
    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is None or entry.analysis_status != "analyzing":
            return
        assets = (
            await db.execute(select(MediaAsset).where(MediaAsset.entry_id == entry_id))
        ).scalars().all()
        if not assets or any(a.analysis_status in ("pending", "analyzing") for a in assets):
            return  # still waiting on some file's vision result
        if entry.visual_phase_details is None:
            return  # still waiting on the visual-phase result (or its timeout)
        if all(a.analysis_status == "failed" for a in assets):
            entry.analysis_status = "failed"
            await db.commit()
            log.error("all files failed vision for entry %s", entry_id)
            return

        obs = (
            await db.execute(
                select(EquipmentObservation).where(
                    EquipmentObservation.project_id == entry.project_id,
                    EquipmentObservation.date == entry.date,
                )
            )
        ).scalar_one_or_none()
        merged_counts = (
            [EquipmentCount(equipment_class=cls, count=n) for cls, n in json.loads(obs.counts).items()]
            if obs is not None
            else []
        )
        entry.equipment_summary = _summarize_equipment(merged_counts)
        plan = await _load_plan(db, entry.project_id)
        history = await _load_equipment_history(db, entry.project_id)
        as_of_date = entry.date
        visual_history = await _load_visual_history(db, entry.project_id, as_of_date)
        await db.commit()

    command = PhaseCommand(
        plan_stages=plan.phases if plan else [],
        history=history,
        as_of_date=as_of_date,
        visual_history=visual_history,
    )
    body = _envelope(entry_id, command).model_dump_json().encode()
    await broker.publish(broker.PHASE_COMMAND, body)


async def handle_visual_phase_result(envelope: Envelope[VisualPhaseResult]) -> None:
    """Persists services/visual_phase's result on the journal entry it was
    computed for (`run_entry_analysis` fires exactly one visual_phase.command
    per entry). The full result goes into `visual_phase_details` (JSON) —
    phase probabilities for services/phase's fusion, evidence for the
    expert-facing report — whatever its status: a `failed` result is stored
    too, since its arrival is what `_maybe_advance_entry` waits for. The
    phase name/confidence columns are only filled on success.

    A result arriving after the entry already moved on (timed out, or
    re-analysis started) is still stored — it then feeds the visual history
    of the project's later entries — but only an entry still in analysis
    gets advanced (`_maybe_advance_entry` checks that itself)."""
    entry_id = envelope.correlation_id
    result = envelope.payload

    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is None:
            return
        entry.visual_phase_details = result.model_dump_json()
        if result.status == "done" and result.phase_name is not None:
            entry.visual_phase_name = result.phase_name
            entry.visual_phase_confidence = result.confidence
        else:
            log.warning("visual phase failed for entry %s: %s", entry_id, result.error)
        await db.commit()

    await _maybe_advance_entry(entry_id)


async def handle_phase_result(envelope: Envelope[PhaseResult]) -> None:
    """services/phase's answer IS the entry's phase — it already fused the
    equipment and visual signals over the project's history. Persist it and
    run the delay forecast off it."""
    entry_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed" or result.phase_name is None:
        await _mark_entry_failed(entry_id, "phase", result.error)
        return

    phase_name = result.phase_name
    confidence = result.confidence if result.confidence is not None else 1.0
    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is None:
            return
        log.info(
            "entry %s phase: %s %.0f%% (equipment %s, visual %s, %s days since last observation)",
            entry_id,
            phase_name,
            confidence * 100,
            result.equipment_phase,
            result.visual_phase,
            result.days_since_last_observation,
        )
        entry.stage_summary = _summarize_phase(result)
        entry.phase_name = phase_name
        entry.phase_confidence = result.confidence
        plan = await _load_plan(db, entry.project_id)
        planned_start = await _project_planned_start(db, entry.project_id)
        as_of_date = entry.date
        await db.flush()
        phase_started_at = await _current_phase_started_at(db, entry.project_id, phase_name, as_of_date)
        await db.commit()

    if planned_start is None:
        # No plan-stage math possible without a start anchor — same
        # "nothing to forecast from" outcome as an empty plan.
        await handle_delay_result(
            _envelope(entry_id, DelayForecastResult(status="done", confidence=confidence))
        )
        return

    command = DelayForecastCommand(
        plan_stages=plan.phases if plan else [],
        planned_start=planned_start,
        project_duration_days=plan.project_duration_days if plan else None,
        current_phase=phase_name,
        as_of_date=as_of_date,
        phase_confidence=confidence,
        current_phase_started_at=phase_started_at,
    )
    body = _envelope(entry_id, command).model_dump_json().encode()
    await broker.publish(broker.DELAY_COMMAND, body)


async def handle_delay_result(envelope: Envelope[DelayForecastResult]) -> None:
    entry_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed":
        await _mark_entry_failed(entry_id, "delay", result.error)
        return

    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is None:
            return
        entry.stage_summary = _summarize_delay(entry.stage_summary, result)
        entry.delay_days = result.delay_days
        entry.expected_completion = result.expected_completion
        entry.spi_time = result.spi_time
        entry.analysis_status = "ready"
        entry.analyzed_at = datetime.now(timezone.utc)
        facts = await _build_entry_facts(db, entry)
        project_id = entry.project_id
        await db.commit()

    # Block 5 — best-effort, never fails the entry: stage_summary above is
    # already committed and is what the rest of the pipeline actually reads;
    # see report.py's module docstring for why this runs in-process rather
    # than as another broker leg, and asyncio.to_thread for the same reason
    # services/planner's worker offloads its own (synchronous) LLM call.
    narrative = await asyncio.to_thread(report.generate_entry_report, facts)
    async with SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        if entry is not None:
            entry.narrative_report = narrative
            await db.commit()

    await _refresh_plan_status(project_id)


async def start_consumers() -> None:
    """Wire up the backend's five result consumers. Called once from
    main.py's lifespan on startup."""

    async def _plan(body: bytes) -> None:
        await handle_plan_result(Envelope[PlanNormalizeResult].model_validate_json(body))

    async def _vision(body: bytes) -> None:
        await handle_vision_result(Envelope[DetectResult].model_validate_json(body))

    async def _visual_phase(body: bytes) -> None:
        await handle_visual_phase_result(Envelope[VisualPhaseResult].model_validate_json(body))

    async def _phase(body: bytes) -> None:
        await handle_phase_result(Envelope[PhaseResult].model_validate_json(body))

    async def _delay(body: bytes) -> None:
        await handle_delay_result(Envelope[DelayForecastResult].model_validate_json(body))

    await broker.start_consumer(broker.PLAN_RESULT, _plan)
    await broker.start_consumer(broker.VISION_RESULT, _vision)
    await broker.start_consumer(broker.VISUAL_PHASE_RESULT, _visual_phase)
    await broker.start_consumer(broker.PHASE_RESULT, _phase)
    await broker.start_consumer(broker.DELAY_RESULT, _delay)


def schedule_entry_analysis(background: BackgroundTasks, entry_id: uuid.UUID) -> None:
    """Queue a journal entry's analysis to run after the current response is sent."""
    background.add_task(run_entry_analysis, entry_id)


async def requeue_pending() -> None:
    """On startup, resume analyses left in flight by a restart: entries never
    even started (`run_entry_analysis` never ran), individual files still
    mid-vision, and entries whose files are all done but that never (or not
    yet) advanced past "analyzing" — the last case just re-runs
    `_maybe_advance_entry`, which re-publishes phase.command if needed; see
    that function's docstring for why doing so twice is harmless."""
    async with SessionLocal() as db:
        pending_entries = (
            await db.execute(select(JournalEntry.id).where(JournalEntry.analysis_status == "pending"))
        ).scalars().all()
        stuck_assets = (
            await db.execute(
                select(MediaAsset.id).where(
                    MediaAsset.role.in_(("journal_video", "journal_photo")),
                    MediaAsset.analysis_status.in_(("pending", "analyzing")),
                )
            )
        ).scalars().all()
        analyzing_entries = (
            await db.execute(select(JournalEntry.id).where(JournalEntry.analysis_status == "analyzing"))
        ).scalars().all()

    for entry_id in pending_entries:
        asyncio.create_task(run_entry_analysis(entry_id))
    for asset_id in stuck_assets:
        asyncio.create_task(run_analysis(asset_id))
    for entry_id in analyzing_entries:
        asyncio.create_task(_maybe_advance_entry(entry_id))
        # The in-process visual-phase timer died with the old process; if the
        # result is still missing, give it a fresh (full) wait and then move on.
        _spawn(_visual_phase_timeout(entry_id))
