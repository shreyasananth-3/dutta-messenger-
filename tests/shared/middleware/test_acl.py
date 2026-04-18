"""Tests for the ACL decorator + check helpers.

The acl module imports models from `src.modules.acl` and `src.modules.groups`
(which haven't been built yet). We exercise both:
  1. The decorator wiring (no-context branches that don't touch DB).
  2. The DB-backed check helpers, by stubbing the deferred imports with
     lightweight SQLAlchemy models bound to the same Base + temporary tables.
"""

from __future__ import annotations

import sys
import uuid
from types import ModuleType

import pytest
import pytest_asyncio
from sqlalchemy import Column, ForeignKey, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base

from src.shared.exceptions import PermissionDeniedError
from src.shared.middleware.acl import (
    check_group_membership,
    check_user_permission,
    check_user_role,
    require_permission,
    require_role,
)

# ---------------------------------------------------------------------------
# Stub `src.modules.acl.models.db_models` and groups equivalent so the
# deferred imports inside the acl helpers resolve without the real modules.
# ---------------------------------------------------------------------------
_AclBase = declarative_base()


class _StubRole(_AclBase):
    __tablename__ = "_acl_test_roles"
    id = Column(String, primary_key=True)
    institution_id = Column(String, nullable=False)
    name = Column(String, nullable=False)


class _StubPermission(_AclBase):
    __tablename__ = "_acl_test_permissions"
    id = Column(String, primary_key=True)
    code = Column(String, nullable=False)


class _StubRolePermission(_AclBase):
    __tablename__ = "_acl_test_role_permissions"
    role_id = Column(String, ForeignKey("_acl_test_roles.id"), primary_key=True)
    permission_id = Column(String, ForeignKey("_acl_test_permissions.id"), primary_key=True)


class _StubUserRole(_AclBase):
    __tablename__ = "_acl_test_user_roles"
    user_id = Column(String, primary_key=True)
    role_id = Column(String, ForeignKey("_acl_test_roles.id"), primary_key=True)


class _StubGroupMember(_AclBase):
    __tablename__ = "_acl_test_group_members"
    user_id = Column(String, primary_key=True)
    group_id = Column(String, primary_key=True)


def _install_stub_modules() -> None:
    acl_mod = ModuleType("src.modules.acl.models.db_models")
    acl_mod.UserRole = _StubUserRole  # type: ignore[attr-defined]
    acl_mod.Role = _StubRole  # type: ignore[attr-defined]
    acl_mod.RolePermission = _StubRolePermission  # type: ignore[attr-defined]
    acl_mod.Permission = _StubPermission  # type: ignore[attr-defined]
    sys.modules["src.modules.acl"] = ModuleType("src.modules.acl")
    sys.modules["src.modules.acl.models"] = ModuleType("src.modules.acl.models")
    sys.modules["src.modules.acl.models.db_models"] = acl_mod

    groups_mod = ModuleType("src.modules.groups.models.db_models")
    groups_mod.GroupMember = _StubGroupMember  # type: ignore[attr-defined]
    sys.modules["src.modules.groups"] = ModuleType("src.modules.groups")
    sys.modules["src.modules.groups.models"] = ModuleType("src.modules.groups.models")
    sys.modules["src.modules.groups.models.db_models"] = groups_mod


_install_stub_modules()


@pytest_asyncio.fixture
async def acl_tables(db_session: AsyncSession) -> AsyncSession:
    """Create the stub tables once per test inside its rolled-back transaction."""
    bind = await db_session.connection()
    await bind.run_sync(_AclBase.metadata.create_all)
    return db_session


# ---------------------------------------------------------------------------
# Decorator branches that don't touch the DB
# ---------------------------------------------------------------------------


class TestRequirePermissionDecorator:
    @pytest.mark.asyncio
    async def test_missing_user_context_raises(self) -> None:
        @require_permission("CREATE_GROUP")
        async def handler(**kwargs: object) -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError):
            await handler()

    @pytest.mark.asyncio
    async def test_user_with_permission_passes_through(self, acl_tables: AsyncSession) -> None:
        uid, inst, role_id, perm_id = (str(uuid.uuid4()) for _ in range(4))
        await acl_tables.execute(
            text(
                "INSERT INTO _acl_test_roles (id, institution_id, name) VALUES (:i, :inst, 'admin')"
            ),
            {"i": role_id, "inst": inst},
        )
        await acl_tables.execute(
            text("INSERT INTO _acl_test_permissions (id, code) VALUES (:i, 'CREATE_GROUP')"),
            {"i": perm_id},
        )
        await acl_tables.execute(
            text("INSERT INTO _acl_test_role_permissions (role_id, permission_id) VALUES (:r, :p)"),
            {"r": role_id, "p": perm_id},
        )
        await acl_tables.execute(
            text("INSERT INTO _acl_test_user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": uid, "r": role_id},
        )

        @require_permission("CREATE_GROUP")
        async def handler(**kwargs: object) -> str:
            return "ok"

        result = await handler(
            current_user={"user_id": uuid.UUID(uid), "institution_id": uuid.UUID(inst)},
            db=acl_tables,
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_user_without_permission_denied(self, acl_tables: AsyncSession) -> None:
        @require_permission("DELETE_USER")
        async def handler(**kwargs: object) -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError):
            await handler(
                current_user={
                    "user_id": uuid.uuid4(),
                    "institution_id": uuid.uuid4(),
                },
                db=acl_tables,
            )


class TestRequireRoleDecorator:
    @pytest.mark.asyncio
    async def test_missing_user_context_raises(self) -> None:
        @require_role("admin")
        async def handler(**kwargs: object) -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError):
            await handler()

    @pytest.mark.asyncio
    async def test_user_with_role_passes_through(self, acl_tables: AsyncSession) -> None:
        uid, inst, role_id = (str(uuid.uuid4()) for _ in range(3))
        await acl_tables.execute(
            text(
                "INSERT INTO _acl_test_roles (id, institution_id, name) VALUES (:i, :inst, 'admin')"
            ),
            {"i": role_id, "inst": inst},
        )
        await acl_tables.execute(
            text("INSERT INTO _acl_test_user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": uid, "r": role_id},
        )

        @require_role("admin")
        async def handler(**kwargs: object) -> str:
            return "ok"

        result = await handler(
            current_user={"user_id": uuid.UUID(uid), "institution_id": uuid.UUID(inst)},
            db=acl_tables,
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_user_without_role_denied(self, acl_tables: AsyncSession) -> None:
        @require_role("admin")
        async def handler(**kwargs: object) -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError):
            await handler(
                current_user={
                    "user_id": uuid.uuid4(),
                    "institution_id": uuid.uuid4(),
                },
                db=acl_tables,
            )


class TestCheckGroupMembership:
    @pytest.mark.asyncio
    async def test_missing_membership_returns_false(self, acl_tables: AsyncSession) -> None:
        ok = await check_group_membership(uuid.uuid4(), uuid.uuid4(), acl_tables)
        assert ok is False

    @pytest.mark.asyncio
    async def test_existing_membership_returns_true(self, acl_tables: AsyncSession) -> None:
        uid, gid = uuid.uuid4(), uuid.uuid4()
        await acl_tables.execute(
            text("INSERT INTO _acl_test_group_members (user_id, group_id) VALUES (:u, :g)"),
            {"u": str(uid), "g": str(gid)},
        )
        assert await check_group_membership(uid, gid, acl_tables) is True


class TestCheckHelpersDirect:
    @pytest.mark.asyncio
    async def test_check_user_permission_false_when_unset(self, acl_tables: AsyncSession) -> None:
        ok = await check_user_permission(uuid.uuid4(), uuid.uuid4(), "ANYTHING", acl_tables)
        assert ok is False

    @pytest.mark.asyncio
    async def test_check_user_role_false_when_unset(self, acl_tables: AsyncSession) -> None:
        ok = await check_user_role(uuid.uuid4(), uuid.uuid4(), "admin", acl_tables)
        assert ok is False
