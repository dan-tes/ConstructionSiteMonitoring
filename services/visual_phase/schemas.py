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


class PhaseScore(BaseModel):
    phase: str
    prob: float


class EvidenceItem(BaseModel):
    concept: str
    label: str
    activation: float
    weight: float


class VisualPhaseResult(BaseModel):
    status: Literal["done", "failed"]
    phase_name: str | None = None
    confidence: float | None = None
    # Distribution over the phases this classifier knows (phase ->
    # probability) — services/phase fuses it over the project's history.
    phase_probs: dict[str, float] | None = None
    top_phases: list[PhaseScore] | None = None
    # Concepts behind the answer — only a concept-based classifier fills it;
    # None from this one (see worker.py).
    evidence: list[EvidenceItem] | None = None
    method_phases: dict[str, str] | None = None  # what each ensemble member alone said
    frames_used: int | None = None
    model_version: str | None = None
    cluster_id: int | None = None  # k-means classifier only
    error: str | None = None
