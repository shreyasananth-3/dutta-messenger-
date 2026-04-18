"""Service-layer tests for ACLService."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.models.db_models import Permission
from src.modules.acl.services.acl_service import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_CATALOG,
    SYSTEM_ROLES,
    ACLService,
)
from src.modules.auth.models.db_models import Institution, User
from src.shared.exceptions import NotFoundError, PermissionDeniedError
from tests.modules.acl.conftest import make_institution, make_user


class TestSeedPermissions:
    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self, db_session: AsyncSession) -> None:
        first = await ACLService.seed_permissions(db_session)
        second = await ACLService.seed_permissions(db_session)
        assert second == 0
        # All catalog codes present after either call
        from sqlalchemy import select

        codes = set((await db_session.execute(select(Permission.code))).scalars().all())
        for code, *_ in PERMISSION_CATALOG:
            assert code in codes
        # Not asserting first>0 because earlier tests may have seeded.
        _ = first


class TestSeedInstitutionRoles:
    @pytest.mark.asyncio
    async def test_seeds_three_system_roles(self, db_session: AsyncSession) -> None:
        inst = await make_institution(db_session, name="Seed Test")
        roles = await ACLService.list_roles(db_session, institution_id=inst.id)
        names = {r.name for r in roles}
        for name, *_ in SYSTEM_ROLES:
            assert name in names

    @pytest.mark.asyncio
    async def test_default_role_permissions_wired(self, db_session: AsyncSession) -> None:
        inst = await make_institution(db_session, name="Perm Wiring")
        # Add a user and assign 'member' — they should get member's perms.
        member = await make_user(db_session, institution=inst, email="m@x.test", role_name="member")
        perms = await ACLService.list_user_permissions(
            db_session, institution_id=inst.id, user_id=member.id
        )
        for code in DEFAULT_ROLE_PERMISSIONS["member"]:
            assert code in perms


class TestAssignRole:
    @pytest.mark.asyncio
    async def test_idempotent(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        roles = await ACLService.list_roles(db_session, institution_id=institution.id)
        member_role = next(r for r in roles if r.name == "member")

        other = await make_user(db_session, institution=institution, email="o@x.test")

        _row1, reused1 = await ACLService.assign_role(
            db_session,
            institution_id=institution.id,
            user_id=other.id,
            role_id=member_role.id,
            assigned_by=super_admin_user.id,
        )
        assert reused1 is False

        _row2, reused2 = await ACLService.assign_role(
            db_session,
            institution_id=institution.id,
            user_id=other.id,
            role_id=member_role.id,
            assigned_by=super_admin_user.id,
        )
        assert reused2 is True

    @pytest.mark.asyncio
    async def test_cross_tenant_role_is_not_found(
        self,
        db_session: AsyncSession,
    ) -> None:
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        roles_a = await ACLService.list_roles(db_session, institution_id=inst_a.id)
        role_from_a = next(r for r in roles_a if r.name == "member")
        user_b = await make_user(db_session, institution=inst_b)

        with pytest.raises(NotFoundError):
            await ACLService.assign_role(
                db_session,
                institution_id=inst_b.id,
                user_id=user_b.id,
                role_id=role_from_a.id,  # cross-tenant role
                assigned_by=user_b.id,
            )


class TestRevokeRole:
    @pytest.mark.asyncio
    async def test_super_admin_cannot_be_revoked(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        roles = await ACLService.list_roles(db_session, institution_id=institution.id)
        super_role = next(r for r in roles if r.name == "super_admin")
        with pytest.raises(PermissionDeniedError):
            await ACLService.revoke_role(
                db_session,
                institution_id=institution.id,
                user_id=super_admin_user.id,
                role_id=super_role.id,
                revoked_by=super_admin_user.id,
            )

    @pytest.mark.asyncio
    async def test_404_on_unknown_assignment(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        roles = await ACLService.list_roles(db_session, institution_id=institution.id)
        member_role = next(r for r in roles if r.name == "member")
        other = await make_user(db_session, institution=institution, email="ghost@x.test")
        with pytest.raises(NotFoundError):
            await ACLService.revoke_role(
                db_session,
                institution_id=institution.id,
                user_id=other.id,
                role_id=member_role.id,
                revoked_by=super_admin_user.id,
            )


class TestPermissionChecks:
    @pytest.mark.asyncio
    async def test_super_admin_has_manage_admins(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        ok = await ACLService.user_has_permission(
            db_session,
            institution_id=institution.id,
            user_id=super_admin_user.id,
            permission_code="institution.manage_admins",
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_member_cannot_manage_admins(
        self,
        db_session: AsyncSession,
        institution: Institution,
        member_user: User,
    ) -> None:
        ok = await ACLService.user_has_permission(
            db_session,
            institution_id=institution.id,
            user_id=member_user.id,
            permission_code="institution.manage_admins",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_cross_institution_permission_blocked(
        self,
        db_session: AsyncSession,
    ) -> None:
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        admin_a = await make_user(
            db_session,
            institution=inst_a,
            email="admin-a@x.test",
            role_name="super_admin",
        )
        # Same user_id probed against inst_b's scope — must be False.
        ok = await ACLService.user_has_permission(
            db_session,
            institution_id=inst_b.id,
            user_id=admin_a.id,
            permission_code="institution.manage_admins",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_unknown_permission_false(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        ok = await ACLService.user_has_permission(
            db_session,
            institution_id=institution.id,
            user_id=super_admin_user.id,
            permission_code="nope.does_not_exist",
        )
        assert ok is False


class TestAuditOnAssign:
    @pytest.mark.asyncio
    async def test_assign_writes_audit_row(
        self,
        db_session: AsyncSession,
        institution: Institution,
        super_admin_user: User,
    ) -> None:
        from sqlalchemy import text

        roles = await ACLService.list_roles(db_session, institution_id=institution.id)
        member_role = next(r for r in roles if r.name == "member")
        target = await make_user(db_session, institution=institution, email="t@x.test")
        await ACLService.assign_role(
            db_session,
            institution_id=institution.id,
            user_id=target.id,
            role_id=member_role.id,
            assigned_by=super_admin_user.id,
        )
        row = (
            (
                await db_session.execute(
                    text(
                        "SELECT action, resource_type FROM audit_logs "
                        "WHERE actor_id = :a AND action = :act ORDER BY created_at DESC"
                    ),
                    {"a": super_admin_user.id, "act": "acl.role.granted"},
                )
            )
            .mappings()
            .first()
        )
        assert row is not None
        assert row["resource_type"] == "user_role"
