"""Pydantic v2 request models for the media module.

Every request model uses `model_config = ConfigDict(strict=True, ...)` so
unexpected fields fail validation at the edge rather than silently being
ignored.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class InitUploadRequest(BaseModel):
    """`POST /api/v1/media/upload/init` request body.

    The client declares the file it is about to upload; the server validates
    size / MIME against the per-type limits in
    `reference-docs/modules/media/MODULE.md` §File Limits and mints a
    presigned PUT URL bound to those exact values.
    """

    model_config = ConfigDict(extra="forbid")

    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    file_size: Annotated[int, Field(gt=0, le=1_073_741_824)]  # 1 GB ceiling
    mime_type: Annotated[str, Field(min_length=1, max_length=100)]


class CompleteUploadRequest(BaseModel):
    """`POST /api/v1/media/upload/complete` request body.

    The client reports that the S3 PUT finished. The server verifies via
    `head_object` and flips `upload_status` from `pending` → `completed`.
    """

    model_config = ConfigDict(extra="forbid")

    upload_id: uuid.UUID
