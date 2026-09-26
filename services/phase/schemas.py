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


class PhaseCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    history: list[DailyEquipmentCounts]  # chronological, sparse, gaps expected
    as_of_date: date


class PhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    matched_stage_index: int | None = None
    # Full distribution over canonical phases (phase -> probability) — the
    # backend fuses it with visual_phase's and the project's history
    # (backend/phase_ensemble.py). None from older workers: then the backend
    # falls back to phase_name/confidence alone.
    probs: dict[str, float] | None = None
    error: str | None = None
