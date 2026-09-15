"""Video-analysis pipeline — orchestrated over RabbitMQ.

`run_analysis()` no longer does the analysis itself: it publishes a
`vision.command` message and returns. The rest of the pipeline is a
choreography driven by result messages (see integrations/README.md for the
full topology): each `handle_*_result` below is registered as a consumer
(see `start_consumers()`, wired up from `main.py`) and, on success, persists
its step's output and publishes the next command. The final step
(`handle_delay_result`) marks the asset `ready`.

`plan_stages` comes from the project's uploaded canonical plan workbook, read
fresh from disk on each step (see `_load_plan` / `plan_parser.py`) —
block 1 parses the real canonical format (phase_determination/data/
Требования_к_каноническому_формату_плана_v2.docx), but only the "ideal,
already-clean workbook" case, not arbitrary plan documents. No plan uploaded
(or it doesn't parse) just means phase/delay run with an empty schedule. The
canonical format carries no calendar dates, so `planned_start` (the delay
forecast's one date anchor) comes from the project's earliest journal entry
instead — see `_project_planned_start`.

vision/phase/delay (see `services/*/worker.py`) run real models now: YOLOv8m
+ ByteTrack for vision, the trained ConstructionPhaseModel for phase, the
Earned-Schedule forecast for delay — see each worker's module docstring for
what's faithful to training and what's a live-inference simplification.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

from config import settings
from database import SessionLocal
from integrations import broker
from integrations.schemas import (
    DelayForecastCommand,
    DelayForecastResult,
    DetectCommand,
    DetectResult,
    Envelope,
    EquipmentEvent,
    PhaseCommand,
    PhaseResult,
)
from models import JournalEntry, MediaAsset, Project
from plan_parser import ParsedPlan, PlanParseError, parse_plan_workbook

log = logging.getLogger("csm.analysis")


def _envelope(correlation_id: uuid.UUID, payload) -> Envelope:
    return Envelope(
        correlation_id=correlation_id, published_at=datetime.now(timezone.utc), payload=payload
    )


def _summarize_equipment(events: list[EquipmentEvent]) -> str:
    if not events:
        return "Техника в кадре не обнаружена."
    classes = sorted({e.equipment_class for e in events})
    return f"В кадре обнаружена техника ({len(events)} событий): {', '.join(classes)}."


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
    """Block 1 stand-in: read the project's uploaded canonical plan workbook,
    if any. See plan_parser.py — assumes an ideal, already-clean workbook; a
    missing plan or one that doesn't parse just means phase/delay run with
    an empty schedule (None, not raised — a bad plan isn't an analysis
    failure)."""
    plan_asset = (
        await db.execute(
            select(MediaAsset).where(
                MediaAsset.project_id == project_id, MediaAsset.role == "plan"
            )
        )
    ).scalar_one_or_none()
    if plan_asset is None:
        return None
    if not plan_asset.original_name.lower().endswith(".xlsx"):
        log.info("plan asset %s isn't an .xlsx workbook, skipping plan_stages", plan_asset.id)
        return None
    try:
        return parse_plan_workbook(settings.media_root / plan_asset.storage_path)
    except PlanParseError:
        log.exception("failed to parse plan workbook for project %s", project_id)
        return None


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
    """Project-level summary = stage summary of the most recently analysed video."""
    latest = (
        await db.execute(
            select(MediaAsset)
            .where(
                MediaAsset.project_id == project_id,
                MediaAsset.role == "journal_video",
                MediaAsset.analysis_status == "ready",
            )
            .order_by(MediaAsset.analyzed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    project = await db.get(Project, project_id)
    if project is not None:
        project.plan_status = latest.stage_summary if latest else None


async def run_analysis(asset_id: uuid.UUID) -> None:
    """Kick off one journal video's analysis: mark it in-flight and publish
    the first pipeline command. The rest happens in handle_*_result below,
    driven by messages coming back from the vision/phase/delay services."""
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None or asset.role != "journal_video":
            return
        asset.analysis_status = "analyzing"
        await db.commit()

    command = DetectCommand(
        asset_id=asset_id,
        video_url=f"{settings.internal_url}/files/{asset_id}",
        recorded_at=datetime.now(timezone.utc),
    )
    body = _envelope(asset_id, command).model_dump_json().encode()
    await broker.publish(broker.VISION_COMMAND, body)


async def handle_vision_result(envelope: Envelope[DetectResult]) -> None:
    asset_id = envelope.correlation_id
    result = envelope.payload
    if result.status == "failed":
        await _mark_failed(asset_id, "vision", result.error)
        return

    events = result.events or []
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None:
            return
        asset.equipment_summary = _summarize_equipment(events)
        plan = await _load_plan(db, asset.project_id)
        await db.commit()

    command = PhaseCommand(plan_stages=plan.phases if plan else [], events=events)
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
    """Wire up the backend's three result consumers. Called once from
    main.py's lifespan on startup."""

    async def _vision(body: bytes) -> None:
        await handle_vision_result(Envelope[DetectResult].model_validate_json(body))

    async def _phase(body: bytes) -> None:
        await handle_phase_result(Envelope[PhaseResult].model_validate_json(body))

    async def _delay(body: bytes) -> None:
        await handle_delay_result(Envelope[DelayForecastResult].model_validate_json(body))

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
                    MediaAsset.role == "journal_video",
                    MediaAsset.analysis_status.in_(("pending", "analyzing")),
                )
            )
        ).scalars().all()
    for asset_id in rows:
        asyncio.create_task(run_analysis(asset_id))
