"""HTTP-level tests for ACL routes — 7-point checklist + cross-tenant."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.services.acl_service import ACLService
from src.modules.auth.models.db_models import User
from tests.modules.acl.conftest import auth_header, make_institution, make_user

API = "/api/v1"


class TestListRoles:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        acl_client: AsyncClient,
        super_admin_user: User,
    ) -> None:
        r = await acl_client.get(f"{API}/acl/roles", headers=auth_header(super_admin_user))
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["data"]}
        assert {"super_admin", "admin", "member"} <= names

    @pytest.mark.asyncio
    async def test_unauth_401_or_403(self, acl_client: AsyncClient) -> None:
        r = await acl_client.get(f"{API}/acl/roles")
        assert r.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_member_forbidden_403(self, acl_client: AsyncClient, member_user: User) -> None:
        r = await acl_client.get(f"{API}/acl/roles", headers=auth_header(member_user))
        assert r.status_code == 403


class TestAssignRole:
    @pytest.mark.asyncio
    async def test_happy_path_and_idempotent(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
        super_admin_user: User,
    ) -> None:
        inst_id = super_admin_user.institution_id
        roles = await ACLService.list_roles(db_session, institution_id=inst_id)
        member_role = next(r for r in roles if r.name == "member")
        target = await make_user(
            db_session, institution=None or await _get_inst(db_session, inst_id), email="tgt@x.test"
        )

        first = await acl_client.post(
            f"{API}/acl/users/{target.id}/roles",
            headers=auth_header(super_admin_user),
            json={"role_id": str(member_role.id)},
        )
        assert first.status_code == 200
        assert first.json()["data"]["reused"] is False

        second = await acl_client.post(
            f"{API}/acl/users/{target.id}/roles",
            headers=auth_header(super_admin_user),
            json={"role_id": str(member_role.id)},
        )
        assert second.status_code == 200
        assert second.json()["data"]["reused"] is True

    @pytest.mark.asyncio
    async def test_member_forbidden(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
        member_user: User,
        super_admin_user: User,
    ) -> None:
        inst_id = super_admin_user.institution_id
        roles = await ACLService.list_roles(db_session, institution_id=inst_id)
        member_role = next(r for r in roles if r.name == "member")
        r = await acl_client.post(
            f"{API}/acl/users/{member_user.id}/roles",
            headers=auth_header(member_user),
            json={"role_id": str(member_role.id)},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_role_404(
        self,
        acl_client: AsyncClient,
        super_admin_user: User,
    ) -> None:
        r = await acl_client.post(
            f"{API}/acl/users/{super_admin_user.id}/roles",
            headers=auth_header(super_admin_user),
            json={"role_id": str(uuid.uuid4())},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_malformed_role_id_422(
        self,
        acl_client: AsyncClient,
        super_admin_user: User,
    ) -> None:
        r = await acl_client.post(
            f"{API}/acl/users/{super_admin_user.id}/roles",
            headers=auth_header(super_admin_user),
            json={"role_id": "not-a-uuid"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_cross_tenant_role_returns_404(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        inst_a = await make_institution(db_session, name="TenantA")
        inst_b = await make_institution(db_session, name="TenantB")
        admin_a = await make_user(
            db_session,
            institution=inst_a,
            email="aa@x.test",
            role_name="super_admin",
        )
        admin_b = await make_user(
            db_session,
            institution=inst_b,
            email="bb@x.test",
            role_name="super_admin",
        )
        roles_a = await ACLService.list_roles(db_session, institution_id=inst_a.id)
        role_from_a = next(r for r in roles_a if r.name == "member")

        r = await acl_client.post(
            f"{API}/acl/users/{admin_b.id}/roles",
            headers=auth_header(admin_b),
            json={"role_id": str(role_from_a.id)},
        )
        assert r.status_code == 404
        _ = admin_a  # silence unused


class TestRevokeRole:
    @pytest.mark.asyncio
    async def test_happy_path_returns_204(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
        super_admin_user: User,
        member_user: User,
    ) -> None:
        inst_id = super_admin_user.institution_id
        roles = await ACLService.list_roles(db_session, institution_id=inst_id)
        member_role = next(r for r in roles if r.name == "member")
        r = await acl_client.delete(
            f"{API}/acl/users/{member_user.id}/roles/{member_role.id}",
            headers=auth_header(super_admin_user),
        )
        assert r.status_code == 204

    @pytest.mark.asyncio
    async def test_super_admin_revoke_blocked(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
        super_admin_user: User,
    ) -> None:
        inst_id = super_admin_user.institution_id
        roles = await ACLService.list_roles(db_session, institution_id=inst_id)
        super_role = next(r for r in roles if r.name == "super_admin")
        r = await acl_client.delete(
            f"{API}/acl/users/{super_admin_user.id}/roles/{super_role.id}",
            headers=auth_header(super_admin_user),
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_assignment_404(
        self,
        acl_client: AsyncClient,
        db_session: AsyncSession,
        super_admin_user: User,
    ) -> None:
        inst_id = super_admin_user.institution_id
        roles = await ACLService.list_roles(db_session, institution_id=inst_id)
        member_role = next(r for r in roles if r.name == "member")
        ghost_id = uuid.uuid4()
        r = await acl_client.delete(
            f"{API}/acl/users/{ghost_id}/roles/{member_role.id}",
            headers=auth_header(super_admin_user),
        )
        assert r.status_code == 404


class TestListUserPermissions:
    @pytest.mark.asyncio
    async def test_self_can_read_own_permissions(
        self,
        acl_client: AsyncClient,
        member_user: User,
    ) -> None:
        r = await acl_client.get(
            f"{API}/acl/users/{member_user.id}/permissions",
            headers=auth_header(member_user),
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert "chat.send_message" in data["permissions"]

    @pytest.mark.asyncio
    async def test_super_admin_can_read_others(
        self,
        acl_client: AsyncClient,
        super_admin_user: User,
        member_user: User,
    ) -> None:
        r = await acl_client.get(
            f"{API}/acl/users/{member_user.id}/permissions",
            headers=auth_header(super_admin_user),
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_member_cannot_read_others(
        self,
        acl_client: AsyncClient,
        super_admin_user: User,
        member_user: User,
    ) -> None:
        r = await acl_client.get(
            f"{API}/acl/users/{super_admin_user.id}/permissions",
            headers=auth_header(member_user),
        )
        assert r.status_code == 403


async def _get_inst(db_session: AsyncSession, inst_id: str):
    from sqlalchemy import select

    from src.modules.auth.models.db_models import Institution

    r = await db_session.execute(select(Institution).where(Institution.id == str(inst_id)))
    inst = r.scalar_one()
    return inst
