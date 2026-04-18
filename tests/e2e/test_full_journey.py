"""End-to-end journey — in-process ASGITransport against a composed FastAPI app.

Unlike the module-level tests that mount a single router, this E2E test
builds a full app with every module enabled and walks through:

  1. create institution + admin user + ACL seed
  2. admin creates a simple group, adds 2 members
  3. all three users open the conversation
  4. member1 sends 3 messages via REST
  5. member2 lists messages, edits their reply, deletes one
  6. admin marks-read + queries audit log for compliance trail
  7. cross-institution user cannot see any of it (404 probe)
  8. soft-deleted messages are tombstoned (content = "[deleted]")

This is the smoke-as-test: if ANY module regresses, the journey breaks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.router import router as acl_router
from src.modules.acl.services.acl_service import ACLService
from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.router import router as chat_router
from src.modules.groups.router import router as groups_router
from src.shared.database import get_db
from src.shared.exceptions import AppException
from src.shared.middleware.auth import create_access_token


def _build_app() -> FastAPI:
    app = FastAPI(title="DuttaMessenger (E2E)")

    @app.exception_handler(AppException)
    async def _handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(acl_router, prefix="/api/v1")
    app.include_router(groups_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def e2e_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = _build_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def auth_header(user_id: str, institution_id: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.UUID(user_id),
        institution_id=uuid.UUID(institution_id),
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_institution_with_admin(db: AsyncSession, *, name: str):
    inst = await AuthService.create_institution(
        db, name=name, domain=f"{uuid.uuid4().hex[:6]}.test"
    )
    roles = await ACLService.seed_institution_roles(db, institution_id=inst.id)
    admin = await AuthService.register_user(
        db,
        institution_id=inst.id,
        email=f"admin-{uuid.uuid4().hex[:4]}@{name.lower().replace(' ', '')}.test",
        password="Sup3rStr0ng!",
        full_name="E2E Admin",
    )
    await ACLService.assign_role(
        db,
        institution_id=inst.id,
        user_id=admin.id,
        role_id=roles["super_admin"].id,
        assigned_by=admin.id,
    )
    await db.flush()
    return inst, admin


@pytest.mark.asyncio
async def test_full_chat_journey(db_session: AsyncSession, e2e_client: AsyncClient) -> None:
    """register → create group → add members → chat → edit → delete → verify."""
    inst, admin = await _seed_institution_with_admin(db_session, name="E2E School")
    member1 = await AuthService.register_user(
        db_session,
        institution_id=inst.id,
        email=f"m1-{uuid.uuid4().hex[:4]}@e2e.test",
        password="Sup3rStr0ng!",
        full_name="Member One",
    )
    member2 = await AuthService.register_user(
        db_session,
        institution_id=inst.id,
        email=f"m2-{uuid.uuid4().hex[:4]}@e2e.test",
        password="Sup3rStr0ng!",
        full_name="Member Two",
    )
    await db_session.flush()

    admin_hdr = auth_header(admin.id, admin.institution_id)
    m1_hdr = auth_header(member1.id, member1.institution_id)
    m2_hdr = auth_header(member2.id, member2.institution_id)

    # ---- 1. admin creates group
    r = await e2e_client.post(
        "/api/v1/groups",
        headers=admin_hdr,
        json={"name": "E2E Group", "mode": "simple"},
    )
    assert r.status_code == 201, r.text
    group_id = r.json()["data"]["id"]

    # ---- 2. admin adds both members
    for m in (member1, member2):
        r = await e2e_client.post(
            f"/api/v1/groups/{group_id}/members",
            headers=admin_hdr,
            json={"user_id": m.id, "role": "member"},
        )
        assert r.status_code == 200

    # ---- 3. all three open the conversation (idempotent)
    for hdr in (admin_hdr, m1_hdr, m2_hdr):
        r = await e2e_client.post(
            "/api/v1/chat/conversations/open-group",
            headers=hdr,
            json={"group_id": group_id},
        )
        assert r.status_code == 200
        conv_id = r.json()["data"]["id"]

    # ---- 4. member1 sends 3 messages
    msg_ids: list[str] = []
    for i in range(3):
        r = await e2e_client.post(
            f"/api/v1/chat/conversations/{conv_id}/messages",
            headers=m1_hdr,
            json={"content": f"hello from m1 #{i}"},
        )
        assert r.status_code == 201
        msg_ids.append(r.json()["data"]["id"])

    # ---- 5. member2 lists + replies + edits + deletes
    listing = await e2e_client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=m2_hdr)
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 3

    reply = await e2e_client.post(
        f"/api/v1/chat/conversations/{conv_id}/messages",
        headers=m2_hdr,
        json={"content": "reply from m2", "reply_to_message_id": msg_ids[0]},
    )
    assert reply.status_code == 201
    reply_id = reply.json()["data"]["id"]
    assert reply.json()["data"]["reply_to_message_id"] == msg_ids[0]

    edited = await e2e_client.patch(
        f"/api/v1/chat/messages/{reply_id}",
        headers=m2_hdr,
        json={"content": "reply from m2 — edited"},
    )
    assert edited.status_code == 200
    assert "edited" in edited.json()["data"]["content"]

    deleted = await e2e_client.delete(f"/api/v1/chat/messages/{msg_ids[1]}", headers=m1_hdr)
    assert deleted.status_code == 204

    # ---- 6. admin marks read + audit trail visible
    mark = await e2e_client.post(
        f"/api/v1/chat/conversations/{conv_id}/read",
        headers=admin_hdr,
        json={"last_read_message_id": msg_ids[2]},
    )
    assert mark.status_code == 200

    audit_count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs "
                "WHERE institution_id = :i AND action IN "
                "  ('group.created','group.member.added','message.edited','message.deleted')"
            ),
            {"i": inst.id},
        )
    ).scalar_one()
    # 1 group.created + 2 group.member.added + 1 message.edited + 1 message.deleted
    assert audit_count >= 5, f"expected >=5 audit rows, got {audit_count}"

    # ---- 7. cross-tenant leak probe
    _inst_b, admin_b = await _seed_institution_with_admin(db_session, name="Other School")
    b_hdr = auth_header(admin_b.id, admin_b.institution_id)

    leak = await e2e_client.get(f"/api/v1/groups/{group_id}", headers=b_hdr)
    assert leak.status_code == 404, "cross-tenant leak! 404 expected"

    leak_messages = await e2e_client.get(
        f"/api/v1/chat/conversations/{conv_id}/messages", headers=b_hdr
    )
    assert leak_messages.status_code == 404

    # ---- 8. soft-delete verification: tombstone preserved in listing
    after_delete = await e2e_client.get(
        f"/api/v1/chat/conversations/{conv_id}/messages", headers=m2_hdr
    )
    deleted_row = next(m for m in after_delete.json()["data"] if m["id"] == msg_ids[1])
    assert deleted_row["content"] == "[deleted]"
    assert deleted_row["deleted_at"] is not None
