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
    recorded_at: datetime


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

    def __str__(self):
        return f'{self.track_id} : cl: {self.equipment_class}, at {self.at}, {self.event}'

    def __repr__(self):
        return self.__str__()


class DetectResult(BaseModel):
    status: Literal["done", "failed"]
    events: list[EquipmentEvent] | None = None
    error: str | None = None

    def __str__(self):
        return str(self.status) + '\n'.join([str(i) for i in self.events])

    def __repr__(self):
        return self.__str__()