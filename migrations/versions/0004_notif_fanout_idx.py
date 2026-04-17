"""add fan-out partial indexes for the notifications module

Revision ID: 0004_notif_fanout_idx
Revises: 0003_refresh_tokens_updated_at
Create Date: 2026-04-18

The baseline migration (0001_baseline_schema) already ships the
`fcm_tokens`, `notifications`, and `notification_batches` tables. What the
notifications module's fan-out path needs that the baseline does not
provide is two partial indexes:

- `idx_fcm_tokens_user_active` on `fcm_tokens(user_id) WHERE is_active`
  — `FanoutService.list_active_tokens(user_id)` is the hot path called once
  per recipient on every message send. Filtering by `user_id` then the
  `is_active` predicate on the existing `idx_fcm_tokens_active` scans the
  entire active slice. A per-user partial index is the right shape.
- `idx_notification_batches_user_pending` on
  `notification_batches(user_id) WHERE status = 'pending'` — lets the
  Celery worker pick up pending batches for retries without scanning the
  whole table.

Both statements use `IF NOT EXISTS` / `IF EXISTS` so re-running the
migration against a database that already has the indexes (e.g. production
after a `pg_restore`) is a no-op, and the downgrade is safe even if a
partial index was pruned manually.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_notif_fanout_idx"
down_revision: str | None = "0003_refresh_tokens_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fcm_tokens_user_active "
        "ON fcm_tokens(user_id) WHERE is_active = true"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_batches_user_pending "
        "ON notification_batches(user_id) WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notification_batches_user_pending")
    op.execute("DROP INDEX IF EXISTS idx_fcm_tokens_user_active")
