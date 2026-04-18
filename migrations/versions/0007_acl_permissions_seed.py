"""seed the permission catalog for the ACL module

Revision ID: 0007_acl_permissions_seed
Revises: 0006_notifications_schema
Create Date: 2026-04-18

The baseline migration (0001) already ships the `roles`, `permissions`,
`role_permissions`, and `user_roles` tables. The ACL module layers its
canonical permission codespace on top — 15 permission codes that module
authors reference by name.

Seeding via migration keeps the catalog reproducible across every
environment. `ON CONFLICT (code) DO NOTHING` makes this idempotent so
re-running the migration against a DB that already has the codes is a
no-op; matches the ACLService.seed_permissions() runtime bootstrap.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_acl_permissions_seed"
down_revision: str | None = "0006_notifications_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
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


def upgrade() -> None:
    for code, name, description, module in _PERMISSIONS:
        op.execute(
            "INSERT INTO permissions (id, code, name, description, module, created_at, updated_at) "
            f"VALUES (gen_random_uuid(), '{code}', '{name}', '{description}', '{module}', NOW(), NOW()) "
            "ON CONFLICT (code) DO NOTHING"
        )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code, *_ in _PERMISSIONS)
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
