"""Service layer for the media module.

All presigned-URL minting, S3 verification, metadata persistence, and recycle-
bin logic lives here. Routes are thin adapters (≤ 15 lines per handler per
CLAUDE.md).

Every mutation:
  1. Uses `tenant_scoped_query(MediaFile, institution_id)` — no raw `select()`
     against the tenant-scoped `media_files` table.
  2. Emits exactly one `write_audit(...)` inside the same DB transaction,
     per `docs/design/tenant-isolation.md`.
  3. Uses `src/shared/storage.py` for every S3/MinIO call so boto3 details
     don't leak into service code.

File-limit constants (`_IMAGE_MAX_BYTES`, `_ALLOWED_IMAGE_MIME`, etc.) match
`reference-docs/modules/media/MODULE.md` §File Limits. If the reference doc
changes, update here and the copy at `src/modules/media/docs/MODULE.md`.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import PurePosixPath
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.media.models.db_models import MediaFile
from src.shared import storage
from src.shared.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.tenant import tenant_scoped_query

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# File-type allow-lists and size caps (MODULE.md §File Limits)
# ---------------------------------------------------------------------------

_IMAGE_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
_VIDEO_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
_AUDIO_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
_DOCUMENT_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB

_ALLOWED_IMAGE_MIME: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)
_ALLOWED_VIDEO_MIME: frozenset[str] = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/webm",
    }
)
_ALLOWED_AUDIO_MIME: frozenset[str] = frozenset(
    {
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/aac",
    }
)
_ALLOWED_DOCUMENT_MIME: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

# Executable extensions are rejected regardless of declared MIME type
# (MODULE.md §Security #5).
_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".sh",
        ".bat",
        ".cmd",
        ".com",
        ".scr",
        ".ps1",
        ".msi",
        ".dll",
        ".jar",
        ".app",
        ".pkg",
    }
)


# ---------------------------------------------------------------------------
# Presigned URL TTLs + recycle-bin window
# ---------------------------------------------------------------------------

_PRESIGNED_PUT_EXPIRES_IN = 3600  # 1 hour for uploads
_PRESIGNED_GET_EXPIRES_IN = 3600  # 1 hour for downloads
_RECYCLE_BIN_GRACE_DAYS = 30  # privacy-erasure.md §Media


class MediaService:
    """Static-method collection; no per-instance state."""

    # ------------------------------------------------------------------
    # Upload — init
    # ------------------------------------------------------------------

    @staticmethod
    async def init_upload(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        uploader_id: uuid.UUID,
        file_name: str,
        file_size: int,
        mime_type: str,
    ) -> tuple[MediaFile, str, int]:
        """Validate the request, create a pending row, mint a presigned PUT URL.

        Args:
            db: Async database session (transaction managed by caller).
            institution_id: Tenant the uploader belongs to. Becomes the S3
                key prefix and the new row's `institution_id`.
            uploader_id: User creating this upload.
            file_name: Client-declared file name (used for the extension and
                stored verbatim for the download Content-Disposition header).
            file_size: Declared size in bytes. Validated against the per-type
                cap and locked into the presigned URL signature so S3 itself
                rejects a larger PUT.
            mime_type: Declared MIME type. Validated against the allow-list.

        Returns:
            A tuple `(media_file, upload_url, expires_in_seconds)`.

        Raises:
            ValidationError: If `mime_type` or `file_size` is rejected, or the
                extension is on the executable block-list.
        """
        _assert_extension_allowed(file_name)
        _assert_mime_and_size(mime_type, file_size)

        media_id = uuid.uuid4()
        storage_key = _build_storage_key(
            institution_id=institution_id,
            media_id=media_id,
            file_name=file_name,
        )

        media = MediaFile(
            id=str(media_id),
            institution_id=str(institution_id),
            uploader_id=str(uploader_id),
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            storage_key=storage_key,
            upload_status="pending",
        )
        db.add(media)
        await db.flush()

        upload_url = await storage.presigned_put_url(
            storage_key,
            content_type=mime_type,
            content_length_max=file_size,
            expires_in=_PRESIGNED_PUT_EXPIRES_IN,
        )

        logger.info(
            "media_upload_initiated",
            media_id=str(media_id),
            institution_id=str(institution_id),
            uploader_id=str(uploader_id),
            mime_type=mime_type,
            file_size=file_size,
        )
        return media, upload_url, _PRESIGNED_PUT_EXPIRES_IN

    # ------------------------------------------------------------------
    # Upload — complete
    # ------------------------------------------------------------------

    @staticmethod
    async def complete_upload(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        uploader_id: uuid.UUID,
        upload_id: uuid.UUID,
    ) -> MediaFile:
        """Verify the S3 object exists and flip status to `completed`.

        Args:
            db: Async database session.
            institution_id: Caller's institution (tenant scope).
            uploader_id: Caller's user id (must match `media.uploader_id`).
            upload_id: The media row returned from `init_upload`.

        Returns:
            The updated `MediaFile` row.

        Raises:
            NotFoundError: If no pending row exists for this caller.
            PermissionDeniedError: If the caller is not the uploader.
            ValidationError: If the S3 object cannot be HEADed (client never
                finished the PUT, or the key was never written).
        """
        media = await _fetch_for_uploader(
            db,
            institution_id=institution_id,
            uploader_id=uploader_id,
            media_id=upload_id,
        )

        if media.upload_status != "pending":
            # Already completed (idempotent replay via Idempotency-Key would
            # short-circuit above us; this catches an accidental second call
            # with a different key). No audit row on a no-op.
            return media

        meta = await storage.head_object(media.storage_key)
        if meta is None:
            logger.warning(
                "media_upload_verify_missing",
                media_id=str(upload_id),
                storage_key=media.storage_key,
            )
            raise ValidationError(
                "Upload not found in storage. Re-upload and try again.",
                field="upload_id",
            )

        media.upload_status = "completed"
        # Stash the server-observed byte count in metadata so operators can
        # compare against the declared size if there's a dispute.
        media.media_metadata = _merge_metadata(
            media.media_metadata,
            {"verified_size_bytes": int(meta.get("ContentLength") or 0)},
        )
        await db.flush()

        await write_audit(
            db,
            actor_id=uploader_id,
            institution_id=institution_id,
            action=AuditEvent.MEDIA_UPLOADED,
            resource_type="media",
            resource_id=upload_id,
            metadata={
                "file_type": media.mime_type,
                "file_size_bytes": media.file_size,
            },
        )
        logger.info(
            "media_upload_completed",
            media_id=str(upload_id),
            institution_id=str(institution_id),
        )
        return media

    # ------------------------------------------------------------------
    # Read — metadata + signed download
    # ------------------------------------------------------------------

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        media_id: uuid.UUID,
    ) -> MediaFile:
        """Fetch a media row by id, scoped to the caller's institution.

        Tombstoned rows (`deleted_at` set) are treated as not-found so
        callers never see a recycled resource. Rows in the 30-day recycle-
        bin grace window are still returned (so uploaders / admins can
        observe their own soft-deleted files).
        """
        stmt = (
            tenant_scoped_query(MediaFile, institution_id)
            .where(MediaFile.id == str(media_id))
            .where(MediaFile.deleted_at.is_(None))
        )
        result = await db.execute(stmt)
        media = result.scalar_one_or_none()
        if media is None:
            raise NotFoundError("media", str(media_id))
        return media

    @staticmethod
    async def list_for_uploader(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        uploader_id: uuid.UUID | str,
        limit: int = 50,
        before_id: uuid.UUID | str | None = None,
    ) -> list[MediaFile]:
        """List the caller's own completed uploads, newest-first.

        Privacy boundary: scoped to (institution_id, uploader_id) — a
        user cannot see another user's vault. Excludes tombstoned and
        recycle-binned rows. Cursor-paginated via `before_id`.
        """
        stmt = (
            tenant_scoped_query(MediaFile, institution_id)
            .where(MediaFile.uploader_id == str(uploader_id))
            .where(MediaFile.upload_status == "completed")
            .where(MediaFile.deleted_at.is_(None))
            .where(MediaFile.recycle_bin_at.is_(None))
            .order_by(MediaFile.created_at.desc())
            .limit(limit)
        )
        if before_id is not None:
            anchor = await db.scalar(
                tenant_scoped_query(MediaFile, institution_id).where(
                    MediaFile.id == str(before_id)
                )
            )
            if anchor is not None:
                stmt = stmt.where(MediaFile.created_at < anchor.created_at)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def assert_uploader_owns(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        uploader_id: uuid.UUID | str,
        media_ids: list[uuid.UUID],
    ) -> None:
        """Guard: every media_id must be owned by `uploader_id`.

        Used by the chat send-message path so a user cannot share
        another user's vault item by guessing its UUID. Raises
        PermissionDeniedError on the first id that fails the check.
        """
        if not media_ids:
            return
        for mid in media_ids:
            stmt = (
                tenant_scoped_query(MediaFile, institution_id)
                .where(MediaFile.id == str(mid))
                .where(MediaFile.uploader_id == str(uploader_id))
                .where(MediaFile.deleted_at.is_(None))
            )
            row = await db.scalar(stmt)
            if row is None:
                raise PermissionDeniedError(
                    f"media {mid} is not owned by the sender"
                )

    @staticmethod
    async def get_download_url(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        media_id: uuid.UUID,
    ) -> tuple[str, int]:
        """Return a short-lived presigned GET URL for a completed upload.

        Raises:
            NotFoundError: If the media does not exist in this institution
                or has been tombstoned.
            ValidationError: If the upload never completed. Callers should
                retry after `/upload/complete` succeeds.
        """
        media = await MediaService.get_by_id(db, institution_id=institution_id, media_id=media_id)
        if media.upload_status != "completed":
            raise ValidationError(
                "Upload is not yet complete. Call /upload/complete first.",
                field="upload_status",
            )
        # Recycle-bin files are still downloadable during the grace window —
        # the uploader may want to restore or export before permanent purge.
        #
        # Content-Disposition: render media (images / video / audio) inline
        # so the chat client can preview them without bouncing the user out
        # to the S3 redirect. Documents (PDF, archives, office files, etc.)
        # keep `attachment` so the client / OS download flow is unaffected
        # — Shreyas explicitly wants PDFs to open in the system viewer.
        mime = (media.mime_type or "").lower()
        if mime.startswith(("image/", "video/", "audio/")):
            disposition_kind = "inline"
        else:
            disposition_kind = "attachment"
        content_disposition = (
            f'{disposition_kind}; filename="{_safe_filename(media.file_name)}"'
        )
        url = await storage.presigned_get_url(
            media.storage_key,
            expires_in=_PRESIGNED_GET_EXPIRES_IN,
            response_content_disposition=content_disposition,
        )
        return url, _PRESIGNED_GET_EXPIRES_IN

    # ------------------------------------------------------------------
    # Delete — enter recycle bin (uploader-only)
    # ------------------------------------------------------------------

    @staticmethod
    async def enter_recycle_bin(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        uploader_id: uuid.UUID,
        media_id: uuid.UUID,
    ) -> MediaFile:
        """Mark the media row as recycled. S3 object untouched for 30 days.

        Only the original uploader can perform this operation through the
        public API. Admin-driven mass delete requires a permission check
        deferred to Stage 4b (ACL) per the kickoff brief — see
        `docs/API.md` §Deferred.

        Idempotent: if already in the recycle bin, returns the row unchanged
        without a second audit write.

        Raises:
            NotFoundError: Media does not exist in this institution.
            PermissionDeniedError: Caller is not the uploader.
        """
        media = await _fetch_for_uploader(
            db,
            institution_id=institution_id,
            uploader_id=uploader_id,
            media_id=media_id,
        )

        if media.recycle_bin_at is not None:
            return media

        media.recycle_bin_at = _dt.datetime.now(tz=_dt.UTC)
        await db.flush()

        await write_audit(
            db,
            actor_id=uploader_id,
            institution_id=institution_id,
            action=AuditEvent.MEDIA_RECYCLE_BIN_ENTERED,
            resource_type="media",
            resource_id=media_id,
            metadata={"grace_days": _RECYCLE_BIN_GRACE_DAYS},
        )
        logger.info(
            "media_recycle_bin_entered",
            media_id=str(media_id),
            institution_id=str(institution_id),
            uploader_id=str(uploader_id),
        )
        return media


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


async def _fetch_for_uploader(
    db: AsyncSession,
    *,
    institution_id: uuid.UUID,
    uploader_id: uuid.UUID,
    media_id: uuid.UUID,
) -> MediaFile:
    """Fetch a media row and enforce `uploader_id == caller`.

    Returns 404 on cross-tenant; 403 on same-tenant-wrong-user. The 403 is
    safe here because the caller has already proven they can see the row
    (tenant match). An attacker from another tenant gets 404 via
    `tenant_scoped_query`, never reaching the permission check.
    """
    stmt = (
        tenant_scoped_query(MediaFile, institution_id)
        .where(MediaFile.id == str(media_id))
        .where(MediaFile.deleted_at.is_(None))
    )
    result = await db.execute(stmt)
    media = result.scalar_one_or_none()
    if media is None:
        raise NotFoundError("media", str(media_id))
    if str(media.uploader_id) != str(uploader_id):
        raise PermissionDeniedError("You can only modify media you uploaded.")
    return media


def _assert_extension_allowed(file_name: str) -> None:
    """Reject files whose extension is on the executable block-list."""
    ext = PurePosixPath(file_name).suffix.lower()
    if ext in _BLOCKED_EXTENSIONS:
        logger.warning("media_denied_extension", file_name=file_name, ext=ext)
        raise ValidationError(
            f"File type '{ext}' is not permitted.",
            field="file_name",
        )


def _assert_mime_and_size(mime_type: str, file_size: int) -> None:
    """Validate MIME allow-list and per-type byte cap."""
    normalised = mime_type.lower().strip()

    if normalised in _ALLOWED_IMAGE_MIME:
        cap = _IMAGE_MAX_BYTES
    elif normalised in _ALLOWED_VIDEO_MIME:
        cap = _VIDEO_MAX_BYTES
    elif normalised in _ALLOWED_AUDIO_MIME:
        cap = _AUDIO_MAX_BYTES
    elif normalised in _ALLOWED_DOCUMENT_MIME:
        cap = _DOCUMENT_MAX_BYTES
    else:
        logger.warning("media_denied_mime_type", mime_type=mime_type)
        raise ValidationError(
            f"MIME type '{mime_type}' is not permitted.",
            field="mime_type",
        )

    if file_size > cap:
        logger.warning(
            "media_denied_size_exceeded",
            mime_type=mime_type,
            file_size=file_size,
            cap=cap,
        )
        raise ValidationError(
            f"File size {file_size} exceeds {cap}-byte limit for {mime_type}.",
            field="file_size",
        )


def _build_storage_key(
    *,
    institution_id: uuid.UUID,
    media_id: uuid.UUID,
    file_name: str,
) -> str:
    """Return the S3 object key per MODULE.md §Storage Structure.

    Layout: ``{institution_id}/originals/{year}/{month}/{media_id}{ext}``
    The month is zero-padded (`01`-`12`) and the year is always 4 digits so
    the prefix sorts lexicographically. The original filename is NOT part of
    the key — it only appears in the DB row and the download
    `Content-Disposition`. Keeping the key UUID-only avoids leaking file
    names via S3 bucket listings.
    """
    now = _dt.datetime.now(tz=_dt.UTC)
    ext = PurePosixPath(file_name).suffix.lower()
    return f"{institution_id}/originals/{now.year:04d}/{now.month:02d}/{media_id}{ext}"


def _safe_filename(file_name: str) -> str:
    """Strip quotes/newlines so Content-Disposition cannot be header-injected."""
    return file_name.replace('"', "").replace("\r", "").replace("\n", "")


def _merge_metadata(existing: Any, add: dict[str, Any]) -> dict[str, Any]:
    """Merge new keys into the existing JSONB dict without losing old data."""
    merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    merged.update(add)
    return merged
