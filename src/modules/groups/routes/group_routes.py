"""HTTP routes for the groups module.

Eleven endpoints covering group CRUD, membership, and topics. Each
route is ≤15 lines and delegates straight to GroupService. All
endpoints require `auth`; role enforcement is per-group (owner /
admin / member) inside the service.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.groups.models.request_models import (
    AddMemberRequest,
    CreateGroupRequest,
    CreateTopicRequest,
    UpdateGroupRequest,
)
from src.modules.groups.models.response_models import (
    AddMemberResponse,
    GroupMemberResponse,
    GroupResponse,
    TopicResponse,
)
from src.modules.groups.services.group_service import GroupService
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

logger = structlog.get_logger()
router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_group(
    data: CreateGroupRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a group with the caller as owner."""
    group = await GroupService.create_group(
        db,
        institution_id=current_user["institution_id"],
        creator_id=current_user["user_id"],
        name=data.name,
        description=data.description,
        mode=data.mode,
    )
    return success_response(GroupResponse.model_validate(group))


@router.get("", response_model=dict[str, Any])
async def list_my_groups(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List groups the caller is a member of."""
    groups = await GroupService.list_user_groups(
        db,
        institution_id=current_user["institution_id"],
        user_id=current_user["user_id"],
    )
    return success_response([GroupResponse.model_validate(g) for g in groups])


@router.get("/{group_id}", response_model=dict[str, Any])
async def get_group(
    group_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get a single group (caller must be a member)."""
    await GroupService._require_membership(db, group_id=group_id, user_id=current_user["user_id"])
    group = await GroupService.get_group(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
    )
    return success_response(GroupResponse.model_validate(group))


@router.patch("/{group_id}", response_model=dict[str, Any])
async def update_group(
    group_id: uuid.UUID,
    data: UpdateGroupRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update group metadata — admin/owner only."""
    group = await GroupService.update_group(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
        name=data.name,
        description=data.description,
        avatar_url=data.avatar_url,
    )
    return success_response(GroupResponse.model_validate(group))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_group(
    group_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete (archive) a group — owner only."""
    await GroupService.archive_group(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
    )


@router.get("/{group_id}/members", response_model=dict[str, Any])
async def list_members(
    group_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List group members (any member can read)."""
    rows = await GroupService.list_members(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
    )
    return success_response([GroupMemberResponse.model_validate(r) for r in rows])


@router.post("/{group_id}/members", response_model=dict[str, Any])
async def add_member(
    group_id: uuid.UUID,
    data: AddMemberRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a member — admin/owner only. Idempotent."""
    row, reused = await GroupService.add_member(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
        target_user_id=data.user_id,
        role=data.role,
    )
    return success_response(
        AddMemberResponse(
            group_id=uuid.UUID(str(row.group_id)),
            user_id=uuid.UUID(str(row.user_id)),
            role=row.role,
            reused=reused,
        )
    )


@router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a member — admin/owner only."""
    await GroupService.remove_member(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
        target_user_id=user_id,
    )


@router.get("/{group_id}/topics", response_model=dict[str, Any])
async def list_topics(
    group_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List topics in a topics-mode group."""
    rows = await GroupService.list_topics(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
    )
    return success_response([TopicResponse.model_validate(r) for r in rows])


@router.post(
    "/{group_id}/topics", response_model=dict[str, Any], status_code=status.HTTP_201_CREATED
)
async def create_topic(
    group_id: uuid.UUID,
    data: CreateTopicRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a topic — admin/owner only. Requires topics mode."""
    topic = await GroupService.create_topic(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        actor_id=current_user["user_id"],
        name=data.name,
        description=data.description,
        icon_emoji=data.icon_emoji,
    )
    return success_response(TopicResponse.model_validate(topic))


@router.delete("/{group_id}/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    group_id: uuid.UUID,
    topic_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a topic — admin/owner only."""
    await GroupService.delete_topic(
        db,
        institution_id=current_user["institution_id"],
        group_id=group_id,
        topic_id=topic_id,
        actor_id=current_user["user_id"],
    )
