"""SQLAlchemy ORM models for the users module.

The `User` row itself is owned by the `auth` module — see
`src/modules/auth/models/db_models.py`. This module adds one new table,
`user_settings`, and re-exports `User` so call sites inside `users/` do
not have to reach into `auth.models`.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from src.modules.auth.models.db_models import User as _AuthUser
from src.shared.database import BaseModel

User = _AuthUser
"""Re-export of `src.modules.auth.models.db_models.User` for users-module
call sites. Stage 4a does not add columns to `users`; only `user_settings`."""


class UserSettings(BaseModel):
    """Per-user preferences: notifications, theme, language.

    One row per user, seeded lazily by the service on first read so we never
    have to back-fill existing users when shipping this module.
    """

    __tablename__ = "user_settings"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    notification_messages = Column(Boolean, nullable=False, default=True)
    notification_groups = Column(Boolean, nullable=False, default=True)
    notification_sound = Column(Boolean, nullable=False, default=True)
    theme = Column(String(10), nullable=False, default="system")
    language = Column(String(5), nullable=False, default="en")

    __table_args__ = (
        CheckConstraint(
            "theme IN ('light', 'dark', 'system')",
            name="user_settings_theme_check",
        ),
    )
