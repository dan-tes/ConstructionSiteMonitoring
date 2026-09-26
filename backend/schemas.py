from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Serialises to camelCase so responses match the frontend's TypeScript types."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class SaltRequest(BaseModel):
    username: str


class SaltResponse(BaseModel):
    salt: str


class RegisterRequest(BaseModel):
    username: str
    password_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    salt: str = Field(pattern=r"^[0-9a-f]{32}$")

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LoginRequest(BaseModel):
    username: str
    password_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UserOut(CamelModel):
    id: uuid.UUID
    username: str
    created_at: datetime


class SessionOut(CamelModel):
    token: str
    user: UserOut
    expires_at: datetime


# ---------------------------------------------------------------------------
# Projects & journal
# ---------------------------------------------------------------------------
class ProjectFileOut(CamelModel):
    id: str
    name: str
    type: str
    size: int
    url: str
    kind: str
    # Only ever set for the project plan asset (its own normalization
    # status) — a journal video/photo has no insight of its own any more,
    # see JournalEntryOut.insight: analysis is combined per upload batch
    # (entry), not per individual file.
    insight: VideoInsightOut | None = None


class VideoInsightOut(CamelModel):
    """Backend neural-network analysis: either a plan's own normalization
    status, or (via JournalEntryOut.insight) a journal entry's combined
    result across every file uploaded in that batch."""

    status: str  # pending | analyzing | ready | failed
    stage_summary: str | None = None
    equipment_summary: str | None = None
    # Block 5 (see backend/report.py) — a short GPT narrative grounded in
    # this entry's phase/delay facts and each file's own equipment findings,
    # with markdown links (`[name](url)`) back to the specific photo/video
    # supporting a given claim. None if narrative generation is unavailable
    # (no YANDEX_CLOUD_* key configured) or failed — stage_summary/
    # equipment_summary above are still the load-bearing fields.
    narrative_report: str | None = None
    photos: list[ProjectFileOut] = []
    # What services/visual_phase alone said about the photo (the entry's
    # phase_name is already the ensemble of this and the equipment-based
    # phase — backend/phase_ensemble.py), computed once per entry against one
    # representative file — arrives
    # independently of `status` (see analysis.py's
    # handle_visual_phase_result), so this can be set even while `status` is
    # still "analyzing", or stay unset once "ready" if that service failed or
    # didn't reply within analysis.VISUAL_PHASE_WAIT_SECONDS. Covers 9 of the 10 canonical phases (no Preconstruction)
    # — see that service's module docstring.
    visual_phase_name: str | None = None
    visual_phase_confidence: float | None = None


ProjectFileOut.model_rebuild()


class JournalEntryOut(CamelModel):
    id: uuid.UUID
    comment: str
    author: str
    date: date_type
    media: list[ProjectFileOut] = []
    # Combined analysis for every file in `media` together — see
    # analysis.py's run_entry_analysis/_maybe_advance_entry.
    insight: VideoInsightOut | None = None


class ProjectOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str
    plan: ProjectFileOut | None = None
    plan_status: str | None = None
    entries: list[JournalEntryOut] = []
    created_at: datetime
    closed_at: datetime | None = None
    # Plan-vs-actual workbook frozen at close time (see progress.py).
    final_report: ProjectFileOut | None = None


class TimelinePhaseOut(CamelModel):
    phase: str
    phase_order: int
    status: str  # Completed | In Progress | Planned
    planned_start: date_type | None = None
    planned_end: date_type | None = None
    first_detected: date_type | None = None
    last_detected: date_type | None = None
    estimated_start: date_type | None = None


class TimelinePointOut(CamelModel):
    """One analysed journal entry — one point on each delay chart."""

    entry_id: uuid.UUID
    date: date_type
    phase: str
    phase_confidence: float | None = None
    delay_days: int | None = None
    expected_completion: date_type | None = None
    spi_time: float | None = None


class ProjectTimelineOut(CamelModel):
    """Data for the project page's delay charts (see progress.py). planned_*
    are None until the project has both a processed plan and at least one
    journal entry to anchor it on the calendar."""

    planned_start: date_type | None = None
    planned_finish: date_type | None = None
    phases: list[TimelinePhaseOut] = []
    points: list[TimelinePointOut] = []


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


# Journal entries are created via multipart/form-data (video files + author +
# date), so their fields are parsed with fastapi.Form in the router rather than
# a JSON body model.
