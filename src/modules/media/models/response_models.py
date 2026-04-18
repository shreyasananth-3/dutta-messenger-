"""Pydantic v2 response models for the media module."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InitUploadResponse(BaseModel):
    """Returned by `POST /api/v1/media/upload/init`.

    `upload_url` is a presigned PUT URL the client uploads bytes to directly;
    large files never pass through this service. The URL locks the
    `Content-Type` to the value the client declared in the init request.
    """

    model_config = ConfigDict(from_attributes=True)

    upload_id: uuid.UUID
    upload_url: str
    storage_key: str
    expires_in: int = Field(description="Seconds until `upload_url` expires.")


class MediaFileResponse(BaseModel):
    """Full metadata for a single `media_files` row.

    Returned by `POST /upload/complete`, `GET /{id}`, and any future listing
    endpoint. Always reflects the server-side row — clients must not cache
    `upload_status` across the pending→completed transition.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    institution_id: uuid.UUID
    uploader_id: uuid.UUID
    file_name: str
    file_size: int
    mime_type: str
    storage_key: str
    thumbnail_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    upload_status: str
    recycle_bin_at: _dt.datetime | None = None
    deleted_at: _dt.datetime | None = None
    created_at: _dt.datetime
    updated_at: _dt.datetime


class DownloadUrlResponse(BaseModel):
    """Returned by `GET /api/v1/media/{id}/download`.

    Time-limited presigned GET URL. Clients must re-request if the URL has
    expired before they finish downloading — the server issues a fresh URL
    each time, so bookmarking the URL defeats the security model.
    """

    model_config = ConfigDict(from_attributes=True)

    download_url: str
    expires_in: int = Field(description="Seconds until `download_url` expires.")


class DeleteMediaResponse(BaseModel):
    """Returned by `DELETE /api/v1/media/{id}`.

    The file enters the 30-day recycle bin per `docs/design/privacy-erasure.md`.
    The object is not yet removed from S3.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recycle_bin_at: _dt.datetime
    message: str = Field(
        default=("Media file moved to recycle bin. It will be permanently deleted after 30 days."),
    )
