-- =============================================================================
-- Module: Auth
-- Tables: institutions, users, invitations, password_reset_tokens
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------------------------
-- Table: institutions
-- Purpose: Top-level organizational entity. All users belong to one institution.
-- -----------------------------------------------------------------------------
CREATE TABLE institutions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    settings    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- Table: users
-- Purpose: Core user record. Shared between auth (owns it) and users module.
-- Note: password_hash is NEVER returned in any API response or logged.
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    email           VARCHAR(320) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(100) NOT NULL,
    avatar_url      TEXT,
    bio             VARCHAR(500),
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'pending')),
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (institution_id, email)
);

-- Supports: Login lookup
CREATE INDEX idx_users_email ON users (email);

-- Supports: User search within institution
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_users_search_name
    ON users USING gin (display_name gin_trgm_ops);

-- -----------------------------------------------------------------------------
-- Table: invitations
-- Purpose: Admin-generated invitation tokens for new user registration.
-- Lifecycle: created → used (on registration) OR expired (after 7 days)
-- -----------------------------------------------------------------------------
CREATE TABLE invitations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    email           VARCHAR(320) NOT NULL,
    token           VARCHAR(255) NOT NULL UNIQUE,
    invited_by      UUID NOT NULL REFERENCES users(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'used', 'expired')),
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: Token lookup during registration
CREATE INDEX idx_invitations_token ON invitations (token) WHERE status = 'pending';

-- -----------------------------------------------------------------------------
-- Table: password_reset_tokens
-- Purpose: One-time tokens for password reset flow. Expire after 1 hour.
-- -----------------------------------------------------------------------------
CREATE TABLE password_reset_tokens (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       VARCHAR(255) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: Token lookup during password reset
CREATE INDEX idx_password_reset_token ON password_reset_tokens (token) WHERE used_at IS NULL;
