"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py.

Services are independent deployables (see backend/integrations/README.md):
each ships its own dependencies and its own copy of only the contracts it
uses, rather than importing across a service boundary. Keep this in sync by
hand with the backend copy if the phase contract changes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class Envelope(BaseModel, Generic[PayloadT]):
    correlation_id: uuid.UUID
    published_at: datetime
    payload: PayloadT


class PlanPhaseIn(BaseModel):
    phase: str
    phase_order: int
    planned_duration_days: float


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


class DailyEquipmentCounts(BaseModel):
    date: date
    counts: list[EquipmentCount]


class DailyVisualPhase(BaseModel):
    """One day's visual phase reading (services/visual_phase result of a
    journal entry dated `date`). Several entries on one day are averaged."""
    date: date
    phase_probs: dict[str, float]


class PhaseCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    history: list[DailyEquipmentCounts]  # chronological, sparse, gaps expected
    as_of_date: date
    # Optional — an older backend that doesn't send it just gets the
    # equipment-only result (smoothed over history, see fusion.py).
    visual_history: list[DailyVisualPhase] = []


class PhaseScore(BaseModel):
    phase: str
    prob: float


class PhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    matched_stage_index: int | None = None
    # All optional — older consumers ignore them:
    phase_probs: dict[str, float] | None = None  # fused posterior (fusion.py)
    top_phases: list[PhaseScore] | None = None
    equipment_phase: str | None = None   # what the equipment model alone says on its latest real day
    visual_phase: str | None = None      # what the latest visual reading alone says
    equipment_days: int | None = None    # days in the window with real equipment observations
    visual_days: int | None = None       # days in the window with visual readings
    # How stale the estimate is: the phase is NOT advanced past the last
    # observation (that would silently assume on-schedule progress).
    days_since_last_observation: int | None = None
    error: str | None = None
