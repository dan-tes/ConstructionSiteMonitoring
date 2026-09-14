"""Video-analysis pipeline.

Right now this is a **stub**: after a configurable delay it writes placeholder
"neural network" text (which construction stage the footage shows relative to the
plan, and what machinery is in frame) and marks the asset ready.

To plug in a real model/RAG service, replace `_analyze()` — keep its return
shape (`stage_summary`, `equipment_summary`). Everything else (status
transitions, project summary refresh, error handling) stays.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

from config import settings
from database import SessionLocal
from models import MediaAsset, Project

log = logging.getLogger("csm.analysis")

_STAGE_TEMPLATE = (
    "На видео зафиксирована активная фаза возведения монолитного каркаса. "
    "Согласно плану объект находится на этапе «Надземная часть, 1–3 этаж»: "
    "опалубка и армирование колонн, бетонирование перекрытия. Нулевой цикл на "
    "кадрах завершён, кладочные и фасадные работы не просматриваются. "
    "Последовательность работ соответствует плановому графику для этой стадии."
)
_EQUIPMENT_TEMPLATE = (
    "В кадре обнаружено 5 единиц техники: башенный кран, автобетононасос, два "
    "самосвала и фронтальный погрузчик. Кран подаёт арматурные каркасы, "
    "автобетононасос занят на бетонировании перекрытия. Простаивающей техники "
    "не отмечено, плотность работающей техники для этой стадии в пределах нормы."
)


async def _analyze(asset: MediaAsset) -> tuple[str, str]:
    """EXTENSION POINT — swap for a real ML/RAG call. Returns
    (stage_summary, equipment_summary)."""
    return _STAGE_TEMPLATE, _EQUIPMENT_TEMPLATE


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
    """Background task: analyse one journal video end to end."""
    async with SessionLocal() as db:
        asset = await db.get(MediaAsset, asset_id)
        if asset is None or asset.role != "journal_video":
            return
        asset.analysis_status = "analyzing"
        await db.commit()

        try:
            if settings.analysis_delay_seconds > 0:
                await asyncio.sleep(settings.analysis_delay_seconds)
            stage, equipment = await _analyze(asset)

            asset.stage_summary = stage
            asset.equipment_summary = equipment
            asset.analysis_status = "ready"
            asset.analyzed_at = datetime.now(timezone.utc)
            await _refresh_plan_status(db, asset.project_id)
            await db.commit()
        except Exception:
            log.exception("analysis failed for asset %s", asset_id)
            await db.rollback()
            failed = await db.get(MediaAsset, asset_id)
            if failed is not None:
                failed.analysis_status = "failed"
                await db.commit()


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
