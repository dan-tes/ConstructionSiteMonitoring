"""Message contracts for the internal analysis pipeline.

Five services sit behind the backend orchestrator (see analysis.py):
planner (plan normalization), vision (equipment detection), visual_phase
(phase read directly off a photo, experimental/secondary), phase (current
project phase) and delay (schedule-lag forecast). All inter-service
communication goes over a single RabbitMQ exchange — no direct HTTP calls
between backend and services, for any step, including the fast ones. See
../integrations/README.md for why.

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
    media_url: str  # {public_base_url}/files/{asset_id} — service fetches it itself
    kind: Literal["video", "image"]  # how to run detection — track a video, or a single frame


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


class EquipmentCount(BaseModel):
    equipment_class: EquipmentClass
    count: int


class DetectResult(BaseModel):
    status: Literal["done", "failed"]
    counts: list[EquipmentCount] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Visual phase — construction phase read directly off a photo, no equipment
# detection in between (extra leg alongside block 3; see services/visual_phase
# and backend/integrations/README.md for why this is a separate, additional
# signal rather than a replacement for the equipment-based `phase` service).
# routing keys: visual_phase.command / visual_phase.result
# ---------------------------------------------------------------------------
class VisualPhaseCommand(BaseModel):
    asset_id: uuid.UUID
    media_url: str
    kind: Literal["video", "image"]


class VisualPhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    cluster_id: int | None = None
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
# Plan normalization — LLM fallback for block 1 (диаграмма: блок 1).
# routing keys: plan.command / plan.result
#
# plan_parser.py only accepts an already-canonical workbook (exact 21-column
# header) — most uploaded plans (an MS Project/Primavera/Excel export with
# its own column names and work-item names) aren't in that shape. This is
# the fallback for those: services/planner/plan_normalizer.py classifies the
# free-form plan activity-by-activity onto a closed 43-activity vocabulary
# (services/planner/data/canonical_plan.xlsx — activity → phase → expected
# equipment, an empirical reference instead of letting the model invent
# names), then aggregates onto the 10 canonical phases below in code.
# ---------------------------------------------------------------------------
CANONICAL_PHASES: tuple[str, ...] = (
    "Preconstruction",
    "Site Preparation",
    "Earthwork",
    "Foundation",
    "Structural Frame",
    "Masonry",
    "MEP",
    "Finishing",
    "External Works",
    "Commissioning",
)  # order is significant: index + 1 == PlanPhaseIn.phase_order. Keep in sync
   # by hand with the CanonicalPhase Literal directly below and with
   # services/planner/schemas.py's own copy of both.

CanonicalPhase = Literal[
    "Preconstruction",
    "Site Preparation",
    "Earthwork",
    "Foundation",
    "Structural Frame",
    "Masonry",
    "MEP",
    "Finishing",
    "External Works",
    "Commissioning",
]


class PlanNormalizeCommand(BaseModel):
    asset_id: uuid.UUID
    plan_url: str  # {internal_url}/files/{asset_id} — service fetches it itself
    original_name: str  # extension hint; plan_url alone carries none


class NormalizedPhase(BaseModel):
    phase: CanonicalPhase
    planned_duration_days: float
    expected_equipment: list[EquipmentClass] = []


class PlanNormalizeResult(BaseModel):
    status: Literal["done", "failed"]
    phases: list[NormalizedPhase] | None = None
    # Total project critical-path duration — the LLM's estimate of the same
    # quantity plan_parser.py reads straight off project_network_duration in
    # a canonical workbook. None only when status is "failed".
    project_duration_days: float | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Phase — current project phase (диаграмма: блок 3)
# routing keys: phase.command / phase.result
#
# The phase model was trained on WINDOW_SIZE=128 consecutive *daily*
# equipment observations per project (see
# phase_determination/construction_phase_training.ipynb's
# build_project_timeline()) — a single-timestep observation is out of that
# distribution and was found to produce systematically unreliable
# predictions (see services/phase/worker.py's module docstring). `history`
# carries the project's real day-by-day equipment counts instead — sparse
# (only days with an analysed video/photo; a project doesn't get filmed
# every day) and NOT necessarily consecutive, so the phase service anchors
# each entry on its own `date` and fills the gaps itself rather than
# assuming the list's positions line up with calendar days.
# ---------------------------------------------------------------------------
class DailyEquipmentCounts(BaseModel):
    date: date
    counts: list[EquipmentCount]


class PhaseCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    history: list[DailyEquipmentCounts]  # chronological, sparse — see above
    as_of_date: date  # which day to predict the phase "as of" (the latest entry's date)


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
