"""HTTP routes for the media module.

Routes are thin adapters over `MediaService`. Per CLAUDE.md each handler is
≤ 15 lines. Per `docs/design/api-versioning.md`, every error path raises an
`AppException` subclass — never a bare `HTTPException(detail=...)`. The
`Idempotency-Key` header is required on `POST /upload/init` per
`docs/design/idempotency.md`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.media.models.request_models import (
    CompleteUploadRequest,
    InitUploadRequest,
)
from src.modules.media.models.response_models import (
    DeleteMediaResponse,
    DownloadUrlResponse,
    InitUploadResponse,
    MediaFileResponse,
)
from src.modules.media.services.media_service import MediaService
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.middleware.idempotency import IdempotencyCheck, require_idempotency
from src.shared.responses import success_response

router = APIRouter(tags=["media"])


# ---------------------------------------------------------------------------
# POST /media/upload/init — mint a presigned PUT URL
# ---------------------------------------------------------------------------


@router.post("/media/upload/init", status_code=status.HTTP_201_CREATED)
async def init_upload(
    data: InitUploadRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idem: Annotated[
        IdempotencyCheck, Depends(require_idempotency("media.upload.init"))
    ],
) -> Any:
    """Initialise an upload and return a presigned PUT URL."""
    if idem.is_hit:
        return idem.replay()
    media, upload_url, expires_in = await MediaService.init_upload(
        db,
        institution_id=current_user["institution_id"],
        uploader_id=current_user["user_id"],
        file_name=data.file_name,
        file_size=data.file_size,
        mime_type=data.mime_type,
    )
    await db.commit()
    response = _init_response(media, upload_url, expires_in)
    await idem.store(response, status=201)
    return response


# ---------------------------------------------------------------------------
# POST /media/upload/complete — verify + flip status to completed
# ---------------------------------------------------------------------------


@router.post("/media/upload/complete")
async def complete_upload(
    data: CompleteUploadRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Confirm an upload completed; verify via S3 HEAD; emit audit."""
    media = await MediaService.complete_upload(
        db,
        institution_id=current_user["institution_id"],
        uploader_id=current_user["user_id"],
        upload_id=data.upload_id,
    )
    await db.commit()
    return success_response(_media_to_response(media))


# ---------------------------------------------------------------------------
# GET /media/{id} — metadata
# ---------------------------------------------------------------------------


@router.get("/media/{media_id}")
async def get_media(
    media_id: uuid.UUID,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return full metadata for a media file the caller can see."""
    media = await MediaService.get_by_id(
        db,
        institution_id=current_user["institution_id"],
        media_id=media_id,
    )
    return success_response(_media_to_response(media))


# ---------------------------------------------------------------------------
# GET /media/{id}/download — short-lived presigned GET URL
# ---------------------------------------------------------------------------


@router.get("/media/{media_id}/download")
async def get_download_url(
    media_id: uuid.UUID,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return a presigned GET URL for the media bytes."""
    url, expires_in = await MediaService.get_download_url(
        db,
        institution_id=current_user["institution_id"],
        media_id=media_id,
    )
    return success_response(
        DownloadUrlResponse(download_url=url, expires_in=expires_in)
    )


# ---------------------------------------------------------------------------
# DELETE /media/{id} — enter 30-day recycle bin (uploader-only)
# ---------------------------------------------------------------------------


@router.delete("/media/{media_id}")
async def delete_media(
    media_id: uuid.UUID,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Soft-delete: set `recycle_bin_at`. The object is purged after 30 days."""
    media = await MediaService.enter_recycle_bin(
        db,
        institution_id=current_user["institution_id"],
        uploader_id=current_user["user_id"],
        media_id=media_id,
    )
    await db.commit()
    return success_response(
        DeleteMediaResponse(
            id=uuid.UUID(media.id),
            recycle_bin_at=media.recycle_bin_at,
        )
    )


# ---------------------------------------------------------------------------
# Internal helper — one MediaFile row → response Pydantic
# ---------------------------------------------------------------------------


def _init_response(media: Any, upload_url: str, expires_in: int) -> Any:
    """Build the JSON-safe dict returned by `/media/upload/init`.

    Separated so the route handler stays ≤ 15 body lines and so the
    Idempotency-Key replay serialises exactly the same bytes we return.
    """
    return jsonable_encoder(
        success_response(
            InitUploadResponse(
                upload_id=uuid.UUID(media.id),
                upload_url=upload_url,
                storage_key=media.storage_key,
                expires_in=expires_in,
            )
        )
    )


def _media_to_response(media: Any) -> MediaFileResponse:
    """Hand-build a MediaFileResponse so the `metadata` column alias resolves.

    The ORM column is named `media_metadata` in Python (to avoid collision
    with SQLAlchemy's `MetaData`) but maps to the `metadata` DB column. Let
    pydantic serialise the rest and splice in the JSONB dict by hand.
    """
    return MediaFileResponse(
        id=uuid.UUID(media.id),
        institution_id=uuid.UUID(media.institution_id),
        uploader_id=uuid.UUID(media.uploader_id),
        file_name=media.file_name,
        file_size=media.file_size,
        mime_type=media.mime_type,
        storage_key=media.storage_key,
        thumbnail_key=media.thumbnail_key,
        metadata=dict(media.media_metadata or {}),
        upload_status=media.upload_status,
        recycle_bin_at=media.recycle_bin_at,
        deleted_at=media.deleted_at,
        created_at=media.created_at,
        updated_at=media.updated_at,
    )
