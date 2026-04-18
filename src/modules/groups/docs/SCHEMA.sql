-- =============================================================================
-- Module: Groups
-- Tables: groups, topics
-- Note: Group membership uses conversation_members from the chat module.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table: groups
-- Purpose: Group entity. Supports two modes:
--   - simple: group has one conversation (conversation_id is set)
--   - topics: group has multiple topic subchannels (conversation_id is NULL)
--
-- The mode field determines how the Flutter app renders the group:
--   - simple → open directly to chat screen
--   - topics → open to topic list screen (like Telegram Topics)
-- -----------------------------------------------------------------------------
CREATE TABLE groups (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id  UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    -- conversation_id is set ONLY for simple mode groups.
    -- For topic-enabled groups, this is NULL (topics hold the conversations).
    name            VARCHAR(100) NOT NULL,
    description     VARCHAR(500),
    avatar_url      TEXT,
    mode            VARCHAR(10) NOT NULL DEFAULT 'simple'
                    CHECK (mode IN ('simple', 'topics')),
    created_by      UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    settings        JSONB NOT NULL DEFAULT '{
        "allow_member_invite": false,
        "max_members": 500
    }',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraint: simple mode MUST have conversation_id, topics mode MUST NOT
    CONSTRAINT chk_group_mode_conversation CHECK (
        (mode = 'simple' AND conversation_id IS NOT NULL) OR
        (mode = 'topics' AND conversation_id IS NULL)
    )
);

-- Supports: Listing groups in an institution
CREATE INDEX idx_groups_institution ON groups (institution_id);

-- Supports: Enforcing unique group names within an institution
CREATE UNIQUE INDEX idx_groups_institution_name
    ON groups (institution_id, LOWER(name));


-- -----------------------------------------------------------------------------
-- Table: topics
-- Purpose: Subchannels within a topic-enabled group. Each topic has its own
--          conversation for messages.
--
-- Rules:
--   - Only exist for groups where mode = 'topics'
--   - Every topic-enabled group has exactly one default topic (is_default=true)
--     named "General" — this is auto-created and cannot be deleted
--   - access_mode controls who can post:
--       'read_write' → all group members can send messages
--       'read_only'  → only group admins/owner can send messages
--   - icon_emoji is displayed as the topic icon in the list (like Telegram)
--   - sort_order determines display order (lower = higher in list)
-- -----------------------------------------------------------------------------
CREATE TABLE topics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    group_id        UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    icon_emoji      VARCHAR(10) DEFAULT '#',
    description     VARCHAR(500),
    access_mode     VARCHAR(15) NOT NULL DEFAULT 'read_write'
                    CHECK (access_mode IN ('read_write', 'read_only')),
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order      SMALLINT NOT NULL DEFAULT 0,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Topic names must be unique within a group
    UNIQUE (group_id, LOWER(name))
);

-- Supports: Listing topics for a group (ordered by sort_order)
-- Query: SELECT * FROM topics WHERE group_id = ? ORDER BY sort_order, created_at
CREATE INDEX idx_topics_group_order
    ON topics (group_id, sort_order, created_at);

-- Supports: Looking up which group a conversation belongs to (for topic conversations)
-- Query: SELECT group_id FROM topics WHERE conversation_id = ?
-- (Already covered by UNIQUE constraint on conversation_id)

-- Supports: Ensuring each topic-enabled group has exactly one default topic
-- A partial unique index: only one row per group can have is_default = true
CREATE UNIQUE INDEX idx_topics_group_default
    ON topics (group_id) WHERE is_default = TRUE;


-- =============================================================================
-- NOTES ON MEMBERSHIP
-- =============================================================================
-- Group membership is managed through conversation_members (from chat module).
--
-- SIMPLE MODE:
--   Adding user to group = INSERT into conversation_members for group's conversation_id
--
-- TOPIC-ENABLED MODE:
--   Adding user to group = INSERT into conversation_members for EVERY topic's
--   conversation_id. This means when a new topic is created, all existing group
--   members must be added to the new topic's conversation.
--
-- The group_service.add_member() function handles this logic — it checks the
-- group mode and adds the user to the appropriate conversation(s).
--
-- There is NO separate group_members table. The source of truth for "who is in
-- this group" is:
--   - Simple mode: conversation_members WHERE conversation_id = group.conversation_id
--   - Topic mode:  conversation_members WHERE conversation_id = (any topic's conversation_id)
--                  (all topics have the same members, so querying any one works)
--                  In practice, we query the default topic's conversation.
-- =============================================================================


-- =============================================================================
-- NOTES ON PINNED MESSAGES
-- =============================================================================
-- Pinned messages are tracked directly on the messages table (in chat module):
--   messages.pinned_at    TIMESTAMPTZ  (NULL = not pinned)
--   messages.pinned_by    UUID         (FK → users, who pinned it)
--
-- This works for both simple groups and topics — pinning is per-conversation.
-- =============================================================================
