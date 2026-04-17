"""FCM device-token lifecycle service.

Owns the mutation paths that register, refresh, and revoke tokens in the
`fcm_tokens` table. Cross-tenant access is blocked by asserting that the
token's owning user belongs to the caller's institution on every lookup
(`fcm_tokens` has no `institution_id` column — scoping is transitive
through `users.institution_id`, per `docs/design/tenant-isolation.md` §1.2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import User
from src.modules.notifications.models.db_models import FcmToken
from src.shared.exceptions import NotFoundError
from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.tenant import (
    TenantScopeViolation,
    assert_same_institution,
)

logger = structlog.get_logger()


def _utcnow() -> datetime:
    """UTC now — isolated so tests can freeze time via `freezegun`."""
    return datetime.now(UTC)


class TokenService:
    """Service methods for FCM device tokens."""

    @staticmethod
    async def register_token(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
        token: str,
        device_name: str | None,
        device_type: str | None,
    ) -> tuple[FcmToken, bool]:
        """Register or reactivate an FCM device token.

        Idempotent on `(user_id, token)` per the idempotency RFC:
        re-registering the same token for the same user updates
        `last_used_at` and returns the existing row with `reused=True`,
        instead of raising a conflict.

        If the same `token` string is already bound to a different user
        (device handed between accounts), the stale row is deactivated with
        a `notification.token.revoked` audit row and a fresh row is written
        for the new owner.

        Args:
            db: Async database session (transaction managed by the caller).
            user_id: Owner of the token. Taken from the verified JWT.
            institution_id: Owner's institution. Taken from the verified JWT.
            token: Opaque FCM registration string from Flutter.
            device_name: Human-readable device label (optional).
            device_type: One of "ios", "android", "web" (optional).

        Returns:
            `(row, reused)` where `reused=True` means the caller
            re-registered a token they already owned.
        """
        existing_own = await db.scalar(
            select(FcmToken).where(
                FcmToken.user_id == str(user_id),
                FcmToken.token == token,
            )
        )
        if existing_own is not None:
            existing_own.is_active = True
            existing_own.device_name = device_name or existing_own.device_name
            existing_own.device_type = device_type or existing_own.device_type
            existing_own.last_used_at = _utcnow()
            await db.flush()
            logger.info(
                "fcm_token_reused",
                user_id=str(user_id),
                token_id=str(existing_own.id),
            )
            return existing_own, True

        existing_other = await db.scalar(
            select(FcmToken).where(FcmToken.token == token)
        )
        if existing_other is not None:
            await TokenService._revoke_stale_binding(
                db,
                institution_id=institution_id,
                stale_row=existing_other,
            )

        row = FcmToken(
            user_id=str(user_id),
            token=token,
            device_name=device_name,
            device_type=device_type,
            is_active=True,
            last_used_at=_utcnow(),
        )
        db.add(row)
        await db.flush()

        await write_audit(
            db,
            actor_id=user_id,
            institution_id=institution_id,
            action=AuditEvent.NOTIFICATION_TOKEN_REGISTERED,
            resource_type="fcm_token",
            resource_id=row.id,
            metadata={"device_type": device_type or "unknown"},
        )
        logger.info(
            "fcm_token_registered",
            user_id=str(user_id),
            token_id=str(row.id),
        )
        return row, False

    @staticmethod
    async def revoke_token(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
        token_id: uuid.UUID,
    ) -> None:
        """Soft-deactivate one of the caller's tokens.

        Sets `is_active = False` and writes a
        `notification.token.revoked` audit row. Cross-tenant access yields
        404 via `TenantScopeViolation` (never 403 — see tenant-isolation
        §1.1 for the "no existence oracle" rule).

        Args:
            db: Async database session.
            user_id: Caller's user_id (from JWT).
            institution_id: Caller's institution_id (from JWT).
            token_id: Primary key of the FcmToken to revoke.

        Raises:
            NotFoundError: If the token doesn't exist, isn't owned by the
                caller, or belongs to another institution.
        """
        row = await db.scalar(select(FcmToken).where(FcmToken.id == str(token_id)))
        if row is None or row.user_id != str(user_id):
            raise NotFoundError("fcm_token", str(token_id))

        try:
            owner = await db.scalar(
                select(User).where(User.id == row.user_id)
            )
            assert_same_institution(
                owner.institution_id if owner is not None else None,
                institution_id,
            )
        except TenantScopeViolation as exc:
            raise NotFoundError("fcm_token", str(token_id)) from exc

        row.is_active = False
        await db.flush()

        await write_audit(
            db,
            actor_id=user_id,
            institution_id=institution_id,
            action=AuditEvent.NOTIFICATION_TOKEN_REVOKED,
            resource_type="fcm_token",
            resource_id=row.id,
            metadata={"reason": "user_revoked"},
        )
        logger.info(
            "fcm_token_revoked",
            user_id=str(user_id),
            token_id=str(row.id),
        )

    @staticmethod
    async def deactivate_by_string(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        token: str,
    ) -> None:
        """Deactivate a token by its opaque string (used on FCM `UNREGISTERED`).

        Called by the Celery push task when FCM returns an unrecoverable
        token error. Writes a `notification.token.revoked` audit row
        attributed to the token's owner.
        """
        row = await db.scalar(select(FcmToken).where(FcmToken.token == token))
        if row is None or not row.is_active:
            return

        row.is_active = False
        await db.flush()

        await write_audit(
            db,
            actor_id=row.user_id,
            institution_id=institution_id,
            action=AuditEvent.NOTIFICATION_TOKEN_REVOKED,
            resource_type="fcm_token",
            resource_id=row.id,
            metadata={"reason": "fcm_unregistered"},
        )
        logger.info(
            "fcm_token_deactivated_by_fcm",
            token_id=str(row.id),
            user_id=str(row.user_id),
        )

    @staticmethod
    async def _revoke_stale_binding(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID,
        stale_row: FcmToken,
    ) -> None:
        """Remove a stale (user, token) binding before a new one is written.

        The token's UNIQUE constraint on the column means a second
        `INSERT` with the same string would violate the DB; deleting the
        row first keeps the audit trail intact (one revoke row + one
        registered row) and is safer than an `UPDATE` that silently
        rebinds.
        """
        prior_user_id = stale_row.user_id
        await db.delete(stale_row)
        await db.flush()
        await write_audit(
            db,
            actor_id=prior_user_id,
            institution_id=institution_id,
            action=AuditEvent.NOTIFICATION_TOKEN_REVOKED,
            resource_type="fcm_token",
            resource_id=stale_row.id,
            metadata={"reason": "rebound_to_another_user"},
        )

    @staticmethod
    async def list_active_tokens(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
    ) -> list[FcmToken]:
        """Return all currently-active tokens for a given user.

        The fan-out path calls this once per recipient. The partial index
        `idx_fcm_tokens_user_active` keeps it O(log n) per call.
        """
        rows = await db.scalars(
            select(FcmToken).where(
                FcmToken.user_id == str(user_id),
                FcmToken.is_active.is_(True),
            )
        )
        return list(rows)
