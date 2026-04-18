"""Business logic for the ACL module.

Every permission check in the product flows through this module. All
methods here are tenant-scoped: role lookups always filter by
`institution_id`, and cross-tenant probes return empty / `False` so no
403-vs-404 existence side-channel leaks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.models.db_models import Permission, Role, RolePermission, UserRole
from src.shared.exceptions import NotFoundError, PermissionDeniedError
from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.tenant import tenant_scoped_query

logger = structlog.get_logger()


SYSTEM_ROLES: tuple[tuple[str, int, str], ...] = (
    ("super_admin", 0, "Full control over the institution."),
    ("admin", 1, "Manage users, create groups, moderate messages."),
    ("member", 2, "Send messages, join groups, upload files."),
)
"""Default roles seeded per institution — (name, level, description)."""


DEFAULT_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "super_admin": (
        "institution.manage_settings",
        "institution.manage_admins",
        "institution.manage_users",
        "institution.view_audit_log",
        "group.create",
        "group.delete",
        "group.manage_members",
        "group.manage_settings",
        "group.send_message",
        "chat.send_message",
        "chat.delete_own_message",
        "chat.delete_any_message",
        "chat.edit_own_message",
        "media.upload",
        "media.download",
    ),
    "admin": (
        "institution.manage_users",
        "institution.view_audit_log",
        "group.create",
        "group.manage_members",
        "group.manage_settings",
        "group.send_message",
        "chat.send_message",
        "chat.delete_own_message",
        "chat.delete_any_message",
        "chat.edit_own_message",
        "media.upload",
        "media.download",
    ),
    "member": (
        "group.send_message",
        "chat.send_message",
        "chat.delete_own_message",
        "chat.edit_own_message",
        "media.upload",
        "media.download",
    ),
}
"""Default permission grants per system role. Keep aligned with MODULE.md."""


PERMISSION_CATALOG: tuple[tuple[str, str, str, str], ...] = (
    (
        "institution.manage_settings",
        "Manage institution settings",
        "Change institution-level settings",
        "institution",
    ),
    (
        "institution.manage_admins",
        "Manage admins",
        "Promote or demote institution admins",
        "institution",
    ),
    (
        "institution.manage_users",
        "Manage users",
        "Invite, deactivate, or update users",
        "institution",
    ),
    (
        "institution.view_audit_log",
        "View audit log",
        "Read the institution audit trail",
        "institution",
    ),
    ("group.create", "Create groups", "Create new groups (simple or topic-enabled)", "groups"),
    ("group.delete", "Delete groups", "Delete a group", "groups"),
    ("group.manage_members", "Manage group members", "Add or remove group members", "groups"),
    ("group.manage_settings", "Manage group settings", "Change group name, avatar, mode", "groups"),
    ("group.send_message", "Send message in group", "Send messages in a group or topic", "groups"),
    ("chat.send_message", "Send message", "Send a DM or group message", "chat"),
    ("chat.delete_own_message", "Delete own message", "Delete own messages", "chat"),
    ("chat.delete_any_message", "Moderate messages", "Delete any message", "chat"),
    ("chat.edit_own_message", "Edit own message", "Edit own messages", "chat"),
    ("media.upload", "Upload media", "Upload files", "media"),
    ("media.download", "Download media", "Download files", "media"),
)
"""The canonical permission catalog. Seeded at bootstrap — see `seed_permissions`."""


@dataclass(frozen=True)
class PermissionCheck:
    """Structured result of a permission check, for logging."""

    user_id: uuid.UUID
    permission: str
    allowed: bool


class ACLService:
    """All ACL reads, writes, and permission checks."""

    @staticmethod
    async def list_roles(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
    ) -> list[Role]:
        """Return every role in the caller's institution, newest-first."""
        result = await db.execute(
            tenant_scoped_query(Role, institution_id).order_by(Role.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_role(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        role_id: uuid.UUID | str,
    ) -> Role:
        """Fetch a role, raising `NotFoundError` on miss or cross-tenant."""
        result = await db.execute(
            tenant_scoped_query(Role, institution_id).where(Role.id == str(role_id))
        )
        role = result.scalar_one_or_none()
        if role is None:
            raise NotFoundError("role", str(role_id))
        return role

    @staticmethod
    async def list_user_roles(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
    ) -> list[Role]:
        """Every role assigned to `user_id` within `institution_id`."""
        result = await db.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == str(user_id),
                Role.institution_id == str(institution_id),
            )
            .order_by(Role.level.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_user_permissions(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
    ) -> list[str]:
        """Distinct permission codes the user has via any role."""
        result = await db.execute(
            select(Permission.code)
            .distinct()
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == str(user_id),
                Role.institution_id == str(institution_id),
            )
        )
        return sorted(row for row in result.scalars().all())

    @staticmethod
    async def user_has_permission(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        permission_code: str,
    ) -> bool:
        """Boolean permission check — tenant-scoped."""
        result = await db.execute(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == str(user_id),
                Role.institution_id == str(institution_id),
                Permission.code == permission_code,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def assign_role(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        role_id: uuid.UUID | str,
        assigned_by: uuid.UUID | str,
    ) -> tuple[UserRole, bool]:
        """Grant a role to a user. Idempotent — returns (row, reused).

        Raises:
            NotFoundError: role does not exist in this institution.
            ConflictError: never (idempotent); kept for future semantics.
        """
        role = await ACLService.get_role(db, institution_id=institution_id, role_id=role_id)

        existing = await db.scalar(
            select(UserRole).where(
                UserRole.user_id == str(user_id),
                UserRole.role_id == str(role.id),
            )
        )
        if existing is not None:
            return existing, True

        row = UserRole(
            user_id=str(user_id),
            role_id=str(role.id),
            assigned_by_user_id=str(assigned_by) if assigned_by else None,
        )
        db.add(row)
        await db.flush()
        await write_audit(
            db,
            actor_id=assigned_by,
            institution_id=institution_id,
            action=AuditEvent.ROLE_GRANTED,
            resource_type="user_role",
            resource_id=None,
            metadata={
                "user_id": str(user_id),
                "role_id": str(role.id),
                "role_name": role.name,
            },
        )
        return row, False

    @staticmethod
    async def revoke_role(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        role_id: uuid.UUID | str,
        revoked_by: uuid.UUID | str,
    ) -> None:
        """Remove a role from a user. 404 if no such assignment.

        Super-admin role cannot be revoked via this path — a separate
        transfer-ownership flow is required to keep at least one
        super_admin per institution at all times.
        """
        role = await ACLService.get_role(db, institution_id=institution_id, role_id=role_id)
        if role.name == "super_admin" and role.is_system_role:
            raise PermissionDeniedError(
                "super_admin cannot be revoked directly — transfer ownership first"
            )

        existing = await db.scalar(
            select(UserRole).where(
                UserRole.user_id == str(user_id),
                UserRole.role_id == str(role.id),
            )
        )
        if existing is None:
            raise NotFoundError("user_role", f"{user_id}:{role_id}")

        await db.delete(existing)
        await write_audit(
            db,
            actor_id=revoked_by,
            institution_id=institution_id,
            action=AuditEvent.ROLE_REVOKED,
            resource_type="user_role",
            resource_id=None,
            metadata={
                "user_id": str(user_id),
                "role_id": str(role.id),
                "role_name": role.name,
            },
        )

    @staticmethod
    async def seed_permissions(db: AsyncSession) -> int:
        """Insert any missing rows from PERMISSION_CATALOG.

        Idempotent. Safe to call at app boot, on migration, or from tests.
        Returns the number of rows inserted.
        """
        existing = set((await db.execute(select(Permission.code))).scalars().all())
        inserted = 0
        for code, name, description, module in PERMISSION_CATALOG:
            if code in existing:
                continue
            db.add(
                Permission(
                    code=code,
                    name=name,
                    description=description,
                    module=module,
                )
            )
            inserted += 1
        if inserted:
            await db.flush()
        return inserted

    @staticmethod
    async def seed_institution_roles(
        db: AsyncSession,
        *,
        institution_id: uuid.UUID | str,
    ) -> dict[str, Role]:
        """Seed the three system roles for a new institution.

        Returns a `{name: Role}` map for the caller (e.g. the auth
        bootstrap then assigns the creating user to `super_admin`).
        """
        await ACLService.seed_permissions(db)

        by_code = {p.code: p for p in (await db.execute(select(Permission))).scalars().all()}

        roles: dict[str, Role] = {}
        for name, level, description in SYSTEM_ROLES:
            existing = await db.scalar(
                select(Role).where(
                    Role.institution_id == str(institution_id),
                    Role.name == name,
                )
            )
            if existing is not None:
                roles[name] = existing
                continue
            role = Role(
                institution_id=str(institution_id),
                name=name,
                level=level,
                description=description,
                is_system_role=True,
            )
            db.add(role)
            await db.flush()
            roles[name] = role
            for code in DEFAULT_ROLE_PERMISSIONS[name]:
                perm = by_code.get(code)
                if perm is None:
                    continue
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        await db.flush()
        return roles
