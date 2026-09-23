"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py.

Services are independent deployables (see backend/integrations/README.md):
each ships its own dependencies and its own copy of only the contracts it
uses, rather than importing across a service boundary. Keep this in sync by
hand with the backend copy if the delay contract changes.
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


class DelayForecastCommand(BaseModel):
    plan_stages: list[PlanPhaseIn]
    planned_start: date
    project_duration_days: float | None = None
    current_phase: str
    as_of_date: date
    phase_confidence: float = 1.0
    current_phase_started_at: date | None = None


class DelayForecastResult(BaseModel):
    status: Literal["done", "failed"]
    delay_days: int | None = None
    expected_completion: date | None = None
    confidence: float | None = None
    spi_time: float | None = None
    error: str | None = None
