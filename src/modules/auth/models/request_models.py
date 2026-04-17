"""Pydantic request models for auth module."""

from pydantic import BaseModel, Field


class CreateInstitutionRequest(BaseModel):
    """Request to create a new institution."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    domain: str | None = Field(None, max_length=255)
    logo_url: str | None = Field(None)
    subscription_tier: str = Field("free")
    max_users: int = Field(100, ge=10, le=10000)
    max_groups: int = Field(500, ge=10, le=50000)


class RegisterRequest(BaseModel):
    """Request for user registration.

    Can be used for self-registration (if institution allows)
    or via invitation link.
    """

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    phone_number: str | None = Field(None, max_length=20)
    invitation_token: str | None = Field(None)


class LoginRequest(BaseModel):
    """Request for user login."""

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)
    institution_id: str | None = Field(None)


class RefreshTokenRequest(BaseModel):
    """Request to refresh access token."""

    refresh_token: str = Field(..., min_length=1)


class InviteUserRequest(BaseModel):
    """Request to invite a user to institution."""

    email: str = Field(..., min_length=1, max_length=255)


class AcceptInvitationRequest(BaseModel):
    """Request to accept an invitation."""

    token: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)


class ChangePasswordRequest(BaseModel):
    """Request to change user password."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=255)
