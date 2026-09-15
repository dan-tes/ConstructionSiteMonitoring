"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py.

Services are independent deployables (see backend/integrations/README.md):
each ships its own dependencies and its own copy of only the contracts it
uses, rather than importing across a service boundary. Keep this in sync by
hand with the backend copy if the phase contract changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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


class EquipmentEvent(BaseModel):
    track_id: int
    equipment_class: EquipmentClass
    event: Literal["arrival", "departure"]
    at: datetime


class PhaseCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    events: list[EquipmentEvent]


class PhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    matched_stage_index: int | None = None
    error: str | None = None
