"""Models for the users module."""

from src.modules.users.models.db_models import User, UserSettings
from src.modules.users.models.request_models import (
    OnlineStatusRequest,
    SearchUsersRequest,
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

__all__ = [
    "OnlineStatusRequest",
    "OnlineStatusResponse",
    "SearchUsersRequest",
    "UpdateProfileRequest",
    "UpdateUserSettingsRequest",
    "User",
    "UserProfileResponse",
    "UserSearchResponse",
    "UserSearchResultItem",
    "UserSettings",
    "UserSettingsResponse",
]
