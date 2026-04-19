"""Pydantic response models for auth module."""

from datetime import datetime

from pydantic import BaseModel


class InstitutionResponse(BaseModel):
    """Institution response model."""

    id: str
    name: str
    description: str | None
    domain: str | None
    logo_url: str | None
    subscription_tier: str
    max_users: int
    max_groups: int
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class UserResponse(BaseModel):
    """User response model (public profile)."""

    id: str
    institution_id: str
    email: str
    full_name: str
    phone_number: str | None
    avatar_url: str | None
    bio: str | None
    status: str
    is_active: bool
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class LoginResponse(BaseModel):
    """Response after successful login."""

    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth 2.0 token_type per RFC 6750, not a password
    expires_in_seconds: int


class RefreshTokenResponse(BaseModel):
    """Response after token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth 2.0 token_type per RFC 6750, not a password
    expires_in_seconds: int


class InvitationResponse(BaseModel):
    """User invitation response model."""

    id: str
    institution_id: str
    email: str
    invited_by_user_id: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    token: str | None = None
    invite_url: str | None = None

    class Config:
        """Pydantic configuration."""

        from_attributes = True


class InviteUserResponse(BaseModel):
    """Response after inviting a user."""

    invitation: InvitationResponse
    message: str


class RegistrationResponse(BaseModel):
    """Response after successful registration."""

    user: UserResponse
    message: str


class AcceptInvitationResponse(BaseModel):
    """Response after accepting invitation."""

    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth 2.0 token_type per RFC 6750, not a password
    expires_in_seconds: int
