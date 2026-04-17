"""SQLAlchemy ORM models for auth module."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from src.shared.database import BaseModel


class Institution(BaseModel):
    """Institution database model.

    Represents an institution (organization) that uses DuttaMessenger.
    """

    __tablename__ = "institutions"

    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    domain = Column(String(255), unique=True, index=True)
    logo_url = Column(String(500), nullable=True)
    subscription_tier = Column(String(50), default="free")
    max_users = Column(Integer, default=100)
    max_groups = Column(Integer, default=500)

    # Relationships
    users = relationship("User", back_populates="institution", cascade="all, delete-orphan")
    roles = relationship("Role", back_populates="institution", cascade="all, delete-orphan")
    groups = relationship("Group", back_populates="institution", cascade="all, delete-orphan")
    invitations = relationship(
        "UserInvitation",
        back_populates="institution",
        cascade="all, delete-orphan",
    )


class User(BaseModel):
    """User database model.

    Represents a user account within an institution.
    """

    __tablename__ = "users"

    institution_id = Column(String(36), ForeignKey("institutions.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    phone_number = Column(String(20), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    bio = Column(Text, nullable=True)
    status = Column(String(50), default="offline", index=True)
    is_active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    institution = relationship("Institution", back_populates="users")
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    invitations_sent = relationship(
        "UserInvitation",
        foreign_keys="UserInvitation.invited_by_user_id",
        back_populates="invited_by_user",
    )
    invitations_accepted = relationship(
        "UserInvitation",
        foreign_keys="UserInvitation.accepted_user_id",
        back_populates="accepted_user",
    )


class UserInvitation(BaseModel):
    """User invitation database model.

    Represents an invitation sent to a user to join an institution.
    """

    __tablename__ = "user_invitations"

    institution_id = Column(String(36), ForeignKey("institutions.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    invited_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    token = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)

    # Relationships
    institution = relationship("Institution", back_populates="invitations")
    invited_by_user = relationship(
        "User",
        foreign_keys=[invited_by_user_id],
        back_populates="invitations_sent",
    )
    accepted_user = relationship(
        "User",
        foreign_keys=[accepted_user_id],
        back_populates="invitations_accepted",
    )


class RefreshToken(BaseModel):
    """Refresh token database model.

    Stores refresh tokens for token revocation and management.
    """

    __tablename__ = "refresh_tokens"

    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="refresh_tokens")
