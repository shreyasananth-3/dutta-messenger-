"""Unit tests for `MediaService`.

Exercises the service layer directly (no HTTP). Integration tests in
`test_media_routes.py` cover the route layer + 7-point checklist.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.media.models.db_models import MediaFile
from src.modules.media.services.media_service import MediaService
from src.shared.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# init_upload
# ---------------------------------------------------------------------------


class TestInitUpload:
    @pytest.mark.asyncio
    async def test_happy_path_creates_pending_row(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
    ) -> None:
        media, url, expires_in = await MediaService.init_upload(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            file_name="holiday.jpg",
            file_size=500_000,
            mime_type="image/jpeg",
        )
        assert media.upload_status == "pending"
        assert media.institution_id == institution.id
        assert media.uploader_id == admin_user.id
        assert media.storage_key.startswith(f"{institution.id}/originals/")
        assert media.storage_key.endswith(".jpg")
        assert url.startswith("https://minio.test/")
        assert expires_in == 3600
        assert mock_storage["last_put_call"]["content_type"] == "image/jpeg"
        assert mock_storage["last_put_call"]["content_length_max"] == 500_000

    @pytest.mark.asyncio
    async def test_rejects_unsupported_mime(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(ValidationError) as ex:
            await MediaService.init_upload(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(admin_user.id),
                file_name="virus.dat",
                file_size=1024,
                mime_type="application/x-msdownload",
            )
        assert ex.value.error_code == "VALIDATION_ERROR"
        assert "mime" in ex.value.message.lower()

    @pytest.mark.asyncio
    async def test_rejects_blocked_extension(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(ValidationError) as ex:
            await MediaService.init_upload(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(admin_user.id),
                file_name="setup.exe",
                file_size=2048,
                mime_type="image/jpeg",  # spoofed
            )
        assert ".exe" in ex.value.message

    @pytest.mark.asyncio
    async def test_rejects_oversized_image(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(ValidationError) as ex:
            await MediaService.init_upload(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(admin_user.id),
                file_name="huge.png",
                file_size=11 * 1024 * 1024,  # 11 MB > 10 MB image cap
                mime_type="image/png",
            )
        assert ex.value.details.get("field") == "file_size"

    @pytest.mark.asyncio
    async def test_storage_key_uses_lowercase_extension(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
    ) -> None:
        media, _, _ = await MediaService.init_upload(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            file_name="VACATION.JPEG",
            file_size=1024,
            mime_type="image/jpeg",
        )
        assert media.storage_key.endswith(".jpeg")

    @pytest.mark.parametrize(
        ("file_name", "mime_type", "file_size"),
        [
            ("clip.mp4", "video/mp4", 50 * 1024 * 1024),  # video branch
            ("voice.ogg", "audio/ogg", 10 * 1024 * 1024),  # audio branch
            ("report.pdf", "application/pdf", 40 * 1024 * 1024),  # document branch
        ],
    )
    @pytest.mark.asyncio
    async def test_allowed_non_image_types(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        mock_storage: dict,
        file_name: str,
        mime_type: str,
        file_size: int,
    ) -> None:
        """Exercise the video/audio/document branches of the MIME allow-list."""
        media, _, _ = await MediaService.init_upload(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
        )
        assert media.upload_status == "pending"
        assert media.mime_type == mime_type


# ---------------------------------------------------------------------------
# complete_upload
# ---------------------------------------------------------------------------


class TestCompleteUpload:
    @pytest.mark.asyncio
    async def test_happy_path_flips_to_completed(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        result = await MediaService.complete_upload(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            upload_id=uuid.UUID(pending_media.id),
        )
        assert result.upload_status == "completed"
        assert result.media_metadata.get("verified_size_bytes") == 12345

    @pytest.mark.asyncio
    async def test_missing_s3_object_raises(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        mock_storage["head_returns"] = None
        with pytest.raises(ValidationError) as ex:
            await MediaService.complete_upload(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(admin_user.id),
                upload_id=uuid.UUID(pending_media.id),
            )
        assert "storage" in ex.value.message.lower()

    @pytest.mark.asyncio
    async def test_non_uploader_cannot_complete(
        self,
        db_session: AsyncSession,
        institution: Institution,
        other_user: User,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await MediaService.complete_upload(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(other_user.id),
                upload_id=uuid.UUID(pending_media.id),
            )

    @pytest.mark.asyncio
    async def test_cross_tenant_complete_returns_not_found(
        self,
        db_session: AsyncSession,
        foreign_institution: Institution,
        foreign_user: User,
        pending_media: MediaFile,  # belongs to institution A
        mock_storage: dict,
    ) -> None:
        """A foreign user sees 404, NOT 403. No existence confirmation."""
        with pytest.raises(NotFoundError):
            await MediaService.complete_upload(
                db_session,
                institution_id=uuid.UUID(foreign_institution.id),
                uploader_id=uuid.UUID(foreign_user.id),
                upload_id=uuid.UUID(pending_media.id),
            )

    @pytest.mark.asyncio
    async def test_double_complete_is_noop(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        # Second complete on an already-completed row must not re-audit.
        result = await MediaService.complete_upload(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            upload_id=uuid.UUID(completed_media.id),
        )
        assert result.upload_status == "completed"
        # head_object was NOT called for the no-op path
        assert mock_storage["head_calls"] == 0


# ---------------------------------------------------------------------------
# get_by_id + get_download_url
# ---------------------------------------------------------------------------


class TestGetById:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        db_session: AsyncSession,
        institution: Institution,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        media = await MediaService.get_by_id(
            db_session,
            institution_id=uuid.UUID(institution.id),
            media_id=uuid.UUID(completed_media.id),
        )
        assert media.id == completed_media.id

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        db_session: AsyncSession,
        foreign_institution: Institution,
        completed_media: MediaFile,  # institution A
        mock_storage: dict,
    ) -> None:
        with pytest.raises(NotFoundError):
            await MediaService.get_by_id(
                db_session,
                institution_id=uuid.UUID(foreign_institution.id),
                media_id=uuid.UUID(completed_media.id),
            )

    @pytest.mark.asyncio
    async def test_unknown_id_returns_404(
        self,
        db_session: AsyncSession,
        institution: Institution,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(NotFoundError):
            await MediaService.get_by_id(
                db_session,
                institution_id=uuid.UUID(institution.id),
                media_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_tombstoned_row_not_leaked(
        self,
        db_session: AsyncSession,
        institution: Institution,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        """A row with `deleted_at` set returns 404 even within-tenant."""
        import datetime as _dt

        completed_media.deleted_at = _dt.datetime.now(tz=_dt.UTC)
        await db_session.flush()
        with pytest.raises(NotFoundError):
            await MediaService.get_by_id(
                db_session,
                institution_id=uuid.UUID(institution.id),
                media_id=uuid.UUID(completed_media.id),
            )


class TestGetDownloadUrl:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        db_session: AsyncSession,
        institution: Institution,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        url, expires_in = await MediaService.get_download_url(
            db_session,
            institution_id=uuid.UUID(institution.id),
            media_id=uuid.UUID(completed_media.id),
        )
        assert url.startswith("https://minio.test/")
        assert expires_in == 3600
        disp = mock_storage["last_get_call"]["content_disposition"]
        assert 'filename="report.pdf"' in disp

    @pytest.mark.asyncio
    async def test_pending_not_downloadable(
        self,
        db_session: AsyncSession,
        institution: Institution,
        pending_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(ValidationError):
            await MediaService.get_download_url(
                db_session,
                institution_id=uuid.UUID(institution.id),
                media_id=uuid.UUID(pending_media.id),
            )


# ---------------------------------------------------------------------------
# enter_recycle_bin
# ---------------------------------------------------------------------------


class TestEnterRecycleBin:
    @pytest.mark.asyncio
    async def test_happy_path_sets_timestamp(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        media = await MediaService.enter_recycle_bin(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            media_id=uuid.UUID(completed_media.id),
        )
        assert media.recycle_bin_at is not None

    @pytest.mark.asyncio
    async def test_idempotent_on_already_recycled(
        self,
        db_session: AsyncSession,
        institution: Institution,
        admin_user: User,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        first = await MediaService.enter_recycle_bin(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            media_id=uuid.UUID(completed_media.id),
        )
        second = await MediaService.enter_recycle_bin(
            db_session,
            institution_id=uuid.UUID(institution.id),
            uploader_id=uuid.UUID(admin_user.id),
            media_id=uuid.UUID(completed_media.id),
        )
        assert first.recycle_bin_at == second.recycle_bin_at

    @pytest.mark.asyncio
    async def test_non_uploader_cannot_delete(
        self,
        db_session: AsyncSession,
        institution: Institution,
        other_user: User,
        completed_media: MediaFile,
        mock_storage: dict,
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await MediaService.enter_recycle_bin(
                db_session,
                institution_id=uuid.UUID(institution.id),
                uploader_id=uuid.UUID(other_user.id),
                media_id=uuid.UUID(completed_media.id),
            )

    @pytest.mark.asyncio
    async def test_cross_tenant_returns_404(
        self,
        db_session: AsyncSession,
        foreign_institution: Institution,
        foreign_user: User,
        completed_media: MediaFile,  # institution A
        mock_storage: dict,
    ) -> None:
        with pytest.raises(NotFoundError):
            await MediaService.enter_recycle_bin(
                db_session,
                institution_id=uuid.UUID(foreign_institution.id),
                uploader_id=uuid.UUID(foreign_user.id),
                media_id=uuid.UUID(completed_media.id),
            )
