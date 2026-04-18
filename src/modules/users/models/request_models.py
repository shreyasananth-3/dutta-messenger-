"""Pydantic request models for the users module."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# The MODULE.md profile-fields table caps bio at 500 chars; the DB column is
# unbounded TEXT. Enforce the cap here so it's impossible to POST beyond the
# documented limit.
_MAX_FULL_NAME_LEN = 100
_MAX_BIO_LEN = 500
_MAX_AVATAR_URL_LEN = 500
_MAX_PHONE_LEN = 20


class UpdateProfileRequest(BaseModel):
    """PATCH /api/v1/users/me — update own profile.

    Every field optional; only the fields the user passes are updated.
    `email` is NOT here — email changes go through auth with re-verification.
    `status` (active/suspended) is NOT here — that is admin-only.
    """

    full_name: Annotated[str, Field(min_length=1, max_length=_MAX_FULL_NAME_LEN)] | None = None
    avatar_url: Annotated[str, Field(max_length=_MAX_AVATAR_URL_LEN)] | None = None
    bio: Annotated[str, Field(max_length=_MAX_BIO_LEN)] | None = None
    phone_number: Annotated[str, Field(max_length=_MAX_PHONE_LEN)] | None = None


class SearchUsersRequest(BaseModel):
    """Query params for GET /api/v1/users/search."""

    q: Annotated[str, Field(min_length=1, max_length=100)]
    limit: Annotated[int, Field(ge=1, le=100)] = 20


class OnlineStatusRequest(BaseModel):
    """POST-body for GET /api/v1/users/online.

    FastAPI uses POST-shaped bodies awkwardly on GET; the implementation
    accepts a repeated `user_ids` query parameter instead. This model is
    used for documentation and internal validation.
    """

    user_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=200)]


class UpdateUserSettingsRequest(BaseModel):
    """PATCH /api/v1/users/me/settings — update own preferences.

    Every field optional; only the fields the user passes are updated.
    """

    notification_messages: bool | None = None
    notification_groups: bool | None = None
    notification_sound: bool | None = None
    theme: Annotated[str, Field(pattern=r"^(light|dark|system)$")] | None = None
    language: Annotated[str, Field(min_length=2, max_length=5)] | None = None

    @field_validator("language")
    @classmethod
    def _language_lowercase(cls, v: str | None) -> str | None:
        return v.lower() if v is not None else v
