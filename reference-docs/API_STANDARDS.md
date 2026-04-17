# API Design Standards

> **Every REST endpoint in this project follows these rules.** Read this before writing any route.

---

## URL Structure

```
/api/v1/{module}/{resource}
/api/v1/{module}/{resource}/{id}
/api/v1/{module}/{resource}/{id}/{sub-resource}
```

**Rules:**
- Always versioned (`/api/v1/`)
- Resources are **plural nouns** (`/users`, `/messages`, `/groups`)
- Resource names are **kebab-case** (`/group-members`, not `/groupMembers`)
- No verbs in URLs — the HTTP method IS the verb
- IDs are UUIDs, never sequential integers (security: prevents enumeration)

**Examples:**
```
GET    /api/v1/chat/conversations                    ← List user's conversations
POST   /api/v1/chat/conversations                    ← Create conversation (1:1 or group)
GET    /api/v1/chat/conversations/{id}                ← Get conversation details
GET    /api/v1/chat/conversations/{id}/messages       ← List messages in conversation
POST   /api/v1/chat/conversations/{id}/messages       ← Send message (REST fallback)
GET    /api/v1/groups/{id}/members                    ← List group members
POST   /api/v1/groups/{id}/members                    ← Add member to group
DELETE /api/v1/groups/{id}/members/{user_id}          ← Remove member from group
POST   /api/v1/media/upload                           ← Upload a file
GET    /api/v1/media/{id}/download                    ← Download a file
```

---

## HTTP Methods

| Method | Meaning | Idempotent | Response Code |
|--------|---------|-----------|---------------|
| `GET` | Retrieve resource(s) | Yes | 200 |
| `POST` | Create resource | No | 201 |
| `PUT` | Full replacement of resource | Yes | 200 |
| `PATCH` | Partial update of resource | No | 200 |
| `DELETE` | Remove resource | Yes | 204 (no body) |

---

## Request Format

### Headers (Required on Every Request)

```
Authorization: Bearer {jwt_token}
Content-Type: application/json
X-Request-ID: {uuid}                    ← Client-generated, for tracing
X-Client-Version: 1.0.0                ← Flutter app version
```

### Request Body

Use Pydantic models. The model IS the documentation.

```python
from pydantic import BaseModel, Field
import uuid

class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Message text content.",
    )
    reply_to_message_id: uuid.UUID | None = Field(
        default=None,
        description="ID of the message being replied to. Must exist in the same conversation.",
    )
    client_message_id: uuid.UUID = Field(
        ...,
        description="Client-generated UUID for idempotency and deduplication.",
    )
```

---

## Response Format

### Success Response — Single Resource

```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "conversation_id": "660e8400-e29b-41d4-a716-446655440000",
    "sender_id": "770e8400-e29b-41d4-a716-446655440000",
    "content": "Hello, world!",
    "reply_to_message_id": null,
    "created_at": "2025-01-15T10:30:00Z",
    "updated_at": "2025-01-15T10:30:00Z"
  }
}
```

### Success Response — List (with Cursor Pagination)

```json
{
  "data": [
    { "id": "...", "content": "Message 1", "created_at": "..." },
    { "id": "...", "content": "Message 2", "created_at": "..." }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNS0wMS0xNVQxMDozMDowMFoiLCJpZCI6Ii4uLiJ9",
    "limit": 50
  }
}
```

**Why cursor pagination, not offset?**
Chat messages are constantly being inserted. Offset-based pagination (`page=2`) produces duplicate or missing messages when new messages arrive between page loads. Cursor-based pagination (`created_at < cursor_timestamp`) is stable.

### Error Response

```json
{
  "error": {
    "code": "NOT_CONVERSATION_MEMBER",
    "message": "You are not a member of this conversation.",
    "details": {
      "conversation_id": "660e8400-e29b-41d4-a716-446655440000"
    },
    "request_id": "req_abc123"
  }
}
```

### Standard Error Codes

| HTTP Status | When to Use | Example `code` |
|------------|-------------|----------------|
| 400 | Bad request / validation failure | `VALIDATION_ERROR` |
| 401 | Missing or invalid auth token | `TOKEN_EXPIRED`, `TOKEN_INVALID` |
| 403 | Authenticated but not authorized | `NOT_CONVERSATION_MEMBER`, `INSUFFICIENT_PERMISSIONS` |
| 404 | Resource not found | `USER_NOT_FOUND`, `MESSAGE_NOT_FOUND` |
| 409 | Conflict (duplicate) | `USER_ALREADY_MEMBER`, `DUPLICATE_MESSAGE` |
| 413 | File too large | `FILE_TOO_LARGE` |
| 422 | Semantically invalid | `CANNOT_REPLY_TO_DELETED_MESSAGE` |
| 429 | Rate limited | `RATE_LIMIT_EXCEEDED` |
| 500 | Server error (never expose internals) | `INTERNAL_ERROR` |

---

## Pydantic Response Model Pattern

```python
from pydantic import BaseModel
from datetime import datetime
import uuid


class MessageResponse(BaseModel):
    """A single message in a conversation."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    sender_avatar_url: str | None
    content: str
    reply_to: "MessageReplySnippet | None"
    media: list["MediaAttachment"]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageReplySnippet(BaseModel):
    """Compact representation of the message being replied to."""

    id: uuid.UUID
    sender_name: str
    content_preview: str = Field(
        ..., description="First 100 characters of the original message."
    )


class PaginatedResponse(BaseModel):
    """Standard paginated list response."""

    data: list
    pagination: "PaginationMeta"


class PaginationMeta(BaseModel):
    has_more: bool
    next_cursor: str | None
    limit: int
```

---

## Pagination Rules

- **Default limit**: 50 items
- **Max limit**: 100 items
- **Cursor-based only**: No offset/page-number pagination
- **Cursor encoding**: Base64-encoded JSON of `{created_at, id}` for stable ordering
- **Sort order**: Most recent first (descending `created_at`) for messages; configurable for other resources

```
GET /api/v1/chat/conversations/{id}/messages?limit=50&cursor=eyJjcmVhdGVkX2F0Ijo...
```

---

## Idempotency

For `POST` endpoints that create resources, the client sends a `client_message_id` (or equivalent). The server checks for duplicates before inserting.

```python
# In message service
existing = await db.execute(
    select(Message).where(Message.client_message_id == request.client_message_id)
)
if existing.scalar_one_or_none():
    return existing  # Return existing, don't create duplicate
```

This prevents duplicate messages when the client retries a failed request.

---

## Versioning

- API version is in the URL path: `/api/v1/`
- When breaking changes are needed, create `/api/v2/` and keep `/api/v1/` working for at least 6 months
- Non-breaking changes (adding fields, new endpoints) go in the current version

---

## Rate Limiting

| Endpoint Category | Limit |
|-------------------|-------|
| Auth (login, register) | 10 requests/minute |
| Message sending | 60 messages/minute |
| File upload | 10 uploads/minute |
| General API | 120 requests/minute |
| WebSocket connections | 5 concurrent per user |

Rate limit info is returned in response headers:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1705312800
```
