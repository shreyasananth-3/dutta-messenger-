"""Pydantic request models for the ACL module."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AssignRoleRequest(BaseModel):
    """Body for `POST /api/v1/acl/users/{id}/roles`."""

    role_id: uuid.UUID = Field(..., description="ID of the role to assign.")
