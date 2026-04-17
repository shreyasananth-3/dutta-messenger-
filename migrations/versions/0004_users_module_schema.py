"""users module: pg_trgm search index + user_settings table

Revision ID: 0004_users_module_schema
Revises: 0003_refresh_tokens_updated_at
Create Date: 2026-04-18

Stage 4a (users module) schema additions:

1. Enable `pg_trgm` extension so `GET /api/v1/users/search?q=...` can use
   trigram similarity matching (ILIKE scans don't scale past a few thousand
   rows).
2. Add a GIN trigram index on `users.full_name` to make that search fast.
   Reference-docs MODULE.md refers to it as `display_name` — that was a
   drift; the real column is `full_name` per the baseline migration.
3. Create `user_settings` (one row per user) with notification / theme /
   language columns matching reference-docs/modules/users/MODULE.md §
   "User Settings Schema". FK to `users(id)` with ON DELETE CASCADE so
   tombstoning a user purges their settings.

Both `upgrade()` and `downgrade()` round-trip without errors; verify with
`make migrate && make migrate-down && make migrate`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_users_module_schema"
down_revision: str | None = "0003_refresh_tokens_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. pg_trgm extension — idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 2. Trigram index on full_name for fast ILIKE / similarity search
    #    within an institution. Name matches `idx_*` convention used in
    #    migrations/001_init_schema.sql.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_full_name_trgm "
        "ON users USING gin (full_name gin_trgm_ops)"
    )

    # 3. user_settings table — one row per user, seeded lazily by the
    #    users service on first read.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                 UUID NOT NULL UNIQUE
                                    REFERENCES users(id) ON DELETE CASCADE,
            notification_messages   BOOLEAN NOT NULL DEFAULT TRUE,
            notification_groups     BOOLEAN NOT NULL DEFAULT TRUE,
            notification_sound      BOOLEAN NOT NULL DEFAULT TRUE,
            theme                   VARCHAR(10) NOT NULL DEFAULT 'system'
                                    CHECK (theme IN ('light', 'dark', 'system')),
            language                VARCHAR(5) NOT NULL DEFAULT 'en',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_settings_user_id "
        "ON user_settings (user_id)"
    )


def downgrade() -> None:
    # Reverse order: drop table, drop index, leave extension (extensions are
    # expensive to re-install and other modules may start using pg_trgm;
    # dropping it here would be a breaking change for siblings).
    op.execute("DROP TABLE IF EXISTS user_settings")
    op.execute("DROP INDEX IF EXISTS idx_users_full_name_trgm")
    # Intentional: CREATE EXTENSION pg_trgm is NOT dropped on downgrade.
