"""Audit logging for mutating actions.

Every mutation that matters for compliance or operator investigation writes a
row to the `audit_logs` table (created in the baseline migration). Read-only
operations are NOT audited — doing so would generate noise and privacy issues.

Usage (from a service method):

    await write_audit(
        db,
        actor_id=user.id,
        institution_id=user.institution_id,
        action=AuditEvent.MESSAGE_DELETED,
        resource_type="message",
        resource_id=message.id,
        metadata={"conversation_id": str(conv.id)},
    )

Failures are swallowed and logged — the audit write must never break the
user's happy path. If the audit table is down, operators find out from
metrics (`dutta_auth_failures_total`-style counters can be added as needed),
not from a 500 on an otherwise-successful message send.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
"""Sentinel UUID for system / background actors (seed script, Celery housekeeping).

Use this when `write_audit()` is called outside an authenticated request.
Per `docs/design/tenant-isolation.md` §2.1 — the `actor_id` column is
`NOT NULL`, so a sentinel beats a nullable column.
"""


class AuditEvent(StrEnum):
    """Canonical list of audited actions.

    Adding a new event here is intentional — a new reviewable surface. Keep
    names stable; operators will build queries against them.
    """

    USER_REGISTERED = "user.registered"
    USER_LOGIN_SUCCESS = "user.login.success"
    USER_LOGIN_FAILURE = "user.login.failure"
    USER_PASSWORD_CHANGED = "user.password.changed"
    USER_DELETED = "user.deleted"
    USER_PROFILE_UPDATED = "user.profile.updated"
    USER_SUSPENDED = "user.suspended"
    USER_REACTIVATED = "user.reactivated"
    USER_SETTINGS_UPDATED = "user.settings.updated"
    ROLE_GRANTED = "acl.role.granted"
    ROLE_REVOKED = "acl.role.revoked"
    GROUP_CREATED = "group.created"
    GROUP_MEMBER_ADDED = "group.member.added"
    GROUP_MEMBER_REMOVED = "group.member.removed"
    MESSAGE_DELETED = "message.deleted"
    MESSAGE_EDITED = "message.edited"
    MEDIA_UPLOADED = "media.uploaded"
    MEDIA_DELETED = "media.deleted"
    MEDIA_RECYCLE_BIN_ENTERED = "media.recycle_bin_entered"
    MEDIA_PERMANENTLY_DELETED = "media.permanently_deleted"
    NOTIFICATION_TOKEN_REGISTERED = "notification.token.registered"


@dataclass(frozen=True)
class _AuditRow:
    actor_id: str
    institution_id: str
    action: str
    resource_type: str
    resource_id: str | None
    metadata_json: str


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | str,
    institution_id: uuid.UUID | str,
    action: AuditEvent,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write one row to `audit_logs`. Never raises to the caller."""
    import json

    row = _AuditRow(
        actor_id=str(actor_id),
        institution_id=str(institution_id),
        action=str(action),
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        metadata_json=json.dumps(metadata or {}),
    )
    try:
        await db.execute(
            text(
                """
                INSERT INTO audit_logs
                    (id, actor_id, institution_id, action,
                     resource_type, resource_id, metadata, created_at)
                VALUES
                    (gen_random_uuid(), :actor_id, :institution_id, :action,
                     :resource_type, :resource_id, CAST(:metadata AS JSONB), NOW())
                """
            ),
            {
                "actor_id": row.actor_id,
                "institution_id": row.institution_id,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "metadata": row.metadata_json,
            },
        )
    except Exception as exc:  # audit is best-effort; business path must succeed
        logger.error(
            "audit_write_failed",
            error=str(exc),
            action=row.action,
            actor_id=row.actor_id,
        )
