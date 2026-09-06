import uuid
from datetime import date as date_type

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from analysis import schedule_analysis
from deps import CurrentUser, DbSession
from media import (
    asset_to_file_out,
    entry_to_out,
    guess_kind,
    is_allowed_plan,
    project_to_out,
    save_upload,
    unlink_asset_files,
)
from models import JournalEntry, MediaAsset, Project, User
from schemas import (
    JournalEntryOut,
    ProjectCreate,
    ProjectFileOut,
    ProjectOut,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_VIDEOS_PER_ENTRY = 20


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
# Project plan (single file: PDF or image)
# ---------------------------------------------------------------------------
@router.post("/{project_id}/plan", response_model=ProjectFileOut)
async def upload_plan(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> ProjectFileOut:
    project = await _get_owned_project(db, user, project_id)
    name = file.filename or "plan"
    if not is_allowed_plan(name, file.content_type or ""):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "План должен быть PDF или изображением")

    existing = project.plan_asset
    if existing is not None:
        unlink_asset_files([existing])
        await db.delete(existing)
        await db.flush()

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
    )
    db.add(asset)
    await db.commit()
    return asset_to_file_out(asset)


@router.delete("/{project_id}/plan", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(project_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    project = await _get_owned_project(db, user, project_id)
    plan = project.plan_asset
    if plan is not None:
        unlink_asset_files([plan])
        await db.delete(plan)
        await db.commit()


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

    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нужно загрузить хотя бы одно видео")
    if len(files) > MAX_VIDEOS_PER_ENTRY:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"За один раз можно загрузить не более {MAX_VIDEOS_PER_ENTRY} видео",
        )
    for f in files:
        if guess_kind(f.filename or "", f.content_type or "") != "video":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"«{f.filename}» — не видеофайл",
            )

    entry = JournalEntry(
        project_id=project.id,
        comment=comment.strip(),
        author=author.strip(),
        date=date,
    )
    db.add(entry)
    await db.flush()

    assets: list[MediaAsset] = []
    for f in files:
        asset_id = uuid.uuid4()
        storage_path, size = await save_upload(f, project.id, asset_id)
        asset = MediaAsset(
            id=asset_id,
            project_id=project.id,
            entry_id=entry.id,
            role="journal_video",
            original_name=f.filename or "video",
            content_type=f.content_type or "application/octet-stream",
            size=size,
            kind="video",
            storage_path=storage_path,
            analysis_status="pending",
        )
        db.add(asset)
        assets.append(asset)

    await db.commit()

    for asset in assets:
        schedule_analysis(background, asset.id)

    entry = (
        await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry.id)
            .options(selectinload(JournalEntry.media))
        )
    ).scalar_one()
    return entry_to_out(entry)


@router.get("/{project_id}/videos/{video_id}", response_model=ProjectFileOut)
async def get_video(
    project_id: uuid.UUID, video_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> ProjectFileOut:
    await _get_owned_project(db, user, project_id)
    asset = (
        await db.execute(
            select(MediaAsset).where(
                MediaAsset.id == video_id,
                MediaAsset.project_id == project_id,
                MediaAsset.role == "journal_video",
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Видео не найдено")
    return asset_to_file_out(asset, with_insight=True)


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
