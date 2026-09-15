"""Message contracts for the internal analysis pipeline.

Three services sit behind the backend orchestrator (see analysis.py):
vision (equipment detection), phase (current project phase) and delay
(schedule-lag forecast). All inter-service communication goes over a single
RabbitMQ exchange — no direct HTTP calls between backend and services, for
any step, including the fast ones. See ../integrations/README.md for why.

Every message on the bus is an `Envelope[T]`: a thin wrapper carrying a
`correlation_id` (the `MediaAsset.id` the message belongs to) around one of
the payload models below. Commands go out on `*.command`; results come back
on `*.result` with `status` telling the backend whether to advance the
pipeline or mark the asset failed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Envelope — wraps every message published to csm.analysis
# ---------------------------------------------------------------------------
PayloadT = TypeVar("PayloadT", bound=BaseModel)


class Envelope(BaseModel, Generic[PayloadT]):
    correlation_id: uuid.UUID  # == MediaAsset.id for this analysis run
    published_at: datetime
    payload: PayloadT


# ---------------------------------------------------------------------------
# Vision — equipment detection & tracking (диаграмма: блок 2)
# routing keys: vision.command / vision.result
# ---------------------------------------------------------------------------
class DetectCommand(BaseModel):
    asset_id: uuid.UUID
    video_url: str  # {public_base_url}/files/{asset_id} — service fetches it itself
    recorded_at: datetime  # wall-clock moment the video starts, to timestamp events against


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


class DetectResult(BaseModel):
    status: Literal["done", "failed"]
    events: list[EquipmentEvent] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Shared plan-phase shape (block 1 output, consumed by phase & delay).
#
# Matches the canonical plan format (see
# phase_determination/data/Требования_к_каноническому_формату_плана_v2.docx
# and plan_parser.py): phase-level durations only, no calendar dates — the
# canonical activities table has planned_duration_days per activity, not
# planned_start/planned_end, so `planned_duration_days` here is the *sum*
# across that phase's activities, and offset from project start is derived
# by whoever consumes this list (sort by phase_order, running sum of
# earlier phases' durations) rather than carried per row.
# ---------------------------------------------------------------------------
class PlanPhaseIn(BaseModel):
    phase: str
    phase_order: int
    planned_duration_days: float


# ---------------------------------------------------------------------------
# Phase — current project phase (диаграмма: блок 3)
# routing keys: phase.command / phase.result
# ---------------------------------------------------------------------------
class PhaseCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    events: list[EquipmentEvent]


class PhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None  # 0..1
    matched_stage_index: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Delay forecast — schedule lag (диаграмма: блок 4)
# routing keys: delay.command / delay.result
# ---------------------------------------------------------------------------
class DelayForecastCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    # Project start anchor. Not part of the canonical plan (it's durations-
    # only, see PlanPhaseIn) — sourced from the project's earliest journal
    # entry date; see analysis.py's _project_planned_start.
    planned_start: date
    # The canonical plan's project_network_duration (real critical-path
    # timeline) — NOT sum(plan_stages.planned_duration_days), which
    # overcounts whenever a phase's activities run in parallel. None only
    # when there's no plan at all (plan_stages is then also empty).
    project_duration_days: float | None = None
    current_phase: str
    as_of_date: date
    phase_confidence: float = 1.0  # module 3's confidence in current_phase, 0..1


class DelayForecastResult(BaseModel):
    status: Literal["done", "failed"]
    delay_days: int | None = None  # positive = behind schedule, negative = ahead
    expected_completion: date | None = None
    confidence: float | None = None
    error: str | None = None
