import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from config import settings
from deps import DbSession
from models import MediaAsset

# Files are served WITHOUT auth: the browser's <video>/<img> tags can't send a
# bearer token. Access is gated only by the unguessable asset UUID. A signed-URL
# scheme would be the next step if that isn't enough.
router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{asset_id}")
async def get_file(asset_id: uuid.UUID, db: DbSession) -> FileResponse:
    asset = await db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    path = settings.media_root / asset.storage_path
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    return FileResponse(
        path,
        media_type=asset.content_type or "application/octet-stream",
        filename=asset.original_name,
        content_disposition_type="inline",
    )
