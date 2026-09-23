import logging
import uuid
from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from analysis import schedule_entry_analysis
from config import settings
from deps import CurrentUser, DbSession
from integrations import broker
from integrations.schemas import Envelope, PlanNormalizeCommand
from media import (
    asset_to_file_out,
    entry_to_out,
    guess_kind,
    is_allowed_plan,
    project_to_out,
    save_upload,
    unlink_asset_files,
)
from models import JournalEntry, MediaAsset, PlanStage, Project, User
from plan_parser import PlanParseError, parse_plan_workbook
from progress import analysed_entries, build_progress_workbook, phase_actuals, planned_start_of
from schemas import (
    JournalEntryOut,
    ProjectCreate,
    ProjectTimelineOut,
    TimelinePhaseOut,
    TimelinePointOut,
    ProjectFileOut,
    ProjectOut,
    ProjectUpdate,
)

log = logging.getLogger("csm.projects")

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_FILES_PER_ENTRY = 20


async def _get_owned_project(db: DbSession, user: User, project_id: uuid.UUID) -> Project:
    project = (
        await db.execute(
            select(Project)
            .where(Project.id == project_id, Project.owner_id == user.id)
            .options(
                selectinload(Project.assets),
                selectinload(Project.entries).selectinload(JournalEntry.media),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
    return project


def _ensure_open(project: Project) -> None:
    if project.closed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Объект закрыт — изменения недоступны")


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: DbSession, user: CurrentUser) -> list[ProjectOut]:
    result = await db.execute(
        select(Project)
        .where(Project.owner_id == user.id)
        .options(
            selectinload(Project.assets),
            selectinload(Project.entries).selectinload(JournalEntry.media),
        )
        .order_by(Project.created_at.desc())
    )
    return [project_to_out(p) for p in result.scalars().all()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, db: DbSession, user: CurrentUser) -> ProjectOut:
    project = Project(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
    )
    db.add(project)
    await db.commit()
    return project_to_out(await _get_owned_project(db, user, project.id))


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ProjectOut:
    return project_to_out(await _get_owned_project(db, user, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, db: DbSession, user: CurrentUser
) -> ProjectOut:
    project = await _get_owned_project(db, user, project_id)
    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description.strip()
    await db.commit()
    return project_to_out(await _get_owned_project(db, user, project_id))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    project = await _get_owned_project(db, user, project_id)
    unlink_asset_files(list(project.assets))
    await db.delete(project)
    await db.commit()


# ---------------------------------------------------------------------------
# Project plan (schedule table: CSV or Excel)
# ---------------------------------------------------------------------------
async def _clear_plan_stages(db: DbSession, project: Project) -> None:
    await db.execute(delete(PlanStage).where(PlanStage.project_id == project.id))
    project.plan_duration_days = None


@router.post("/{project_id}/plan", response_model=ProjectFileOut)
async def upload_plan(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> ProjectFileOut:
    project = await _get_owned_project(db, user, project_id)
    _ensure_open(project)
    name = file.filename or "plan"
    if not is_allowed_plan(name, file.content_type or ""):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "План должен быть в формате CSV или Excel (.csv, .xlsx, .xls)"
        )

    existing = project.plan_asset
    if existing is not None:
        unlink_asset_files([existing])
        await db.delete(existing)
        await db.flush()
    await _clear_plan_stages(db, project)

    asset_id = uuid.uuid4()
    storage_path, size = await save_upload(file, project_id, asset_id)
    asset = MediaAsset(
        id=asset_id,
        project_id=project_id,
        role="plan",
        original_name=name,
        content_type=file.content_type or "application/octet-stream",
        size=size,
        kind=guess_kind(name, file.content_type or ""),
        storage_path=storage_path,
        analysis_status="pending",
    )
    db.add(asset)
    await db.flush()

    # Fast path: the uploaded workbook is already in the canonical shape
    # (plan_parser.py — exact 21-column .xlsx). Only .xlsx is worth trying;
    # .csv/.xls can't carry the canonical format's multiple sheets (see
    # integrations/README.md). Anything that doesn't fit falls back to the
    # planner service's LLM normalization, same as .csv/.xls always do.
    parsed = None
    if name.lower().endswith(".xlsx"):
        try:
            parsed = parse_plan_workbook(settings.media_root / storage_path)
        except PlanParseError:
            log.info(
                "plan %s isn't canonical-shaped, falling back to LLM normalization", asset_id
            )

    if parsed is not None:
        project.plan_duration_days = parsed.project_duration_days
        for p in parsed.phases:
            db.add(
                PlanStage(
                    project_id=project_id,
                    phase=p.phase,
                    phase_order=p.phase_order,
                    planned_duration_days=p.planned_duration_days,
                )
            )
        asset.analysis_status = "ready"
        await db.commit()
    else:
        asset.analysis_status = "analyzing"
        await db.commit()
        command = PlanNormalizeCommand(
            asset_id=asset_id,
            plan_url=f"{settings.internal_url}/files/{asset_id}",
            original_name=name,
        )
        body = Envelope(
            correlation_id=asset_id, published_at=datetime.now(timezone.utc), payload=command
        ).model_dump_json().encode()
        await broker.publish(broker.PLAN_COMMAND, body)
        # In production this asset is still "analyzing" here — the planner
        # service hasn't replied yet. In tests the fake broker (conftest.py)
        # runs the whole plan.command/plan.result round trip synchronously,
        # in a separate DB session, so this session's cached `asset` needs an
        # explicit refresh to see whatever it left behind.
        await db.refresh(asset)

    return asset_to_file_out(asset, with_insight=True)


@router.delete("/{project_id}/plan", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    project = await _get_owned_project(db, user, project_id)
    _ensure_open(project)
    plan = project.plan_asset
    if plan is not None:
        unlink_asset_files([plan])
        await db.delete(plan)
        await _clear_plan_stages(db, project)
        await db.commit()


async def _load_progress_workbook(db: DbSession, project: Project) -> bytes:
    stages = (
        await db.execute(
            select(PlanStage)
            .where(PlanStage.project_id == project.id)
            .order_by(PlanStage.phase_order)
        )
    ).scalars().all()
    return build_progress_workbook(project, list(stages), list(project.entries))


@router.get("/{project_id}/plan/canonical")
async def download_canonical_plan(
    project_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    """The project's plan, re-expressed as a canonical-format `.xlsx` (see
    plan_parser.py) — regardless of whether it got there via the fast path
    or the LLM fallback (services/planner) — plus the current plan-vs-actual
    fact from the journal (progress.py): phase statuses filled in, and
    `actuals`/`history` sheets. Built fresh on every request; the uploaded
    plan itself is never modified. Re-uploadable as a plan once it has one.
    Always downloadable: with no plan normalized yet (none uploaded, or the
    LLM fallback still running — see PlanStage/analysis.py) or no analysed
    entries, the corresponding sheets are just left empty."""
    project = await _get_owned_project(db, user, project_id)
    content = await _load_progress_workbook(db, project)
    return Response(
        content=content,
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": 'attachment; filename="plan_fact.xlsx"'},
    )


@router.get("/{project_id}/timeline", response_model=ProjectTimelineOut)
async def get_timeline(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ProjectTimelineOut:
    """Series behind the project page's delay charts: one point per analysed
    journal entry (delay, forecast finish, SPI, detected phase) plus each
    phase's planned window vs observed start — the same numbers as the
    `history`/`actuals` sheets of the plan-and-fact export (progress.py).
    No plan yet just means no phases and no planned dates; points are still
    returned."""
    project = await _get_owned_project(db, user, project_id)
    entries = list(project.entries)
    points = [
        TimelinePointOut(
            entry_id=e.id,
            date=e.date,
            phase=e.phase_name,
            phase_confidence=e.phase_confidence,
            delay_days=e.delay_days,
            expected_completion=e.expected_completion,
            spi_time=e.spi_time,
        )
        for e in analysed_entries(entries)
    ]
    if project.plan_duration_days is None:
        return ProjectTimelineOut(points=points)

    stages = (
        await db.execute(select(PlanStage).where(PlanStage.project_id == project.id))
    ).scalars().all()
    actuals = phase_actuals(list(stages), entries, project.plan_duration_days)
    planned_start = planned_start_of(entries)
    return ProjectTimelineOut(
        planned_start=planned_start,
        planned_finish=(
            planned_start + timedelta(days=round(project.plan_duration_days))
            if planned_start
            else None
        ),
        phases=[
            TimelinePhaseOut(
                phase=a.phase,
                phase_order=a.phase_order,
                status=a.status,
                planned_start=a.window.planned_start,
                planned_end=a.window.planned_end,
                first_detected=a.first_detected,
                last_detected=a.last_detected,
                estimated_start=a.estimated_start,
            )
            for a in actuals
        ],
        points=points,
    )


@router.post("/{project_id}/close", response_model=ProjectOut)
async def close_project(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ProjectOut:
    """Freeze the project: the same plan-vs-actual workbook the live export
    serves is stored once as a `final_report` asset, and the project stops
    accepting journal entries and plan changes (409). Works without a plan
    or analysed entries too — the frozen report then has those sheets
    empty, same as the live export."""
    project = await _get_owned_project(db, user, project_id)
    _ensure_open(project)
    content = await _load_progress_workbook(db, project)

    asset_id = uuid.uuid4()
    directory = settings.media_root / str(project.id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{asset_id}.xlsx"
    (directory / filename).write_bytes(content)
    db.add(
        MediaAsset(
            id=asset_id,
            project_id=project.id,
            role="final_report",
            original_name=f"{project.name or 'project'}_final_report.xlsx",
            content_type=XLSX_CONTENT_TYPE,
            size=len(content),
            kind="table",
            storage_path=f"{project.id}/{filename}",
            analysis_status="ready",
        )
    )
    project.closed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project, ["assets"])
    return project_to_out(project)


# ---------------------------------------------------------------------------
# Journal entries (multipart: video files + author + date)
# ---------------------------------------------------------------------------
@router.post(
    "/{project_id}/entries",
    response_model=JournalEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_entry(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
    author: str = Form(..., min_length=1, max_length=64),
    date: date_type = Form(...),
    comment: str = Form("", max_length=5000),
    files: list[UploadFile] = File(...),
) -> JournalEntryOut:
    project = await _get_owned_project(db, user, project_id)
    _ensure_open(project)

    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Нужно загрузить хотя бы одно видео или фото"
        )
    if len(files) > MAX_FILES_PER_ENTRY:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"За один раз можно загрузить не более {MAX_FILES_PER_ENTRY} файлов",
        )
    kinds: list[str] = []
    for f in files:
        kind = guess_kind(f.filename or "", f.content_type or "")
        if kind not in ("video", "image"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"«{f.filename}» — не видео и не фото",
            )
        kinds.append(kind)

    entry = JournalEntry(
        project_id=project.id,
        comment=comment.strip(),
        author=author.strip(),
        date=date,
    )
    db.add(entry)
    await db.flush()

    for f, kind in zip(files, kinds):
        asset_id = uuid.uuid4()
        storage_path, size = await save_upload(f, project.id, asset_id)
        asset = MediaAsset(
            id=asset_id,
            project_id=project.id,
            entry_id=entry.id,
            role="journal_video" if kind == "video" else "journal_photo",
            original_name=f.filename or kind,
            content_type=f.content_type or "application/octet-stream",
            size=size,
            kind=kind,
            storage_path=storage_path,
            analysis_status="pending",
        )
        db.add(asset)

    await db.commit()

    # One combined analysis for the whole entry, not one per file — see
    # analysis.py's run_entry_analysis.
    schedule_entry_analysis(background, entry.id)

    entry = (
        await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry.id)
            .options(selectinload(JournalEntry.media))
        )
    ).scalar_one()
    return entry_to_out(entry)


@router.get("/{project_id}/entries/{entry_id}", response_model=JournalEntryOut)
async def get_entry(
    project_id: uuid.UUID, entry_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> JournalEntryOut:
    """Polled by the frontend while an entry's combined analysis is still
    running — see analysis.py's run_entry_analysis/_maybe_advance_entry."""
    await _get_owned_project(db, user, project_id)
    entry = (
        await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry_id, JournalEntry.project_id == project_id)
            .options(selectinload(JournalEntry.media))
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запись не найдена")
    return entry_to_out(entry)


@router.delete("/{project_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    project_id: uuid.UUID, entry_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> None:
    project = await _get_owned_project(db, user, project_id)
    entry = (
        await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry_id, JournalEntry.project_id == project.id)
            .options(selectinload(JournalEntry.media))
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запись не найдена")
    unlink_asset_files(list(entry.media))
    await db.delete(entry)
    await db.commit()
