"""SQLAlchemy ORM models for the ACL module.

Column names, types, and nullability mirror the shipped baseline schema
(`migrations/001_init_schema.sql`, §ACL MODULE). The reference-docs
schema drifts in places (e.g. uses `codename` vs the baseline's `code`);
we follow the baseline because that's what's in the database.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.shared.database import Base, BaseModel


class Role(BaseModel):
    """Role within an institution.

    Institution-scoped. System roles (super_admin, admin, member) are
    seeded on institution creation via the AuthService bootstrap.
    Custom roles may be created by super_admin.
    """

    __tablename__ = "roles"

    institution_id = Column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    level = Column(Integer, nullable=False)
    is_system_role = Column(Boolean, default=False, nullable=False)

    __table_args__ = (UniqueConstraint("institution_id", "name", name="uq_role_inst_name"),)


class Permission(Base):
    """Granular capability that can be assigned to a role.

    Global (no `institution_id`) — permission codenames are part of the
    product's codespace, not per-tenant state. Seeded once by
    migration 0007_acl_permissions_seed.
    """

    __tablename__ = "permissions"

    id = Column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        nullable=False,
    )
    code = Column(String(100), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    module = Column(String(100), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RolePermission(Base):
    """Many-to-many mapping of roles to permissions."""

    __tablename__ = "role_permissions"

    role_id = Column(
        UUID(as_uuid=False),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    permission_id = Column(
        UUID(as_uuid=False),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    granted_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserRole(Base):
    """Many-to-many mapping of users to roles."""

    __tablename__ = "user_roles"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    role_id = Column(
        UUID(as_uuid=False),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    assigned_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    assigned_by_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
