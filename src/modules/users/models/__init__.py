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
    "User",
    "UserSettings",
    "UpdateProfileRequest",
    "SearchUsersRequest",
    "OnlineStatusRequest",
    "UpdateUserSettingsRequest",
    "UserProfileResponse",
    "UserSearchResponse",
    "UserSearchResultItem",
    "OnlineStatusResponse",
    "UserSettingsResponse",
]
