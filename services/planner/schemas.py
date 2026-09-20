"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py.

Services are independent deployables (see backend/integrations/README.md):
each ships its own dependencies and its own copy of only the contracts it
uses, rather than importing across a service boundary. Keep this in sync by
hand with the backend copy if the plan-normalize contract changes.
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


# MOCS (Moving Objects on Construction Sites) detector classes — must match
# backend/integrations/schemas.py's EquipmentClass exactly.
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

EQUIPMENT_CLASSES: tuple[str, ...] = (
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
)

# The 10 empirically-derived canonical phases — must match
# backend/integrations/schemas.py's CANONICAL_PHASES/CanonicalPhase exactly.
# Order is significant: index + 1 == PlanPhaseIn.phase_order.
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
)

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
    plan_url: str
    original_name: str


class NormalizedPhase(BaseModel):
    phase: CanonicalPhase
    planned_duration_days: float
    expected_equipment: list[EquipmentClass] = []


class PlanNormalizeResult(BaseModel):
    status: Literal["done", "failed"]
    phases: list[NormalizedPhase] | None = None
    project_duration_days: float | None = None
    error: str | None = None
