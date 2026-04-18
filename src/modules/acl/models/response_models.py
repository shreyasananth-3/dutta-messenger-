"""Pydantic response models for the ACL module."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RoleResponse(BaseModel):
    """Role record returned by list and detail endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    institution_id: uuid.UUID
    name: str
    description: str | None
    level: int
    is_system_role: bool
    created_at: datetime


class PermissionResponse(BaseModel):
    """Permission record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    module: str


class UserPermissionsResponse(BaseModel):
    """Effective permission set + roles for a user."""

    user_id: uuid.UUID
    roles: list[RoleResponse]
    permissions: list[str]


class AssignRoleResponse(BaseModel):
    """Outcome of assigning a role to a user."""

    user_id: uuid.UUID
    role_id: uuid.UUID
    assigned_by: uuid.UUID | None
    reused: bool
