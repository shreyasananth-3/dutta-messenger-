-- =============================================================================
-- Module: Media  (living copy — authoritative post-Stage-4e)
-- Tables: media_files
-- Note: message_media join table is defined in chat/SCHEMA.sql
-- =============================================================================
--
-- This copy is the authoritative post-Stage-4e definition of the `media_files`
-- table, including the recycle-bin columns that `docs/design/privacy-erasure.md`
-- requires but that the original `reference-docs/modules/media/SCHEMA.sql`
-- predates. The Alembic migration is
-- `migrations/versions/0005_media_module_schema.py`.
-- =============================================================================

CREATE TABLE media_files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    uploader_id     UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    file_name       VARCHAR(255) NOT NULL,
    file_size       BIGINT NOT NULL,
    mime_type       VARCHAR(100) NOT NULL,
    storage_key     TEXT NOT NULL,       -- S3 key: {inst_id}/originals/2026/04/uuid.jpg
    thumbnail_key   TEXT,                -- S3 key for thumbnail (NULL until generated)
    metadata        JSONB NOT NULL DEFAULT '{}',
    -- metadata examples:
    --   Image: {"width": 1920, "height": 1080}
    --   Video: {"width": 1920, "height": 1080, "duration_seconds": 120}
    --   Audio: {"duration_seconds": 245}
    --   Document: {"page_count": 12}
    upload_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (upload_status IN ('pending', 'completed', 'failed')),
    -- Privacy-erasure extensions (privacy-erasure.md §Media recycle bin):
    recycle_bin_at  TIMESTAMPTZ,         -- set when uploader deletes OR user is erased
    deleted_at      TIMESTAMPTZ,         -- set by nightly Celery sweep after 30-day grace
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: listing uploads by a specific user.
CREATE INDEX idx_media_files_uploader
    ON media_files (uploader_id, created_at DESC);

-- Supports: cleaning up stale pending uploads (Celery job).
CREATE INDEX idx_media_files_pending
    ON media_files (created_at)
    WHERE upload_status = 'pending';

-- Supports: nightly recycle-bin sweep (privacy-erasure.md).
CREATE INDEX idx_media_files_recycle_bin
    ON media_files (recycle_bin_at)
    WHERE recycle_bin_at IS NOT NULL AND deleted_at IS NULL;

-- Per-table trigger to bump updated_at on every UPDATE.
CREATE TRIGGER update_media_files_updated_at
    BEFORE UPDATE ON media_files
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
