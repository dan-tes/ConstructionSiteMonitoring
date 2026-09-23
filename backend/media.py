"""Helpers for storing uploaded files on disk and shaping them for the API."""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile, status

from config import settings
from models import JournalEntry, MediaAsset, Project
from schemas import JournalEntryOut, ProjectFileOut, ProjectOut, VideoInsightOut

_CHUNK = 1024 * 1024

# Kept in sync with the frontend's fileKind() in frontend/src/lib/files.ts —
# browsers report an empty or generic MIME type for many containers.
VIDEO_EXTENSIONS = {
    "mp4", "m4v", "webm", "ogv", "mov", "qt", "mkv", "avi", "wmv", "flv",
    "3gp", "3g2", "mpeg", "mpg", "mts", "m2ts", "ts",
}
IMAGE_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "heic", "heif", "svg",
}
# Project plan is a schedule table (stages + dates), uploaded as CSV or Excel.
TABLE_EXTENSIONS = {"csv", "xlsx", "xls"}
TABLE_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",  # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
}


def _extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.lower() if "." in name else ""


def guess_kind(name: str, content_type: str) -> str:
    if content_type in TABLE_CONTENT_TYPES or _extension(name) in TABLE_EXTENSIONS:
        return "table"
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    ext = _extension(name)
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "other"


def is_allowed_plan(name: str, content_type: str) -> bool:
    return guess_kind(name, content_type) == "table"


async def save_upload(
    upload: UploadFile, project_id: uuid.UUID, asset_id: uuid.UUID
) -> tuple[str, int]:
    """Stream an upload to <media_root>/<project_id>/<asset_id><ext>.

    Returns (storage_path relative to media_root, size in bytes). Raises 413 if
    the file exceeds settings.max_upload_bytes.
    """
    ext = _extension(upload.filename or "")
    filename = f"{asset_id}.{ext}" if ext else str(asset_id)
    directory = settings.media_root / str(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / filename

    size = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"Файл больше {settings.max_upload_mb} МБ",
                    )
                fh.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return str(PurePosixPath(str(project_id)) / filename), size


def unlink_asset_files(assets: list[MediaAsset]) -> None:
    """Best-effort removal of the on-disk files for the given asset rows."""
    for asset in assets:
        try:
            (settings.media_root / asset.storage_path).unlink(missing_ok=True)
        except OSError:
            pass


def file_url(asset_id: uuid.UUID) -> str:
    return f"{settings.public_base_url}/files/{asset_id}"


def asset_to_file_out(asset: MediaAsset, *, with_insight: bool = False) -> ProjectFileOut:
    insight = None
    if with_insight and asset.role == "plan":
        # Reused for the plan's own async step: canonical-workbook parsing is
        # synchronous (status goes straight to "ready"/"failed" at upload
        # time), but a plan that needed the LLM fallback sits at "analyzing"
        # until the planner service's result comes back — see
        # routers/projects.py's upload_plan and analysis.py's
        # handle_plan_result. A journal_video/journal_photo asset never gets
        # an insight of its own — see entry_to_out, analysis is combined per
        # upload batch (JournalEntry), not per individual file.
        insight = VideoInsightOut(status=asset.analysis_status)
    return ProjectFileOut(
        id=str(asset.id),
        name=asset.original_name,
        type=asset.content_type,
        size=asset.size,
        url=file_url(asset.id),
        kind=asset.kind,
        insight=insight,
    )


def entry_to_out(entry: JournalEntry) -> JournalEntryOut:
    ready = entry.analysis_status == "ready"
    insight = VideoInsightOut(
        status=entry.analysis_status,
        stage_summary=entry.stage_summary if ready else None,
        equipment_summary=entry.equipment_summary if ready else None,
        narrative_report=entry.narrative_report if ready else None,
        photos=[],  # frame extraction not implemented yet
        visual_phase_name=entry.visual_phase_name,
        visual_phase_confidence=entry.visual_phase_confidence,
    )
    return JournalEntryOut(
        id=entry.id,
        comment=entry.comment,
        author=entry.author,
        date=entry.date,
        media=[asset_to_file_out(a) for a in entry.media],
        insight=insight,
    )


def project_to_out(project: Project) -> ProjectOut:
    plan = project.plan_asset
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        plan=asset_to_file_out(plan, with_insight=True) if plan else None,
        plan_status=project.plan_status,
        entries=[entry_to_out(e) for e in project.entries],
        created_at=project.created_at,
        closed_at=project.closed_at,
        final_report=asset_to_file_out(final) if (final := project.final_report_asset) else None,
    )
