# WebSocket Protocol — Chat Module

> **This document is the TARGET spec.** The minimal implementation that's actually live today (auth-in-first-frame, no heartbeat, flat frame shape) is documented in [`docs/ui-contract/websocket-integration.md`](../../../../docs/ui-contract/websocket-integration.md). Flutter integrations should follow that doc, not this one, until the items listed under MODULE.md § Deferred ship.

---

## Connection

### Endpoint
```
wss://{host}/api/v1/ws/chat
```

The path is `/api/v1/ws/chat`. Prior revisions of this doc listed `/ws/chat` — that was wrong and will give you a 404.

### Handshake (target spec)
1. Client connects (no query params).
2. Client sends `{"type":"auth","token":"<jwt>"}` as the first frame.
3. Server verifies JWT. If invalid → close with code `4001` and reason `"Invalid token"`.
4. Server registers the connection in the connection manager (in-memory + Redis).
5. Server sends `connection.established` event.
6. Client begins sending/receiving events.

### Heartbeat
- Server sends `ping` frame every 30 seconds.
- Client must respond with `pong` within 10 seconds.
- If no pong received, server closes connection (client presumed dead).
- Client should implement reconnection with exponential backoff (1s, 2s, 4s, 8s, max 30s).

### Disconnection Codes

| Code | Reason | Action |
|------|--------|--------|
| 1000 | Normal close | Clean shutdown |
| 4001 | Invalid token | Re-authenticate and reconnect |
| 4002 | Token expired | Refresh token, then reconnect |
| 4003 | Account suspended | Show error, do not reconnect |
| 4008 | Rate limited | Wait, then reconnect |
| 4009 | Duplicate connection | Another device connected (optional: allow multi-device) |

---

## Message Frame Format

All WebSocket frames are JSON with this structure:

```json
{
  "type": "event.type",
  "payload": { ... },
  "request_id": "uuid"
}
```

Server responses include:
```json
{
  "type": "event.type",
  "payload": { ... },
  "request_id": "uuid",
  "timestamp": "ISO-8601"
}
```

---

## Events: Client → Server

### `message.send`

Send a new message to a conversation (works for DMs, simple groups, and topics).

```json
{
  "type": "message.send",
  "payload": {
    "conversation_id": "uuid",
    "content": "Hello, world!",
    "reply_to_message_id": "uuid | null",
    "client_message_id": "uuid",
    "media_file_ids": ["uuid", "uuid"]
  },
  "request_id": "uuid"
}
```

**Server processing:**
1. Validate user is a member of `conversation_id`.
2. **If topic conversation**: check topic's `access_mode`. If `read_only`, only admins/owners can send.
3. If `reply_to_message_id` is set, verify it exists in this conversation.
4. If `media_file_ids` is set, verify all files exist and belong to this user.
5. Check `client_message_id` for deduplication.
6. Persist message to PostgreSQL.
7. Publish to Redis Pub/Sub for delivery to other instances.
8. Send `message.new` to all connected members of the conversation.
9. Queue push notification for offline members (via Celery).

**Server response (to sender only):**
```json
{
  "type": "message.sent",
  "payload": {
    "client_message_id": "uuid",
    "server_message_id": "uuid",
    "created_at": "2025-01-15T10:30:00Z"
  },
  "request_id": "uuid"
}
```

**Error (to sender only):**
```json
{
  "type": "error",
  "payload": {
    "code": "NOT_CONVERSATION_MEMBER",
    "message": "You are not a member of this conversation.",
    "related_request_id": "uuid"
  }
}
```

**Read-only topic error:**
```json
{
  "type": "error",
  "payload": {
    "code": "TOPIC_READ_ONLY",
    "message": "This topic is read-only. Only admins can post.",
    "related_request_id": "uuid"
  }
}
```

---

### `message.edit`

Edit a previously sent message (only the sender can edit).

```json
{
  "type": "message.edit",
  "payload": {
    "message_id": "uuid",
    "content": "Updated content"
  },
  "request_id": "uuid"
}
```

**Rules:**
- Only the sender can edit their own message.
- Editing appends old content to `metadata.edit_history[]`.
- `updated_at` is refreshed.
- Server broadcasts `message.edited` to all conversation members.

---

### `message.delete`

Soft-delete a message (sender or conversation admin can delete).

```json
{
  "type": "message.delete",
  "payload": {
    "message_id": "uuid"
  },
  "request_id": "uuid"
}
```

**Rules:**
- Sender can delete their own message.
- Conversation admin/owner can delete any message.
- Content is set to NULL, `deleted_at` is set.
- If message was pinned, pinned_at is also cleared.
- Server broadcasts `message.deleted` to all conversation members.

---

### `message.pin`

Pin a message in a conversation (admin/owner only).

```json
{
  "type": "message.pin",
  "payload": {
    "message_id": "uuid"
  },
  "request_id": "uuid"
}
```

**Rules:**
- Only conversation admin/owner can pin.
- Sets `pinned_at` and `pinned_by` on the message.
- Multiple messages can be pinned simultaneously.
- Server broadcasts `message.pinned` to all conversation members.

---

### `message.unpin`

Unpin a message (admin/owner only).

```json
{
  "type": "message.unpin",
  "payload": {
    "message_id": "uuid"
  },
  "request_id": "uuid"
}
```

---

### `typing.start`

Indicate the user started typing in a conversation.

```json
{
  "type": "typing.start",
  "payload": {
    "conversation_id": "uuid"
  }
}
```

**Rules:**
- No `request_id` needed (fire-and-forget).
- Server broadcasts `typing.indicator` to other members.
- Typing indicator auto-expires after 5 seconds if no `typing.start` is re-sent.
- Rate limited: max 1 per second per user per conversation.

---

### `typing.stop`

Indicate the user stopped typing.

```json
{
  "type": "typing.stop",
  "payload": {
    "conversation_id": "uuid"
  }
}
```

---

### `read.update`

Mark messages as read up to a specific message in a conversation.

```json
{
  "type": "read.update",
  "payload": {
    "conversation_id": "uuid",
    "last_read_message_id": "uuid"
  }
}
```

**Rules:**
- Upserts `message_reads` table (one row per user per conversation).
- Server broadcasts `read.receipt` to other members of the conversation.
- Debounce on client side: don't send for every message scroll, batch to every 2 seconds.
- For topic-enabled groups, each topic conversation has its own read tracking.

---

## Events: Server → Client

### `connection.established`

Sent immediately after successful WebSocket handshake.

```json
{
  "type": "connection.established",
  "payload": {
    "user_id": "uuid",
    "server_time": "2025-01-15T10:30:00Z"
  }
}
```

### `message.new`

Broadcast to all members of a conversation when a new message arrives.

```json
{
  "type": "message.new",
  "payload": {
    "id": "uuid",
    "conversation_id": "uuid",
    "sender": {
      "id": "uuid",
      "display_name": "Rajesh",
      "avatar_url": "https://..."
    },
    "content": "Hello, world!",
    "reply_to": {
      "id": "uuid",
      "sender_name": "Priya",
      "content_preview": "Hey, did you see the..."
    },
    "media": [
      {
        "id": "uuid",
        "file_name": "photo.jpg",
        "mime_type": "image/jpeg",
        "file_size": 245000,
        "thumbnail_url": "https://...",
        "download_url": "https://..."
      }
    ],
    "message_type": "text",
    "created_at": "2025-01-15T10:30:00Z"
  }
}
```

### `message.edited`

Broadcast when a message is edited.

```json
{
  "type": "message.edited",
  "payload": {
    "id": "uuid",
    "conversation_id": "uuid",
    "content": "Updated content",
    "updated_at": "2025-01-15T10:31:00Z"
  }
}
```

### `message.deleted`

Broadcast when a message is soft-deleted.

```json
{
  "type": "message.deleted",
  "payload": {
    "id": "uuid",
    "conversation_id": "uuid",
    "deleted_at": "2025-01-15T10:32:00Z"
  }
}
```

### `message.pinned`

Broadcast when a message is pinned.

```json
{
  "type": "message.pinned",
  "payload": {
    "id": "uuid",
    "conversation_id": "uuid",
    "pinned_by": {
      "id": "uuid",
      "display_name": "Sarbani"
    },
    "pinned_at": "2025-01-15T10:33:00Z",
    "content_preview": "Don't miss the conference..."
  }
}
```

### `message.unpinned`

Broadcast when a message is unpinned.

```json
{
  "type": "message.unpinned",
  "payload": {
    "id": "uuid",
    "conversation_id": "uuid"
  }
}
```

### `typing.indicator`

Broadcast to conversation members (except the typer) when someone is typing.

```json
{
  "type": "typing.indicator",
  "payload": {
    "conversation_id": "uuid",
    "user_id": "uuid",
    "user_name": "Rajesh",
    "is_typing": true
  }
}
```

### `read.receipt`

Broadcast when someone reads messages in a conversation.

```json
{
  "type": "read.receipt",
  "payload": {
    "conversation_id": "uuid",
    "user_id": "uuid",
    "last_read_message_id": "uuid",
    "read_at": "2025-01-15T10:33:00Z"
  }
}
```

### `presence.update`

Broadcast when a user comes online or goes offline.

```json
{
  "type": "presence.update",
  "payload": {
    "user_id": "uuid",
    "status": "online | offline",
    "last_seen_at": "2025-01-15T10:33:00Z"
  }
}
```

---

## Error Handling

All errors sent over WebSocket follow this format:

```json
{
  "type": "error",
  "payload": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "related_request_id": "uuid | null"
  }
}
```

| Code | When |
|------|------|
| `INVALID_PAYLOAD` | Malformed JSON or missing required fields |
| `NOT_CONVERSATION_MEMBER` | User tried to act on a conversation they're not in |
| `MESSAGE_NOT_FOUND` | Referenced message doesn't exist |
| `PERMISSION_DENIED` | User lacks permission for this action |
| `TOPIC_READ_ONLY` | Tried to send message in a read-only topic without admin role |
| `RATE_LIMITED` | Too many events in too short a time |

---

## Flutter Implementation Notes

1. **Reconnection**: Use exponential backoff. On reconnect, fetch missed messages via REST API (`GET /messages?after_cursor=...`).
2. **Optimistic UI**: Show sent messages immediately in UI (using `client_message_id`). Update with server data when `message.sent` arrives.
3. **Deduplication**: Use `client_message_id` to prevent showing duplicate messages if the server ack arrives while the optimistic message is displayed.
4. **Typing debounce**: Send `typing.start` at most once per second. Auto-send `typing.stop` after 3 seconds of no keystroke.
5. **Read receipt batching**: Don't send `read.update` for every message scroll. Batch: send when user pauses scrolling for 2+ seconds.
6. **Read-only indicator**: If a topic has `access_mode: read_only`, hide the message input (or show it disabled with "Only admins can post" text). Check this from the topic metadata, not by waiting for a server error.
7. **Pin notification**: When `message.pinned` arrives, show a subtle banner at the top of the chat: "Sarbani pinned a message". Tapping it scrolls to the pinned message.
