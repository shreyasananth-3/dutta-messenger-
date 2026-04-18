"""media module: realign media_files to reference-docs + add recycle-bin cols

Revision ID: 0005_media_module_schema
Revises: 0004_users_module_schema
Create Date: 2026-04-18

The baseline `media_files` table (migrations/001_init_schema.sql lines 261-276)
was scaffolded with a message-attachment shape (`message_id`, `user_id`,
`s3_url`, `is_virus_scanned`, `virus_scan_result`) that predates the
reference-docs module design. The canonical shape per
`reference-docs/modules/media/SCHEMA.sql` and `docs/design/privacy-erasure.md`
§Media recycle bin is:

  - institution_id   — tenant scope; media is a tenant-scoped resource.
  - uploader_id      — was `user_id`.
  - storage_key      — was `s3_key`; TEXT, not VARCHAR(1024) (S3 keys
                       legitimately exceed 1024 if institution names grow).
  - thumbnail_key    — was `thumbnail_url`; we store the key, clients get a
                       presigned URL at download time.
  - metadata         — JSONB for width/height/duration/page_count.
  - upload_status    — pending | completed | failed (replaces the implicit
                       `virus_scanned` flag with a first-class lifecycle).
  - recycle_bin_at   — privacy-erasure.md: set on uploader DELETE or on user
                       erasure; 30-day grace before permanent purge.
  - deleted_at       — privacy-erasure.md: nightly Celery sweep tombstone.

Per the precedent of `0002_align_audit_logs`, we drop and recreate rather
than ALTER across this many columns. `ENABLE_MEDIA` is OFF in production
and no service method is wired to the old shape, so there are no rows to
preserve.

The per-table trigger `update_media_files_updated_at` created in the baseline
(001_init_schema.sql lines 399-400) is dropped by CASCADE on DROP TABLE and
re-added here. The shared trigger function `update_updated_at_column()` is
NOT touched — it stays global.

Both `upgrade()` and `downgrade()` round-trip cleanly; verify with
`make migrate && make migrate-down && make migrate`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_media_module_schema"
down_revision: str | None = "0004_users_module_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the baseline shape. CASCADE also drops the trigger
    # `update_media_files_updated_at` and any FK dependents (there are none
    # today — `message_media` in the chat module is a separate join table).
    op.execute("DROP TABLE IF EXISTS media_files CASCADE")

    op.execute(
        """
        CREATE TABLE media_files (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id  UUID NOT NULL
                            REFERENCES institutions(id) ON DELETE CASCADE,
            uploader_id     UUID NOT NULL
                            REFERENCES users(id) ON DELETE SET NULL,
            file_name       VARCHAR(255) NOT NULL,
            file_size       BIGINT NOT NULL,
            mime_type       VARCHAR(100) NOT NULL,
            storage_key     TEXT NOT NULL,
            thumbnail_key   TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            upload_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (upload_status IN
                                   ('pending', 'completed', 'failed')),
            recycle_bin_at  TIMESTAMPTZ,
            deleted_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # Uploads by a specific user — used by profile/history views.
    op.execute(
        "CREATE INDEX idx_media_files_uploader "
        "ON media_files (uploader_id, created_at DESC)"
    )

    # Stale-pending sweep — only covers rows still in the 'pending' state,
    # so the partial index stays small.
    op.execute(
        "CREATE INDEX idx_media_files_pending "
        "ON media_files (created_at) "
        "WHERE upload_status = 'pending'"
    )

    # Recycle-bin sweep — nightly Celery task ordered by recycle_bin_at.
    # Partial index excludes already-tombstoned rows.
    op.execute(
        "CREATE INDEX idx_media_files_recycle_bin "
        "ON media_files (recycle_bin_at) "
        "WHERE recycle_bin_at IS NOT NULL AND deleted_at IS NULL"
    )

    # Re-attach the per-table updated_at trigger. The function
    # `update_updated_at_column()` is created once in the baseline migration.
    op.execute(
        "CREATE TRIGGER update_media_files_updated_at "
        "BEFORE UPDATE ON media_files "
        "FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()"
    )


def downgrade() -> None:
    # Restore the baseline shape so callers of older revisions still resolve.
    op.execute("DROP TABLE IF EXISTS media_files CASCADE")

    op.execute(
        """
        CREATE TABLE media_files (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_name VARCHAR(500) NOT NULL,
            file_size BIGINT NOT NULL,
            file_type VARCHAR(100) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            s3_key VARCHAR(1024) NOT NULL UNIQUE,
            s3_url TEXT NOT NULL,
            thumbnail_url TEXT,
            is_virus_scanned BOOLEAN DEFAULT false,
            virus_scan_result TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute("CREATE INDEX idx_media_files_user ON media_files(user_id)")
    op.execute("CREATE INDEX idx_media_files_message ON media_files(message_id)")
    op.execute(
        "CREATE INDEX idx_media_files_created ON media_files(created_at DESC)"
    )

    op.execute(
        "CREATE TRIGGER update_media_files_updated_at "
        "BEFORE UPDATE ON media_files "
        "FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()"
    )
