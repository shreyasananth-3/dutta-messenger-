"""add updated_at to refresh_tokens

Revision ID: 0003_refresh_tokens_updated_at
Revises: 0002_align_audit_logs
Create Date: 2026-04-18

CLAUDE.md DB rule: every table has `created_at` AND `updated_at`. The
baseline schema omitted `updated_at` on `refresh_tokens` while the
SQLAlchemy `BaseModel` it inherits from declares both, producing
`UndefinedColumnError` on every refresh-token insert. Adding the column
restores the contract.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_refresh_tokens_updated_at"
down_revision: str | None = "0002_align_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE refresh_tokens "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS updated_at")
