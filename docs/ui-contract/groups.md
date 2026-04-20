# Groups Module — API Contract

**Status:** Live on `main`.
**Base path:** `/api/v1`
**Auth required:** Bearer on every endpoint.

All requests send/receive JSON (`Content-Type: application/json`). All responses follow the standard envelope (`{"data": {...}}` on success, `{"error": {...}}` on failure — see [README.md §4](README.md#4-standard-response-envelope)).

---

## The one concept you need before reading this doc

A **group** in DuttaMessenger can be one of two modes:

| Mode | What it's like | Has topics? | Where messages go |
|------|---------------|-------------|-------------------|
| `simple` | WhatsApp group — one chat, one timeline | No | One conversation, shared by everyone in the group |
| `topics` | Telegram group with topics / Slack channel with threads | Yes | One conversation **per topic** |

Pick when you create the group. You **cannot** switch modes later.

The Flutter UI should look different for the two modes — simple groups open straight into a chat screen, topics groups open into a topic list (Slack-style sidebar), and tapping a topic opens that topic's chat screen.

---

## Endpoints at a glance

| Method | Path | Purpose | Who can call |
|--------|------|---------|-------------|
| POST | `/groups` | Create a group | Any authenticated user |
| GET | `/groups` | List **my** groups | Any authenticated user |
| GET | `/groups/{group_id}` | Get one group | Members only |
| PATCH | `/groups/{group_id}` | Update name / description / avatar | Admin or owner |
| DELETE | `/groups/{group_id}` | Archive (soft-delete) | Owner only |
| GET | `/groups/{group_id}/members` | List members | Members only |
| POST | `/groups/{group_id}/members` | Add a member | Admin or owner |
| DELETE | `/groups/{group_id}/members/{user_id}` | Remove a member | Admin or owner (cannot remove owner) |
| GET | `/groups/{group_id}/topics` | List topics | Members only |
| POST | `/groups/{group_id}/topics` | Create a topic | Admin or owner (group must be `mode=topics`) |
| DELETE | `/groups/{group_id}/topics/{topic_id}` | Delete a topic | Admin or owner (cannot delete "General") |

---

## 1. `POST /api/v1/groups` — Create a group

### Request
```json
{
  "name": "Class 9B",
  "description": "Grade 9 section B — 2026 batch",
  "mode": "topics"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | 1–255 chars |
| `description` | string \| null | no | 0–2000 chars |
| `mode` | `"simple"` or `"topics"` | no | Default `"simple"` |

### 201 Created
```json
{
  "data": {
    "id": "e71ccb5d-3f02-4e43-b941-3de94b8fb413",
    "institution_id": "301842b2-3a73-46d5-b4d5-6ab8e13c3829",
    "name": "Class 9B",
    "description": "Grade 9 section B — 2026 batch",
    "avatar_url": null,
    "mode": "topics",
    "created_by_user_id": "8585217f-04ab-43b8-8edd-9bd7ba000a93",
    "is_archived": false,
    "created_at": "2026-04-19T16:44:06.227368Z",
    "updated_at": "2026-04-19T16:44:06.227368Z"
  }
}
```

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `AUTHENTICATION_FAILED` | Missing / invalid token |
| 409 | `CONFLICT` | `mode` is not one of `"simple"` / `"topics"` |
| 422 | `VALIDATION_ERROR` | Missing `name`, name too long, etc. |

### Business rules

- Creator becomes **owner** automatically. No separate "add yourself as member" call.
- If `mode="topics"`, a topic named **"General"** is auto-created. Treat it as the default topic — the Flutter UI usually opens to it.
- The Flutter UI should **never** let the user pick `mode` again after creation.

---

## 2. `GET /api/v1/groups` — List my groups

No request body. Returns an **array** of groups the caller is a member of, newest first. Archived groups are excluded.

### 200 OK
```json
{
  "data": [
    {
      "id": "...",
      "name": "Class 9B",
      "mode": "topics",
      ...
    },
    {
      "id": "...",
      "name": "Staff Room",
      "mode": "simple",
      ...
    }
  ]
}
```

### Flutter note

This is the endpoint to call when you open the home screen / group list. Cache locally + refetch on pull-to-refresh. No pagination today — if a user has 500+ groups this would need to change, but at 5k users and typical usage that's unlikely.

---

## 3. `GET /api/v1/groups/{group_id}` — Get one group

No request body. Returns the same shape as create.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 404 | `NOT_FOUND` | Group doesn't exist, **or** caller is not a member (returned as 404 for privacy, not 403) |

### Flutter note

Don't show "you're not a member" messages to the user — the server deliberately returns 404 for both cases to avoid leaking group existence across members. Treat both as "not found".

---

## 4. `PATCH /api/v1/groups/{group_id}` — Update group

### Request
All three fields are optional. Send only what you want to change.
```json
{
  "name": "Class 9B — 2026",
  "description": "Updated description",
  "avatar_url": "https://.../avatar.png"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `name` | string \| null | 1–255 chars |
| `description` | string \| null | 0–2000 chars |
| `avatar_url` | string \| null | 0–2000 chars |

### 200 OK
Updated `GroupResponse`.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Caller is a member but not admin / owner |
| 404 | `NOT_FOUND` | Group not found |

---

## 5. `DELETE /api/v1/groups/{group_id}` — Archive group

Soft-delete: sets `is_archived=true`. Messages and member records are preserved. The group disappears from `GET /groups` but direct `GET /groups/{id}` still returns it (with `is_archived: true`) — useful if you have a deep-link open.

### 204 No Content
Empty response.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Not the owner (admins can't archive) |
| 404 | `NOT_FOUND` | Group not found |

---

## 6. `GET /api/v1/groups/{group_id}/members` — List members

Returns an array, oldest member first (owner is almost always first).

### 200 OK
```json
{
  "data": [
    {
      "id": "5350484f-fe8c-491f-a903-f73f50592226",
      "group_id": "e71ccb5d-3f02-4e43-b941-3de94b8fb413",
      "user_id": "8585217f-04ab-43b8-8edd-9bd7ba000a93",
      "role": "owner",
      "joined_at": "2026-04-19T16:44:06.411284Z"
    },
    {
      "id": "...",
      "user_id": "...",
      "role": "admin",
      "joined_at": "..."
    },
    {
      "id": "...",
      "user_id": "...",
      "role": "member",
      "joined_at": "..."
    }
  ]
}
```

`role` is always one of: `"owner"`, `"admin"`, `"member"`. There is exactly one owner per group.

### Flutter note

To show names + avatars on the member list, join this with cached user data from `/users/search` or `/users/{id}`. This endpoint returns `user_id` only, not profile data.

---

## 7. `POST /api/v1/groups/{group_id}/members` — Add member

### Request
```json
{
  "user_id": "b7115bfa-bb79-4045-9626-377265f8e2fc",
  "role": "member"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `user_id` | UUID string | yes | User must already exist in your institution |
| `role` | `"member"` or `"admin"` | no | Default `"member"`. You cannot add someone as `"owner"` — there's only one, and it's the creator |

### 201 Created
```json
{
  "data": {
    "group_id": "...",
    "user_id": "...",
    "role": "member",
    "reused": false
  }
}
```

`reused: true` means "this user was already a member" — the call is idempotent, safe to retry. If `reused: true`, no audit log is written and no duplicate row is created.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Caller is a member but not admin / owner |
| 404 | `NOT_FOUND` | Group not found, or target user doesn't exist in your institution |

---

## 8. `DELETE /api/v1/groups/{group_id}/members/{user_id}` — Remove member

### 204 No Content

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Caller is a member but not admin / owner, **or** target is the owner (owner cannot be removed, only transferred) |
| 404 | `NOT_FOUND` | Group / member not found |

### Flutter note

If the removed user is currently connected by WebSocket, they'll still receive any messages that were already in flight, but new `message.new` frames for this conversation will not reach them after the server processes the removal.

---

## 9. `GET /api/v1/groups/{group_id}/topics` — List topics

Returns an array, oldest first. Empty array if the group is `mode="simple"`.

### 200 OK
```json
{
  "data": [
    {
      "id": "bb623aa2-d1cf-49a7-bb0c-6a2143e2ec2f",
      "group_id": "e71ccb5d-3f02-4e43-b941-3de94b8fb413",
      "name": "General",
      "description": null,
      "icon_emoji": null,
      "created_by_user_id": "...",
      "created_at": "2026-04-19T16:44:06.411284Z"
    },
    {
      "id": "...",
      "name": "algebra",
      "description": "Chapter 5–8 problems",
      "icon_emoji": "📐",
      ...
    }
  ]
}
```

---

## 10. `POST /api/v1/groups/{group_id}/topics` — Create topic

### Request
```json
{
  "name": "homework",
  "description": "Weekly homework drops",
  "icon_emoji": "📝"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | 1–255 chars, unique within the group |
| `description` | string \| null | no | 0–2000 chars |
| `icon_emoji` | string \| null | no | 0–10 chars (one emoji) |

### 201 Created
Returns the created `TopicResponse`.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Not admin / owner |
| 404 | `NOT_FOUND` | Group not found |
| 409 | `CONFLICT` | Group is `mode="simple"` — can't have topics, OR a topic with this name already exists in the group |

---

## 11. `DELETE /api/v1/groups/{group_id}/topics/{topic_id}` — Delete topic

### 204 No Content

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Not admin / owner, or target is **"General"** (the default topic cannot be deleted) |
| 404 | `NOT_FOUND` | Group / topic not found |

---

## Connecting groups + chat

This is the **most common mistake** the Flutter team will make: they'll add a user to a group and expect them to see messages automatically. That's not how it works. Here's the actual rule:

> **Being a group member ≠ being a conversation member.**
> When a user opens the chat screen for a group / topic, the client **must** call `POST /api/v1/chat/conversations/open-group` to (a) get the conversation id and (b) register the caller as a conversation member so they can receive WebSocket fanout.

### The correct flow

```dart
// Flutter pseudo-code when the user taps on a group in the list
Future<void> openGroup(String groupId, String? topicId) async {
  final resp = await api.post(
    '/api/v1/chat/conversations/open-group',
    body: {
      'group_id': groupId,
      if (topicId != null) 'topic_id': topicId,
    },
  );
  final conversationId = resp.data['id'];

  // Now subscribe over WebSocket to get real-time messages
  ChatService.instance.subscribe(conversationId);

  // Load history once
  final history = await api.get('/api/v1/chat/conversations/$conversationId/messages?limit=50');

  // ...push to chat screen with conversationId + history
}
```

`open-group` is **idempotent** — calling it twice is safe. It returns the same conversation id every time. The server will add the caller as a conversation member if they weren't already.

For **simple** groups, omit `topic_id` — there's one conversation per group.
For **topics** groups, send `topic_id` — there's one conversation per topic.

---

## Minimum "groups tab" implementation

A complete working group UI needs these calls in order. Use this as a checklist:

- [ ] **App launch / tab open:** `GET /groups` → render list. Cache locally.
- [ ] **Group tile tap (simple mode):** `POST /chat/conversations/open-group` with `{group_id}` → navigate to chat screen with returned `conversation_id`.
- [ ] **Group tile tap (topics mode):** `GET /groups/{id}/topics` → render topic list. Cache locally.
- [ ] **Topic tile tap:** `POST /chat/conversations/open-group` with `{group_id, topic_id}` → navigate to chat screen with returned `conversation_id`.
- [ ] **Create group button:** modal with `name` + `description` + `mode` picker → `POST /groups` → prepend to local list.
- [ ] **Group info / edit screen:** `GET /groups/{id}` + `GET /groups/{id}/members`.
- [ ] **Add member (admin UI):** user picker (from `/users/search`) → `POST /groups/{id}/members`.
- [ ] **Remove member (admin UI):** swipe-to-delete on member row → `DELETE /groups/{id}/members/{user_id}`.
- [ ] **Create topic (topics-mode admin UI):** modal with `name` + optional emoji → `POST /groups/{id}/topics`.

Every authenticated request must send `Authorization: Bearer <access_token>`. On 401, call `/auth/refresh`; on 403, show a "you don't have permission" toast; on 404 to `GET /groups/{id}`, navigate back and show "group not found or you're not a member" (same message for both cases).
