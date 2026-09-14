"""Request/response contracts for the internal analysis services.

Three services sit behind the backend orchestrator (see analysis.py):
vision (equipment detection), phase (current project phase) and delay
(schedule-lag forecast). All three are plain REST — the vision service is
long-running so it uses a job-submission + polling shape; phase and delay
are cheap enough to call synchronously. No queue/broker is involved: every
inter-service call in this pipeline goes over HTTP so the integration
pattern stays uniform.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Vision service — equipment detection & tracking (диаграмма: блок 2)
# ---------------------------------------------------------------------------
class DetectRequest(BaseModel):
    asset_id: uuid.UUID
    video_url: str  # {public_base_url}/files/{asset_id} — service fetches it itself
    recorded_at: datetime  # wall-clock moment the video starts, to timestamp events against


class DetectAccepted(BaseModel):
    job_id: str


# MOCS (Moving Objects on Construction Sites) detector classes.
EquipmentClass = Literal[
    "worker",
    "tower_crane",
    "hanging_hook",
    "vehicle_crane",
    "roller",
    "bulldozer",
    "excavator",
    "truck",
    "loader",
    "pump_truck",
    "concrete_mixer",
    "pile_driver",
    "other_vehicle",
]


class EquipmentEvent(BaseModel):
    track_id: int
    equipment_class: EquipmentClass
    event: Literal["arrival", "departure"]
    at: datetime


class DetectJobStatus(BaseModel):
    status: Literal["processing", "done", "failed"]
    events: list[EquipmentEvent] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Shared plan-stage shape (block 1 output, consumed by phase & delay services)
# ---------------------------------------------------------------------------
class PlanStageIn(BaseModel):
    name: str
    planned_start: date
    planned_end: date


# ---------------------------------------------------------------------------
# Phase service — current project phase (диаграмма: блок 3)
# ---------------------------------------------------------------------------
class PhaseRequest(BaseModel):
    plan_stages: list[PlanStageIn]
    events: list[EquipmentEvent]


class PhaseResponse(BaseModel):
    phase_name: str
    confidence: float  # 0..1
    matched_stage_index: int | None = None


# ---------------------------------------------------------------------------
# Delay-forecast service — schedule lag (диаграмма: блок 4)
# ---------------------------------------------------------------------------
class DelayForecastRequest(BaseModel):
    plan_stages: list[PlanStageIn]
    current_phase: str
    as_of_date: date


class DelayForecastResponse(BaseModel):
    delay_days: int  # positive = behind schedule, negative = ahead
    expected_completion: date
    confidence: float | None = None
