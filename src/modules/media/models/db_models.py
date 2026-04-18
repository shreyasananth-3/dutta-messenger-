"""SQLAlchemy ORM models for the media module.

The single table `media_files` is defined by
`migrations/versions/0005_media_module_schema.py` and the living schema at
`src/modules/media/docs/SCHEMA.sql`. Columns are tracked 1:1; changing this
file without a matching migration is a drift bug.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.shared.database import BaseModel


class MediaFile(BaseModel):
    """A single uploaded file (image, video, audio, document).

    Lifecycle:
      1. `upload_status = 'pending'` — row created when a presigned-PUT URL
         is issued. Storage object does not yet exist.
      2. `upload_status = 'completed'` — client PUT succeeded and the server
         confirmed via `head_object`. Object lives at `storage_key`.
      3. `recycle_bin_at` set (uploader delete or user erasure) — object
         stays in S3 for a 30-day grace window per privacy-erasure.md.
      4. `deleted_at` set by the nightly Celery sweep after 30 days — the
         S3 object is deleted and the row is effectively tombstoned (we keep
         the row until hard-delete on the next sweep pass so audit cross-
         references still resolve).
    """

    __tablename__ = "media_files"

    institution_id = Column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploader_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(255), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    storage_key = Column(Text, nullable=False)
    thumbnail_key = Column(Text, nullable=True)
    media_metadata = Column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    upload_status = Column(String(20), nullable=False, server_default="pending")
    recycle_bin_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "upload_status IN ('pending', 'completed', 'failed')",
            name="media_files_upload_status_check",
        ),
    )
