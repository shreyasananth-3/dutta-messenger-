# API Specification — Chat Module

> **Every endpoint in the chat module is documented here.** Flutter team: use this as your contract. Backend team: implement exactly this.

---

## `GET /api/v1/chat/conversations`

**List the authenticated user's conversations.**

Returns conversations sorted by most recent activity (latest message timestamp).

### Request

```
GET /api/v1/chat/conversations?limit=20&cursor={cursor}
Authorization: Bearer {token}
```

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `limit` | int | 20 | Max conversations to return (1-50) |
| `cursor` | string | null | Pagination cursor from previous response |

### Response — `200 OK`

```json
{
  "data": [
    {
      "id": "conv_uuid",
      "type": "dm",
      "other_user": {
        "id": "user_uuid",
        "display_name": "Priya Sharma",
        "avatar_url": "https://...",
        "is_online": true
      },
      "last_message": {
        "id": "msg_uuid",
        "sender_name": "Priya Sharma",
        "content_preview": "Hey, can you check the...",
        "created_at": "2025-01-15T10:30:00Z"
      },
      "unread_count": 3,
      "muted_until": null,
      "updated_at": "2025-01-15T10:30:00Z"
    },
    {
      "id": "conv_uuid",
      "type": "group",
      "group": {
        "id": "group_uuid",
        "name": "Engineering Team",
        "avatar_url": "https://...",
        "member_count": 12
      },
      "last_message": {
        "id": "msg_uuid",
        "sender_name": "Rajesh",
        "content_preview": "Deployment is done!",
        "created_at": "2025-01-15T09:15:00Z"
      },
      "unread_count": 0,
      "muted_until": "2025-01-16T00:00:00Z",
      "updated_at": "2025-01-15T09:15:00Z"
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJ1cGRhdGVkX2F0IjoiMjAyNS0wMS0xNVQwOToxNTowMFoiLCJpZCI6Ii4uLiJ9",
    "limit": 20
  }
}
```

**Notes:**
- For `type: "dm"`, the response includes `other_user` (the other person in the DM).
- For `type: "group"`, the response includes `group` info.
- `unread_count` is calculated from the user's last read position.
- `content_preview` is the first 100 characters of the last message.

---

## `POST /api/v1/chat/conversations`

**Create a new DM conversation.**

If a DM already exists between the two users, returns the existing conversation (idempotent).

### Request

```json
{
  "type": "dm",
  "participant_id": "user_uuid"
}
```

### Response — `201 Created` (or `200 OK` if already exists)

```json
{
  "data": {
    "id": "conv_uuid",
    "type": "dm",
    "other_user": {
      "id": "user_uuid",
      "display_name": "Priya Sharma",
      "avatar_url": "https://...",
      "is_online": false
    },
    "last_message": null,
    "unread_count": 0,
    "created_at": "2025-01-15T10:30:00Z"
  }
}
```

### Errors

| Status | Code | When |
|--------|------|------|
| 404 | `USER_NOT_FOUND` | `participant_id` doesn't exist |
| 403 | `CANNOT_DM_SELF` | Trying to create DM with yourself |
| 403 | `USER_SUSPENDED` | Target user is suspended |

---

## `GET /api/v1/chat/conversations/{conversation_id}/messages`

**List messages in a conversation (paginated, newest first).**

### Request

```
GET /api/v1/chat/conversations/{conversation_id}/messages?limit=50&cursor={cursor}
Authorization: Bearer {token}
```

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `limit` | int | 50 | Max messages to return (1-100) |
| `cursor` | string | null | Pagination cursor (for loading older messages) |

### Response — `200 OK`

```json
{
  "data": [
    {
      "id": "msg_uuid",
      "conversation_id": "conv_uuid",
      "sender": {
        "id": "user_uuid",
        "display_name": "Rajesh Kumar",
        "avatar_url": "https://..."
      },
      "content": "Hello! Check this document.",
      "message_type": "text",
      "reply_to": {
        "id": "msg_uuid_original",
        "sender_name": "Priya Sharma",
        "content_preview": "Can you send me the..."
      },
      "media": [
        {
          "id": "media_uuid",
          "file_name": "report.pdf",
          "mime_type": "application/pdf",
          "file_size": 1245000,
          "thumbnail_url": null,
          "download_url": "/api/v1/media/media_uuid/download"
        }
      ],
      "is_edited": false,
      "is_deleted": false,
      "created_at": "2025-01-15T10:30:00Z",
      "updated_at": "2025-01-15T10:30:00Z"
    },
    {
      "id": "msg_uuid_2",
      "conversation_id": "conv_uuid",
      "sender": {
        "id": "user_uuid_2",
        "display_name": "Priya Sharma",
        "avatar_url": "https://..."
      },
      "content": null,
      "message_type": "text",
      "reply_to": null,
      "media": [],
      "is_edited": false,
      "is_deleted": true,
      "created_at": "2025-01-15T10:29:00Z",
      "updated_at": "2025-01-15T10:31:00Z"
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNS0wMS0xNVQxMDoyOTowMFoiLCJpZCI6Ii4uLiJ9",
    "limit": 50
  }
}
```

**Notes:**
- Deleted messages have `is_deleted: true` and `content: null`. The UI should show "This message was deleted".
- `reply_to` is a compact snippet of the original message (not the full message object).
- `media` array can be empty (text-only message) or contain up to 10 items.
- Messages are returned newest-first. To load older messages, use the `next_cursor`.

### Errors

| Status | Code | When |
|--------|------|------|
| 403 | `NOT_CONVERSATION_MEMBER` | User is not in this conversation |
| 404 | `CONVERSATION_NOT_FOUND` | Conversation doesn't exist |

---

## `POST /api/v1/chat/conversations/{conversation_id}/messages`

**Send a message (REST fallback — prefer WebSocket for real-time).**

This endpoint exists as a fallback when WebSocket is unavailable. The primary message sending path is via WebSocket (`message.send` event).

### Request

```json
{
  "content": "Hello, world!",
  "reply_to_message_id": "msg_uuid_or_null",
  "client_message_id": "client_generated_uuid",
  "media_file_ids": ["media_uuid_1", "media_uuid_2"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | No* | Message text (1-4096 chars) |
| `reply_to_message_id` | uuid | No | ID of message being replied to |
| `client_message_id` | uuid | Yes | Client-generated for idempotency |
| `media_file_ids` | uuid[] | No | Previously uploaded media file IDs (max 10) |

*Either `content` or `media_file_ids` must be provided. A message cannot be completely empty.

### Response — `201 Created`

Same structure as a single message object in the list response above.

### Errors

| Status | Code | When |
|--------|------|------|
| 403 | `NOT_CONVERSATION_MEMBER` | User is not in this conversation |
| 404 | `CONVERSATION_NOT_FOUND` | Conversation doesn't exist |
| 404 | `MESSAGE_NOT_FOUND` | `reply_to_message_id` doesn't exist in this conversation |
| 404 | `MEDIA_NOT_FOUND` | One or more `media_file_ids` don't exist |
| 409 | `DUPLICATE_MESSAGE` | `client_message_id` already used (returns existing message) |
| 422 | `CONTENT_EMPTY` | Both content and media_file_ids are empty |
| 422 | `CONTENT_TOO_LONG` | Content exceeds 4096 characters |
| 422 | `TOO_MANY_MEDIA` | More than 10 media files attached |

---

## `PATCH /api/v1/chat/messages/{message_id}`

**Edit a sent message (sender only).**

### Request

```json
{
  "content": "Updated message content"
}
```

### Response — `200 OK`

Returns the updated message object.

### Errors

| Status | Code | When |
|--------|------|------|
| 403 | `NOT_MESSAGE_SENDER` | Only the sender can edit their message |
| 404 | `MESSAGE_NOT_FOUND` | Message doesn't exist |
| 422 | `CANNOT_EDIT_DELETED` | Message is already deleted |

---

## `DELETE /api/v1/chat/messages/{message_id}`

**Soft-delete a message.**

Sender can delete their own. Conversation admin/owner or institution admin can delete any.

### Response — `204 No Content`

### Errors

| Status | Code | When |
|--------|------|------|
| 403 | `INSUFFICIENT_PERMISSIONS` | User cannot delete this message |
| 404 | `MESSAGE_NOT_FOUND` | Message doesn't exist |

---

## `POST /api/v1/chat/conversations/{conversation_id}/read`

**Mark messages as read up to a specific message.**

### Request

```json
{
  "last_read_message_id": "msg_uuid"
}
```

### Response — `200 OK`

```json
{
  "data": {
    "conversation_id": "conv_uuid",
    "last_read_message_id": "msg_uuid",
    "read_at": "2025-01-15T10:35:00Z"
  }
}
```

### Errors

| Status | Code | When |
|--------|------|------|
| 403 | `NOT_CONVERSATION_MEMBER` | User is not in this conversation |
| 404 | `MESSAGE_NOT_FOUND` | The referenced message doesn't exist |
