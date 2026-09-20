"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py.

Services are independent deployables (see backend/integrations/README.md):
each ships its own dependencies and its own copy of only the contracts it
uses, rather than importing across a service boundary. Keep this in sync by
hand with the backend copy if the vision contract changes.
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


class DetectCommand(BaseModel):
    asset_id: uuid.UUID
    media_url: str
    kind: Literal["video", "image"]


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