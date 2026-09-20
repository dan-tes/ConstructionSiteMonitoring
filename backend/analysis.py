"""Video-analysis pipeline — orchestrated over RabbitMQ.

`run_analysis()` no longer does the analysis itself: it publishes a
`vision.command` message and returns. The rest of the pipeline is a
choreography driven by result messages (see integrations/README.md for the
full topology): each `handle_*_result` below is registered as a consumer
(see `start_consumers()`, wired up from `main.py`) and, on success, persists
its step's output and publishes the next command. The final step
(`handle_delay_result`) marks the asset `ready`.

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

from config import settings
from database import SessionLocal
from integrations import broker
from integrations.schemas import (
    CANONICAL_PHASES,
    DailyEquipmentCounts,
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
)
from models import EquipmentObservation, JournalEntry, MediaAsset, PlanStage, Project
from plan_parser import ParsedPlan

# How many days of a project's equipment-observation history to feed the
# phase model — see services/phase/worker.py's WINDOW_SIZE (128) docstring;
# a little more than that so its forward-fill has something to look back on
# even near the start of the requested window.
EQUIPMENT_HISTORY_DAYS = 200

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
    return f"Текущая фаза объекта: {result.phase_name} (уверенность {confidence_pct}%)."


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
    services/phase/worker.py."""
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


async def _project_planned_start(db, project_id: uuid.UUID) -> date | None:
    """Project start anchor for the delay forecast. The canonical plan
    carries no calendar dates (see plan_parser.py), so this is the earliest
    journal entry date recorded for the project instead — a project with no
    entries yet (shouldn't happen mid-analysis, since analysing a video
    means its entry already exists) has no anchor to forecast from."""
    return (
        await db.execute(
            select(func.min(JournalEntry.date)).where(JournalEntry.project_id == project_id)
        )
    ).scalar_one_or_none()


async def _mark_failed(asset_id: uuid.UUID, step: str, error: str | None) -> None:
    log.error("analysis step %r failed for asset %s: %s", step, asset_id, error)
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is not None:
            asset.analysis_status = "failed"
            await db.commit()


async def _refresh_plan_status(db, project_id: uuid.UUID) -> None:
    """Project-level summary = stage summary of the most recently analysed
    video or photo."""
    latest = (
        await db.execute(
            select(MediaAsset)
            .where(
                MediaAsset.project_id == project_id,
                MediaAsset.role.in_(("journal_video", "journal_photo")),
                MediaAsset.analysis_status == "ready",
            )
            .order_by(MediaAsset.analyzed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    project = await db.get(Project, project_id)
    if project is not None:
        project.plan_status = latest.stage_summary if latest else None


async def handle_plan_result(envelope: Envelope[PlanNormalizeResult]) -> None:
    """Block 1's LLM fallback finishing: persist the normalized phases the
    same way the canonical-workbook fast path does (see `upload_plan` in
    routers/projects.py), so `_load_plan` never needs to know which path a
    given project's plan took."""
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed" or not result.phases or result.project_duration_days is None:
        await _mark_failed(asset_id, "plan", result.error)
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
    """Kick off one journal video's or photo's analysis: mark it in-flight and
    publish the first pipeline command. The rest happens in handle_*_result
    below, driven by messages coming back from the vision/phase/delay
    services."""
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


async def handle_vision_result(envelope: Envelope[DetectResult]) -> None:
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed":
        await _mark_failed(asset_id, "vision", result.error)
        return

    counts = result.counts or []
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None:
            return
        asset.equipment_summary = _summarize_equipment(counts)
        entry = await db.get(JournalEntry, asset.entry_id) if asset.entry_id else None
        # Which calendar day this video/photo's equipment counts belong to —
        # shouldn't be missing (analysing a video means its entry already
        # exists), but falls back to today rather than raising if it is.
        obs_date = entry.date if entry is not None else date.today()
        await _upsert_equipment_observation(db, asset.project_id, obs_date, counts)
        plan = await _load_plan(db, asset.project_id)
        history = await _load_equipment_history(db, asset.project_id)
        await db.commit()

    command = PhaseCommand(
        plan_stages=plan.phases if plan else [], history=history, as_of_date=obs_date
    )
    body = _envelope(asset_id, command).model_dump_json().encode()
    await broker.publish(broker.PHASE_COMMAND, body)


async def handle_phase_result(envelope: Envelope[PhaseResult]) -> None:
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed" or result.phase_name is None:
        await _mark_failed(asset_id, "phase", result.error)
        return

    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None:
            return
        asset.stage_summary = _summarize_phase(result)
        plan = await _load_plan(db, asset.project_id)
        planned_start = await _project_planned_start(db, asset.project_id)
        entry = await db.get(JournalEntry, asset.entry_id) if asset.entry_id else None
        await db.commit()

    if planned_start is None:
        # No plan-stage math possible without a start anchor — same
        # "nothing to forecast from" outcome as an empty plan.
        await handle_delay_result(
            _envelope(asset_id, DelayForecastResult(status="done", confidence=result.confidence))
        )
        return

    command = DelayForecastCommand(
        plan_stages=plan.phases if plan else [],
        planned_start=planned_start,
        project_duration_days=plan.project_duration_days if plan else None,
        current_phase=result.phase_name,
        as_of_date=entry.date if entry is not None else date.today(),
        phase_confidence=result.confidence if result.confidence is not None else 1.0,
    )
    body = _envelope(asset_id, command).model_dump_json().encode()
    await broker.publish(broker.DELAY_COMMAND, body)


async def handle_delay_result(envelope: Envelope[DelayForecastResult]) -> None:
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed":
        await _mark_failed(asset_id, "delay", result.error)
        return

    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None:
            return
        asset.stage_summary = _summarize_delay(asset.stage_summary, result)
        asset.analysis_status = "ready"
        asset.analyzed_at = datetime.now(timezone.utc)
        await _refresh_plan_status(db, asset.project_id)
        await db.commit()


async def start_consumers() -> None:
    """Wire up the backend's four result consumers. Called once from
    main.py's lifespan on startup."""

    async def _plan(body: bytes) -> None:
        await handle_plan_result(Envelope[PlanNormalizeResult].model_validate_json(body))

    async def _vision(body: bytes) -> None:
        await handle_vision_result(Envelope[DetectResult].model_validate_json(body))

    async def _phase(body: bytes) -> None:
        await handle_phase_result(Envelope[PhaseResult].model_validate_json(body))

    async def _delay(body: bytes) -> None:
        await handle_delay_result(Envelope[DelayForecastResult].model_validate_json(body))

    await broker.start_consumer(broker.PLAN_RESULT, _plan)
    await broker.start_consumer(broker.VISION_RESULT, _vision)
    await broker.start_consumer(broker.PHASE_RESULT, _phase)
    await broker.start_consumer(broker.DELAY_RESULT, _delay)


def schedule_analysis(background: BackgroundTasks, asset_id: uuid.UUID) -> None:
    """Queue analysis to run after the current response is sent."""
    background.add_task(run_analysis, asset_id)


async def requeue_pending() -> None:
    """On startup, resume analyses left `pending`/`analyzing` by a restart."""
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(MediaAsset.id).where(
                    MediaAsset.role.in_(("journal_video", "journal_photo")),
                    MediaAsset.analysis_status.in_(("pending", "analyzing")),
                )
            )
        ).scalars().all()
    for asset_id in rows:
        asyncio.create_task(run_analysis(asset_id))
