# Module: Chat

> **This is the core module.** It handles messaging across all conversation types — DMs, simple groups, and topic subchannels. The chat module is agnostic about what a conversation belongs to.

---

## What This Module Does

- Send and receive text messages in conversations (DMs, simple groups, and topics)
- Reply to specific messages (quote/tag)
- Mark messages as read (read receipts, tracked per-conversation)
- Edit and soft-delete messages
- Pin and unpin messages
- Real-time delivery via WebSocket
- Offline delivery via push notifications (delegates to `notifications` module)
- Message history with cursor-based pagination
- Client-side deduplication via `client_message_id`

## What This Module Does NOT Do

- Create/manage groups or topics → see [groups/MODULE.md](../groups/MODULE.md)
- Upload/download files → see [media/MODULE.md](../media/MODULE.md)
- Define permission codenames → uses ACL middleware from [acl/MODULE.md](../acl/MODULE.md)
- User profiles → see [users/MODULE.md](../users/MODULE.md)

---

## Core Design Principle

**The chat module does not know about groups or topics.** It only knows about `conversations`. A conversation has an `id`, a `type` (dm, group, or topic), members, and messages. Whether a conversation belongs to a simple group or to a topic inside a topic-enabled group is the concern of the groups module.

This means:
- The same message sending logic works for DMs, simple groups, and topics.
- The same WebSocket events work for all conversation types.
- The same read receipt logic works everywhere.
- The same pagination works everywhere.

The only exception is the **read-only check for topic conversations** — before allowing a message to be sent, the chat module calls the ACL middleware which checks if the topic's `access_mode` is `read_only` and whether the sender has admin/owner privileges.

---

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | JWT verification for all endpoints and WebSocket handshake |
| `users` | Sender info (name, avatar) attached to messages |
| `acl` | Checks if user is allowed to send messages (especially read-only topic check) |
| `groups` | Group/topic conversations are created by the groups module |
| `media` | Messages can have media attachments |
| `notifications` | Offline users get push notifications |

---

## Conversation Types

| Type | Created By | Description |
|------|-----------|-------------|
| `dm` | Chat module | Direct message between two users. One per user pair. |
| `group` | Groups module | Single chat stream for a simple-mode group. |
| `topic` | Groups module | Chat stream for one topic within a topic-enabled group. |

The chat module treats all three identically. The `type` field exists for:
- Flutter UI routing (DM shows other user's name, group shows group name, topic shows topic name)
- ACL checks (topic conversations check read-only mode)

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `conversations` | Container for messages. Type is `dm`, `group`, or `topic`. |
| `conversation_members` | Who is in which conversation, with what role. |
| `messages` | The actual messages. Has `reply_to_id` for threading, `pinned_at` for pins. |
| `message_reads` | Tracks latest read message per user per conversation. |
| `message_media` | Join table linking messages to media files (from media module). |

---

## API Endpoints

> Full specification: [API.md](API.md)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/chat/conversations` | List user's conversations |
| `POST` | `/api/v1/chat/conversations` | Create a DM conversation |
| `GET` | `/api/v1/chat/conversations/{id}` | Get conversation details |
| `GET` | `/api/v1/chat/conversations/{id}/messages` | List messages (paginated) |
| `POST` | `/api/v1/chat/conversations/{id}/messages` | Send message (REST fallback) |
| `PATCH` | `/api/v1/chat/messages/{id}` | Edit a sent message |
| `DELETE` | `/api/v1/chat/messages/{id}` | Soft-delete a message |
| `POST` | `/api/v1/chat/conversations/{id}/read` | Mark conversation as read up to a message |
| `POST` | `/api/v1/chat/conversations/{id}/pin/{message_id}` | Pin a message |
| `DELETE` | `/api/v1/chat/conversations/{id}/pin/{message_id}` | Unpin a message |
| `GET` | `/api/v1/chat/conversations/{id}/pinned` | List pinned messages |

---

## WebSocket Events

> Full specification: [WEBSOCKET.md](WEBSOCKET.md)

### Client → Server

| Event Type | Purpose |
|-----------|---------|
| `message.send` | Send a new message |
| `message.edit` | Edit a sent message |
| `message.delete` | Soft-delete a message |
| `message.pin` | Pin a message |
| `message.unpin` | Unpin a message |
| `typing.start` | User started typing |
| `typing.stop` | User stopped typing |
| `read.update` | Mark messages as read |

### Server → Client

| Event Type | Purpose |
|-----------|---------|
| `message.new` | A new message arrived |
| `message.edited` | A message was edited |
| `message.deleted` | A message was soft-deleted |
| `message.pinned` | A message was pinned |
| `message.unpinned` | A message was unpinned |
| `typing.indicator` | Someone is typing |
| `read.receipt` | Someone read messages |
| `presence.update` | A user came online or went offline |

---

## Key Business Rules

1. **DM conversations are unique per pair**: If User A and User B already have a DM, creating another returns the existing one.

2. **Reply chains are one level deep**: A message can reply to another message, but you cannot reply to a reply's reply. The UI shows the original message being replied to.

3. **Edited messages show edit history**: When a message is edited, `updated_at` changes and `metadata.edit_history` appends the old content. The UI shows "(edited)" indicator.

4. **Deleted messages show tombstone**: Soft-deleted messages appear as "This message was deleted" in the UI. Content is nullified in the database but the row persists for conversation flow.

5. **Read receipts are per-conversation, not per-message**: The client sends "I've read up to message X in conversation Y". One row per user per conversation.

6. **Message ordering**: Messages are ordered by `created_at DESC`. In case of timestamp collision, secondary sort is by `id`.

7. **Idempotency**: The `client_message_id` (UUID generated by Flutter) ensures that retrying a failed send doesn't create duplicates.

8. **Media in messages**: A message can have 0-10 media attachments. Media is uploaded first (via `/api/v1/media/upload`), then the `media_file_ids` are included in the message send request.

9. **Pinned messages**: Any conversation member with admin/owner role can pin messages. Multiple messages can be pinned simultaneously. Pinned messages are retrieved via a dedicated endpoint, not mixed into the message pagination.

10. **Read-only topic enforcement**: When sending a message to a topic conversation, the chat module checks with the ACL/groups module whether the topic's `access_mode` is `read_only`. If so, only admins/owners can post.

---

## File Structure

```
backend/modules/chat/
├── MODULE.md          ← You are here
├── API.md             ← REST endpoint specs
├── WEBSOCKET.md       ← WebSocket event specs
├── SCHEMA.sql         ← Table definitions
├── __init__.py
├── router.py          ← FastAPI router setup
├── routes/
│   ├── __init__.py
│   ├── conversations.py    ← Conversation CRUD endpoints
│   ├── messages.py         ← Message CRUD endpoints
│   └── pins.py             ← Pin/unpin endpoints
├── services/
│   ├── __init__.py
│   ├── conversation_service.py
│   ├── message_service.py
│   ├── read_receipt_service.py
│   └── pin_service.py
├── models/
│   ├── __init__.py
│   ├── db_models.py         ← SQLAlchemy table models
│   ├── request_models.py    ← Pydantic request bodies
│   └── response_models.py   ← Pydantic response bodies
├── websocket/
│   ├── __init__.py
│   ├── handler.py           ← WebSocket event dispatcher
│   └── events.py            ← Event type definitions
└── tests/
    ├── __init__.py
    ├── test_conversation_service.py
    ├── test_message_service.py
    ├── test_pin_service.py
    ├── test_conversation_routes.py
    ├── test_message_routes.py
    ├── test_websocket_events.py
    └── factories.py
```
