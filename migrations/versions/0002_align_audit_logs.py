"""align audit_logs with security/audit.py design

Revision ID: 0002_align_audit_logs
Revises: 0001_baseline
Create Date: 2026-04-18

The baseline `audit_logs` table was scaffolded with web-server-style columns
(`user_id`, `changes`, `ip_address`, `user_agent`) that don't match the
canonical `write_audit()` contract documented in `docs/LOCAL_TESTING.md` and
implemented in `src/shared/security/audit.py`. The contract calls for:

  - actor_id        (the user who did it; NULL allowed for system actions)
  - institution_id  (tenant scope; required for tenant-aware queries)
  - resource_id     (UUID, NULL allowed for actions without a single target)
  - metadata        (JSONB free-form context)

Since no module is wired to call `write_audit()` yet (Stage 1 only shipped
the helper), we drop and recreate the table rather than ALTER it across
several columns. Downgrade restores the baseline shape so callers of older
revisions still resolve.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_align_audit_logs"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace audit_logs with the design-spec shape."""
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
    op.execute(
        """
        CREATE TABLE audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            institution_id UUID REFERENCES institutions(id) ON DELETE CASCADE,
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id UUID,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_actor "
        "ON audit_logs(actor_id) "
        "WHERE actor_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_institution "
        "ON audit_logs(institution_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_resource "
        "ON audit_logs(resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_action_time "
        "ON audit_logs(action, created_at DESC)"
    )


def downgrade() -> None:
    """Restore the baseline `audit_logs` shape."""
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
    op.execute(
        """
        CREATE TABLE audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id UUID NOT NULL,
            changes JSONB,
            ip_address VARCHAR(50),
            user_agent TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_audit_logs_user ON audit_logs(user_id)")
    op.execute(
        "CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_created ON audit_logs(created_at DESC)"
    )
