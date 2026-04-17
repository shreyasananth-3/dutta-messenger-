"""Integration tests for audit_logs writes."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService
from src.shared.security.audit import AuditEvent, write_audit


@pytest_asyncio.fixture
async def actor_and_institution(
    db_session: AsyncSession,
) -> tuple[User, Institution]:
    """Create a real user + institution so the audit_logs FKs are satisfied."""
    inst = await AuthService.create_institution(
        db_session, name=f"AuditCo {uuid.uuid4().hex[:8]}"
    )
    await db_session.flush()
    user = await AuthService.register_user(
        db_session,
        institution_id=inst.id,
        email=f"actor-{uuid.uuid4().hex[:6]}@audit.test",
        password="Sup3rStr0ng!",
        full_name="Audit Actor",
    )
    await db_session.flush()
    return user, inst


@pytest.mark.integration
class TestWriteAudit:
    async def test_writes_row_with_design_columns(
        self,
        db_session: AsyncSession,
        actor_and_institution: tuple[User, Institution],
    ) -> None:
        actor, inst = actor_and_institution
        resource = uuid.uuid4()
        await write_audit(
            db_session,
            actor_id=actor.id,
            institution_id=inst.id,
            action=AuditEvent.USER_LOGIN_SUCCESS,
            resource_type="user",
            resource_id=resource,
            metadata={"ip": "127.0.0.1", "ua": "test"},
        )
        row = (
            await db_session.execute(
                text(
                    "SELECT actor_id, institution_id, action, resource_type, "
                    "resource_id, metadata FROM audit_logs WHERE actor_id = :a"
                ),
                {"a": actor.id},
            )
        ).mappings().first()
        assert row is not None
        assert str(row["actor_id"]) == actor.id
        assert str(row["institution_id"]) == inst.id
        assert row["action"] == "user.login.success"
        assert row["resource_type"] == "user"
        assert str(row["resource_id"]) == str(resource)
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta == {"ip": "127.0.0.1", "ua": "test"}

    async def test_resource_id_optional(
        self,
        db_session: AsyncSession,
        actor_and_institution: tuple[User, Institution],
    ) -> None:
        actor, inst = actor_and_institution
        await write_audit(
            db_session,
            actor_id=actor.id,
            institution_id=inst.id,
            action=AuditEvent.USER_PASSWORD_CHANGED,
            resource_type="user",
            resource_id=None,
        )
        row = (
            await db_session.execute(
                text(
                    "SELECT resource_id FROM audit_logs WHERE actor_id = :a"
                ),
                {"a": actor.id},
            )
        ).mappings().first()
        assert row is not None
        assert row["resource_id"] is None

    async def test_metadata_defaults_to_empty(
        self,
        db_session: AsyncSession,
        actor_and_institution: tuple[User, Institution],
    ) -> None:
        actor, inst = actor_and_institution
        await write_audit(
            db_session,
            actor_id=actor.id,
            institution_id=inst.id,
            action=AuditEvent.USER_REGISTERED,
            resource_type="user",
            resource_id=uuid.uuid4(),
            metadata=None,
        )
        row = (
            await db_session.execute(
                text("SELECT metadata FROM audit_logs WHERE actor_id = :a"),
                {"a": actor.id},
            )
        ).mappings().first()
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta == {}

    async def test_swallows_db_errors(self) -> None:
        """A broken session must not propagate — audit is best-effort."""

        class _BrokenSession:
            async def execute(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("simulated outage")

        await write_audit(
            _BrokenSession(),  # type: ignore[arg-type]
            actor_id=uuid.uuid4(),
            institution_id=uuid.uuid4(),
            action=AuditEvent.MESSAGE_DELETED,
            resource_type="message",
            resource_id=uuid.uuid4(),
        )

    def test_audit_event_enum_has_documented_actions(self) -> None:
        names = {e.value for e in AuditEvent}
        assert "user.login.success" in names
        assert "message.deleted" in names
        assert "acl.role.granted" in names
