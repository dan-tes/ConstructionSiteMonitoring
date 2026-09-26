"""Message contracts this service needs — a deliberate, small copy of the
matching models in backend/integrations/schemas.py (see that file and
services/phase/schemas.py for why: each service ships its own copy rather
than importing across a service boundary).
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


class VisualPhaseCommand(BaseModel):
    asset_id: uuid.UUID
    media_url: str
    kind: Literal["video", "image"]


class VisualPhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    # Which of the 8 unsupervised visual clusters matched, for debugging/
    # auditing a prediction against phase_determination/data/visual_phase_checkpoints/.
    cluster_id: int | None = None
    # Distribution over the phases this classifier knows (phase ->
    # probability); phases it can't see (e.g. Preconstruction) are absent and
    # treated as neutral by the backend's fusion (backend/phase_ensemble.py).
    probs: dict[str, float] | None = None
    error: str | None = None
