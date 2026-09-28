import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    salt: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Project-level summary generated from the latest analysed video (see analysis.py).
    plan_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The uploaded plan's total critical-path duration (project_network_duration
    # in the canonical format, or the LLM's estimate of the same quantity for a
    # normalized plan — see plan_parser.py / PlanStage / integrations/schemas.py's
    # PlanNormalizeResult). None until a plan has been parsed/normalized.
    plan_duration_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Set once by POST /projects/{id}/close, which also freezes the
    # plan-vs-actual export as a `final_report` asset (see progress.py). A
    # closed project accepts no new journal entries or plan changes.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    entries: Mapped[list["JournalEntry"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="JournalEntry.date.desc(), JournalEntry.created_at.desc()",
    )
    assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    plan_stages: Mapped[list["PlanStage"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="PlanStage.phase_order",
    )
    equipment_observations: Mapped[list["EquipmentObservation"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="EquipmentObservation.date",
    )

    @property
    def plan_asset(self) -> "MediaAsset | None":
        return next((a for a in self.assets if a.role == "plan"), None)

    @property
    def final_report_asset(self) -> "MediaAsset | None":
        return next((a for a in self.assets if a.role == "final_report"), None)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Combined analysis for every file uploaded as part of this entry — one
    # result per upload batch, not one per file. See analysis.py's
    # run_entry_analysis/_maybe_advance_entry: vision (equipment detection)
    # still runs per file, but phase/delay/visual_phase run once the whole
    # entry's files have all reported in.
    analysis_status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending", nullable=False
    )  # pending | analyzing | ready | failed
    stage_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipment_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Visual-phase signal (services/visual_phase), computed once per entry
    # against one representative file — what the photo classifier alone
    # said. The entry's phase_name below is services/phase's fusion of the
    # equipment and visual signals over the project's history.
    visual_phase_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    visual_phase_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Full services/visual_phase result (JSON VisualPhaseResult): phase
    # probabilities that analysis._load_visual_history sends to
    # services/phase, evidence for the expert-facing report. Also the marker
    # that the visual result has arrived — {"status": "done" | "failed" |
    # "timeout", ...}; None while analysis._maybe_advance_entry still waits.
    visual_phase_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The same phase/delay numbers `stage_summary` already renders into one
    # text blob, kept as their own columns too — see report.py/analysis.py's
    # _build_entry_facts: blocks 5/6 (GPT narrative reports) need these as
    # structured facts to ground a report in, not re-parsed out of prose.
    phase_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phase_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_completion: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Effective SPI(t) behind delay_days — see DelayForecastResult.spi_time.
    spi_time: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Block 5 (диаграмма: блок 5) — a short GPT narrative for this entry,
    # grounded in the structured facts above plus each file's own equipment
    # findings (MediaAsset.equipment_counts), with markdown links back to the
    # specific photo/video that supports a given claim (e.g. "no equipment
    # visible in this photo, hence the delay") — see report.py. Best-effort:
    # None if no YANDEX_CLOUD_* key is configured, or if the call fails; a
    # missing narrative never fails the entry itself, only stage_summary is
    # load-bearing for the pipeline.
    narrative_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="entries")
    media: Mapped[list["MediaAsset"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="MediaAsset.created_at",
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null for the project plan; set for journal videos and (later) extracted frames.
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Source video for an extracted frame; unused until frame extraction lands.
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # plan | journal_video | journal_photo | frame | final_report
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # image | video | other
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # This file's OWN status: for role='plan', the plan-normalization step
    # (see analysis.py's handle_plan_result); for role in
    # 'journal_video'/'journal_photo', just whether ITS OWN vision (equipment
    # detection) step has finished — the combined phase/delay/visual_phase
    # result for the whole entry this file belongs to lives on
    # JournalEntry, not here (see analysis.py's run_entry_analysis).
    analysis_status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending", nullable=False
    )  # pending | analyzing | ready | failed

    # This file's OWN equipment counts (JSON-encoded {equipment_class:
    # count}), set for journal_video/journal_photo roles once vision.result
    # comes back — see analysis.py's handle_vision_result. Kept alongside
    # (not instead of) the project's day-merged EquipmentObservation: that
    # table answers "how much of class X did the project have on date D"
    # (max across same-day files, per-file granularity lost), while this
    # column is what lets the block-5 narrative report cite a *specific*
    # photo/video's own findings (see report.py's FileEvidence).
    equipment_counts: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="assets")
    entry: Mapped["JournalEntry | None"] = relationship(back_populates="media")
    # NOTE: extracted frames (role == 'frame', source_asset_id set) are not
    # produced yet; no relationship until frame extraction is implemented.


class PlanStage(Base):
    """One canonical phase's planned duration for a project — block 1's
    single stored output, regardless of whether it came from parsing an
    already-canonical workbook (plan_parser.py) or from the LLM fallback
    (integrations/schemas.py's PlanNormalizeResult, see analysis.py's
    handle_plan_result). Read fresh from the uploaded file used to happen on
    every analysis step (see analysis.py's old _load_plan); now parsed/
    normalized once, at upload time, and persisted here instead."""

    __tablename__ = "plan_stages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(String(100), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_duration_days: Mapped[float] = mapped_column(Float, nullable=False)
    # JSON-encoded list of EquipmentClass strings. Always "[]" for a
    # canonical-workbook plan — plan_parser.py doesn't extract per-phase
    # equipment today (see its module docstring) — populated for an
    # LLM-normalized plan (NormalizedPhase.expected_equipment).
    expected_equipment: Mapped[str] = mapped_column(Text, default="[]", server_default="[]", nullable=False)

    project: Mapped[Project] = relationship(back_populates="plan_stages")


class EquipmentObservation(Base):
    """One project's aggregated equipment counts for one calendar day —
    merged (max per class) across every video/photo analysed that day, see
    analysis.py's handle_vision_result. Exists so the phase model
    (services/phase) gets a real multi-day sequence at inference instead of
    a single-timestep observation: it was trained on WINDOW_SIZE=128
    consecutive daily observations per project (see
    phase_determination/construction_phase_training.ipynb's
    build_project_timeline()), and feeding it a length-1 sequence turned out
    to produce systematically unreliable predictions — see
    services/phase/worker.py's module docstring."""

    __tablename__ = "equipment_observations"
    __table_args__ = (
        UniqueConstraint("project_id", "date", name="uq_equipment_observations_project_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # JSON-encoded {equipment_class: count}.
    counts: Mapped[str] = mapped_column(Text, default="{}", server_default="{}", nullable=False)

    project: Mapped[Project] = relationship(back_populates="equipment_observations")
