"""HTTP routes for the ACL module.

Four endpoints per `reference-docs/modules/acl/MODULE.md`:
  GET    /api/v1/acl/roles
  POST   /api/v1/acl/users/{user_id}/roles
  DELETE /api/v1/acl/users/{user_id}/roles/{role_id}
  GET    /api/v1/acl/users/{user_id}/permissions

All endpoints require `institution.manage_admins` except the last which
allows self-lookup ("or self" per MODULE.md §API Endpoints).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.acl.models.request_models import AssignRoleRequest
from src.modules.acl.models.response_models import (
    AssignRoleResponse,
    PermissionResponse,
    RoleResponse,
    UserPermissionsResponse,
)
from src.modules.acl.services.acl_service import ACLService
from src.shared import realtime
from src.shared.database import get_db
from src.shared.exceptions import PermissionDeniedError
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

logger = structlog.get_logger()
router = APIRouter(prefix="/acl", tags=["acl"])


async def _require_manage_admins(current_user: dict[str, Any], db: AsyncSession) -> None:
    """Permission guard for admin-only ACL endpoints."""
    allowed = await ACLService.user_has_permission(
        db,
        institution_id=current_user["institution_id"],
        user_id=current_user["user_id"],
        permission_code="institution.manage_admins",
    )
    if not allowed:
        raise PermissionDeniedError("institution.manage_admins permission required")


@router.get(
    "/roles",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def list_roles(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List every role in the caller's institution."""
    await _require_manage_admins(current_user, db)
    rows = await ACLService.list_roles(db, institution_id=current_user["institution_id"])
    return success_response([RoleResponse.model_validate(r) for r in rows])


@router.post(
    "/users/{user_id}/roles",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def assign_role(
    user_id: uuid.UUID,
    data: AssignRoleRequest,
    background_tasks: BackgroundTasks,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Assign a role to a user. Idempotent."""
    await _require_manage_admins(current_user, db)
    row, reused = await ACLService.assign_role(
        db,
        institution_id=current_user["institution_id"],
        user_id=user_id,
        role_id=data.role_id,
        assigned_by=current_user["user_id"],
    )
    # Push the change to every device the target user has open so their
    # client can re-fetch /users/me and reflect the new permissions
    # without requiring a sign-out (audit 4.7 cross-device gap). Skip
    # the broadcast when the assignment was already in place — nothing
    # to tell the client.
    if not reused:
        background_tasks.add_task(
            realtime.broadcast_to_user,
            str(user_id),
            {"type": "user.role_changed"},
        )
    return success_response(
        AssignRoleResponse(
            user_id=uuid.UUID(str(row.user_id)),
            role_id=uuid.UUID(str(row.role_id)),
            assigned_by=uuid.UUID(str(row.assigned_by_user_id))
            if row.assigned_by_user_id
            else None,
            reused=reused,
        )
    )


@router.delete(
    "/users/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a role from a user."""
    await _require_manage_admins(current_user, db)
    await ACLService.revoke_role(
        db,
        institution_id=current_user["institution_id"],
        user_id=user_id,
        role_id=role_id,
        revoked_by=current_user["user_id"],
    )
    # Same cross-device push as assign — the target user's other
    # sessions need to drop any cached permission that this role granted.
    background_tasks.add_task(
        realtime.broadcast_to_user,
        str(user_id),
        {"type": "user.role_changed"},
    )


@router.get(
    "/users/{user_id}/permissions",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def list_user_permissions(
    user_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Effective roles + permissions for a user. Self or admin-only."""
    is_self = str(user_id) == str(current_user["user_id"])
    if not is_self:
        await _require_manage_admins(current_user, db)

    roles = await ACLService.list_user_roles(
        db,
        institution_id=current_user["institution_id"],
        user_id=user_id,
    )
    perms = await ACLService.list_user_permissions(
        db,
        institution_id=current_user["institution_id"],
        user_id=user_id,
    )
    _ = PermissionResponse  # imported for future per-permission detail path
    return success_response(
        UserPermissionsResponse(
            user_id=user_id,
            roles=[RoleResponse.model_validate(r) for r in roles],
            permissions=perms,
        )
    )
