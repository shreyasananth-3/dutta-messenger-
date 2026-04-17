"""HTTP routes for the users module.

Routes are thin adapters over `UserService`. Per CLAUDE.md each handler is
≤ 15 lines. Per `docs/design/api-versioning.md`, every error path raises
an `AppException` subclass — never a bare `HTTPException(detail=...)` —
so the Stage-3 middleware normalisation is a redundant safety net, not a
load-bearing dependency.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.users.models.request_models import (
    UpdateProfileRequest,
    UpdateUserSettingsRequest,
)
from src.modules.users.models.response_models import (
    OnlineStatusResponse,
    UserProfileResponse,
    UserSearchResponse,
    UserSearchResultItem,
    UserSettingsResponse,
)
from src.modules.users.services import presence_service
from src.modules.users.services.user_service import UserService
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

router = APIRouter(tags=["users"])


async def _build_profile(
    user: Any,
    *,
    include_email: bool,
    is_online: bool | None = None,
) -> UserProfileResponse:
    """Shape a User row into a UserProfileResponse, injecting online status."""
    online = is_online if is_online is not None else await presence_service.is_online(user.id)
    payload = UserProfileResponse.model_validate(user)
    if not include_email:
        payload.email = None
    payload.is_online = online
    return payload


# ---------------------------------------------------------------------------
# GET /users/me — own profile
# ---------------------------------------------------------------------------


@router.get("/users/me")
async def get_me(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return the caller's own profile."""
    user = await UserService.get_by_id(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
    )
    return success_response(await _build_profile(user, include_email=True))


# ---------------------------------------------------------------------------
# PATCH /users/me — update own profile
# ---------------------------------------------------------------------------


@router.patch("/users/me")
async def update_me(
    data: UpdateProfileRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update fields the caller is allowed to edit on their own profile."""
    user = await UserService.update_profile(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
        **data.model_dump(exclude_unset=True),
    )
    await db.commit()
    return success_response(await _build_profile(user, include_email=True))


# ---------------------------------------------------------------------------
# GET /users/search — within institution
# ---------------------------------------------------------------------------


@router.get("/users/search")
async def search_users(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Search users inside the caller's institution by name or email."""
    users = await UserService.search(
        db, institution_id=current_user["institution_id"], query=q, limit=limit
    )
    online_map = await UserService.annotate_online(users)
    items = [
        UserSearchResultItem(
            id=u.id,
            full_name=u.full_name,
            avatar_url=u.avatar_url,
            bio=u.bio,
            status=u.status,
            is_online=online_map.get(u.id, False),
        )
        for u in users
    ]
    return success_response(UserSearchResponse(results=items, has_more=False))


# ---------------------------------------------------------------------------
# GET /users/online — bulk online lookup
# ---------------------------------------------------------------------------


@router.get("/users/online")
async def online_status(
    _current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    user_ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=200)],
) -> dict[str, Any]:
    """Return online/offline for every user_id passed (max 200)."""
    online_map = await presence_service.get_online_map(user_ids)
    return success_response(OnlineStatusResponse(online=online_map))


# ---------------------------------------------------------------------------
# GET /users/me/settings
# ---------------------------------------------------------------------------


@router.get("/users/me/settings")
async def get_my_settings(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return the caller's settings, seeding defaults on first read."""
    settings = await UserService.get_or_create_settings(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
    )
    await db.commit()
    return success_response(UserSettingsResponse.model_validate(settings))


# ---------------------------------------------------------------------------
# PATCH /users/me/settings
# ---------------------------------------------------------------------------


@router.patch("/users/me/settings")
async def update_my_settings(
    data: UpdateUserSettingsRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Patch the caller's own settings."""
    settings = await UserService.update_settings(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
        **data.model_dump(exclude_unset=True),
    )
    await db.commit()
    return success_response(UserSettingsResponse.model_validate(settings))


# ---------------------------------------------------------------------------
# GET /users/{id} — another user's public profile
# Registered last so the specific paths above win the router match.
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return another user's public profile (email stripped)."""
    user = await UserService.get_by_id(
        db, user_id=user_id, institution_id=current_user["institution_id"]
    )
    return success_response(await _build_profile(user, include_email=False))


# ---------------------------------------------------------------------------
# PATCH /users/{id}/status — deliberately NOT exposed in Stage 4a.
# MODULE.md lists this endpoint under the `institution.manage_users`
# permission, which only exists once the ACL module (Stage 4b) ships.
# It will be added there alongside the real permission check. Shipping
# it in Stage 4a would have required a flaky "first-registered-user-is-
# admin" heuristic — see src/modules/users/services/user_service.py
# header comment for the reasoning.
# ---------------------------------------------------------------------------
