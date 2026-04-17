-- =============================================================================
-- Module: Chat
-- Tables: conversations, conversation_members, messages, message_reads, message_media
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------------------------
-- Table: conversations
-- Purpose: Container for messages. A conversation can be:
--   - dm:    Direct message between two users
--   - group: Single chat stream for a simple-mode group
--   - topic: Chat stream for one topic within a topic-enabled group
--
-- The chat module treats all three types identically for messaging.
-- The type field exists for Flutter UI routing and ACL checks.
-- -----------------------------------------------------------------------------
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type            VARCHAR(10) NOT NULL CHECK (type IN ('dm', 'group', 'topic')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: Listing recent conversations for a user (joined via conversation_members)
CREATE INDEX idx_conversations_updated
    ON conversations (updated_at DESC);


-- -----------------------------------------------------------------------------
-- Table: conversation_members
-- Purpose: Maps users to conversations with their role in that conversation.
-- Rules:
--   - DM conversations have exactly 2 members.
--   - Group/topic conversations can have 1 to MAX_GROUP_MEMBERS members.
--   - A user cannot be in the same conversation twice.
--   - For topic-enabled groups, adding a user to the group adds them to ALL
--     topic conversations. This is handled by group_service, not here.
-- -----------------------------------------------------------------------------
CREATE TABLE conversation_members (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL DEFAULT 'member'
                    CHECK (role IN ('owner', 'admin', 'member')),
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    muted_until     TIMESTAMPTZ,

    UNIQUE (conversation_id, user_id)
);

-- Supports: GET /api/v1/chat/conversations (list user's conversations)
-- Query: SELECT conversation_id FROM conversation_members WHERE user_id = ?
CREATE INDEX idx_conversation_members_user
    ON conversation_members (user_id);

-- Supports: GET /api/v1/chat/conversations/{id}/members (list members)
-- Query: SELECT * FROM conversation_members WHERE conversation_id = ?
CREATE INDEX idx_conversation_members_conversation
    ON conversation_members (conversation_id);

-- Supports: Finding existing DM between two users
CREATE INDEX idx_conversation_members_user_conversation
    ON conversation_members (user_id, conversation_id);


-- -----------------------------------------------------------------------------
-- Table: messages
-- Purpose: Stores all messages sent in conversations.
-- Rules:
--   - content can be NULL if message has media attachments only.
--   - reply_to_id references another message in the SAME conversation.
--   - client_message_id is unique for idempotency (prevents duplicate sends).
--   - deleted_at enables soft delete (content is nullified, row persists).
--   - message_type: 'text', 'media', 'system' (e.g., "User X joined the group")
--   - pinned_at/pinned_by: if pinned_at is set, message is pinned.
--     Multiple messages can be pinned in the same conversation.
-- -----------------------------------------------------------------------------
CREATE TABLE messages (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id           UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    content             TEXT,
    reply_to_id         UUID REFERENCES messages(id) ON DELETE SET NULL,
    client_message_id   UUID NOT NULL,
    message_type        VARCHAR(20) NOT NULL DEFAULT 'text'
                        CHECK (message_type IN ('text', 'media', 'system')),
    metadata            JSONB DEFAULT '{}',
    -- metadata.edit_history: [{content: "old text", edited_at: "ISO timestamp"}, ...]
    pinned_at           TIMESTAMPTZ,         -- NULL = not pinned
    pinned_by           UUID REFERENCES users(id) ON DELETE SET NULL,
    deleted_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: GET /api/v1/chat/conversations/{id}/messages?cursor=...
-- Query: SELECT * FROM messages
--        WHERE conversation_id = ? AND created_at < ?
--        ORDER BY created_at DESC LIMIT 50
CREATE INDEX idx_messages_conversation_created
    ON messages (conversation_id, created_at DESC);

-- Supports: Idempotency check on message creation
-- Query: SELECT * FROM messages WHERE client_message_id = ?
CREATE UNIQUE INDEX idx_messages_client_message_id
    ON messages (client_message_id);

-- Supports: Listing pinned messages for a conversation
-- Query: SELECT * FROM messages
--        WHERE conversation_id = ? AND pinned_at IS NOT NULL
--        ORDER BY pinned_at DESC
CREATE INDEX idx_messages_pinned
    ON messages (conversation_id, pinned_at DESC)
    WHERE pinned_at IS NOT NULL;

-- Supports: Counting unread messages per conversation
-- Query: SELECT COUNT(*) FROM messages
--        WHERE conversation_id = ? AND created_at > ?
--        AND sender_id != ? AND deleted_at IS NULL
CREATE INDEX idx_messages_conversation_sender_created
    ON messages (conversation_id, sender_id, created_at DESC)
    WHERE deleted_at IS NULL;


-- -----------------------------------------------------------------------------
-- Table: message_reads
-- Purpose: Tracks the latest message read by each user in each conversation.
-- Design: One row per (user, conversation) pair, NOT per message.
--         This is efficient because read receipts are cumulative — if you've
--         read message 50, you've read 1-49 too.
-- Note: For topic-enabled groups, each topic has its own conversation,
--       so unread counts are automatically per-topic.
-- -----------------------------------------------------------------------------
CREATE TABLE message_reads (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    read_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (conversation_id, user_id)
);

-- Supports: Getting read status for all members of a conversation
-- Query: SELECT * FROM message_reads WHERE conversation_id = ?
CREATE INDEX idx_message_reads_conversation
    ON message_reads (conversation_id);


-- -----------------------------------------------------------------------------
-- Table: message_media
-- Purpose: Join table linking messages to media files.
--          A message can have 0-10 media attachments.
--          Media files are managed by the media module.
-- -----------------------------------------------------------------------------
CREATE TABLE message_media (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    media_file_id   UUID NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
    sort_order      SMALLINT NOT NULL DEFAULT 0
);

-- Supports: Loading media for a message
-- Query: SELECT * FROM message_media WHERE message_id = ? ORDER BY sort_order
CREATE INDEX idx_message_media_message
    ON message_media (message_id, sort_order);
