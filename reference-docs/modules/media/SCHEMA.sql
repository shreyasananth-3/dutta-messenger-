-- =============================================================================
-- Module: Media
-- Tables: media_files
-- Note: message_media join table is defined in chat/SCHEMA.sql
-- =============================================================================

CREATE TABLE media_files (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    uploader_id     UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    file_name       VARCHAR(255) NOT NULL,
    file_size       BIGINT NOT NULL,
    mime_type       VARCHAR(100) NOT NULL,
    storage_key     TEXT NOT NULL,       -- S3 object key: {inst_id}/originals/2025/01/uuid.jpg
    thumbnail_key   TEXT,                -- S3 object key for thumbnail (NULL if not applicable)
    metadata        JSONB NOT NULL DEFAULT '{}',
    -- metadata examples:
    --   Image: {"width": 1920, "height": 1080}
    --   Video: {"width": 1920, "height": 1080, "duration_seconds": 120}
    --   Audio: {"duration_seconds": 245}
    --   Document: {"page_count": 12}
    upload_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (upload_status IN ('pending', 'completed', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: Listing uploads by a specific user
CREATE INDEX idx_media_files_uploader ON media_files (uploader_id, created_at DESC);

-- Supports: Cleaning up stale pending uploads (Celery job)
CREATE INDEX idx_media_files_pending
    ON media_files (created_at)
    WHERE upload_status = 'pending';
