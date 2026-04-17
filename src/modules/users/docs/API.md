# Users API

> **Stage 4a.** Feature-flag: `ENABLE_USERS` (default OFF in `src/config.py`).
> Flip to ON in `.env` or via env var to expose these endpoints.

All paths are under the `/api/v1` prefix. All responses use the standard
envelope from `docs/design/api-versioning.md`:

```json
{ "data": { ... } }                                // success
{ "error": { "code": "…", "message": "…", "details": {…} } }   // error
```

Every endpoint requires a Bearer JWT (via `Authorization: Bearer <token>`).
A missing or bad token returns `401` with the canonical error envelope.

---

## Endpoint summary

| Method | Path | Purpose | Stage |
|--------|------|---------|-------|
| `GET` | `/api/v1/users/me` | Caller's own profile | 4a |
| `PATCH` | `/api/v1/users/me` | Update own profile | 4a |
| `GET` | `/api/v1/users/{id}` | Another user's public profile | 4a |
| `GET` | `/api/v1/users/search` | Search users in the institution | 4a |
| `GET` | `/api/v1/users/online` | Online/offline lookup for up to 200 users | 4a |
| `GET` | `/api/v1/users/me/settings` | Caller's settings | 4a |
| `PATCH` | `/api/v1/users/me/settings` | Update own settings | 4a |
| `PATCH` | `/api/v1/users/{id}/status` | Admin: activate / suspend user | **4b (ACL)** |

The last row is intentionally deferred to Stage 4b so it can ship with a
real `institution.manage_users` permission check. See the header comment in
`src/modules/users/services/user_service.py` for the rationale.

---

## `GET /api/v1/users/me`

Returns the caller's full profile. `email` is included here (and **only** here
— `/users/{id}` strips it from the response).

**Response 200**
```json
{
  "data": {
    "id": "31d33b44-022d-450c-9b54-c0a011ed6cc8",
    "institution_id": "5a29cbeb-c928-4067-8959-ef215bed07f1",
    "email": "admin@smoke.test",
    "full_name": "Smoke Admin",
    "avatar_url": null,
    "bio": null,
    "phone_number": null,
    "status": "offline",
    "is_active": true,
    "is_online": false,
    "last_seen_at": "2026-04-17T22:14:36.756132Z",
    "created_at": "2026-04-17T22:14:27.720219Z",
    "updated_at": "2026-04-17T22:14:27.720219Z"
  }
}
```

**Errors**
- `401 AUTHENTICATION_FAILED` — missing / expired / malformed token.

---

## `PATCH /api/v1/users/me`

Partial update of profile fields the caller is allowed to edit. Every field
is optional; only fields present in the body are written. Email changes go
through `auth`; user activation/suspension is admin-only (Stage 4b).

**Request body**
```json
{
  "full_name": "Admin Renamed",   // 1–100 chars
  "avatar_url": "https://…",      // ≤ 500 chars
  "bio": "Hello 😀 नमस्ते 你好",  // ≤ 500 chars, Unicode OK
  "phone_number": "+91-9999999999"// ≤ 20 chars
}
```

**Response 200** — same shape as `GET /users/me`.

**Errors**
- `401 AUTHENTICATION_FAILED` — as above.
- `422 VALIDATION_ERROR` — bio > 500 chars, full_name > 100 chars, etc.

**Audit:** emits one `user.profile.updated` row with
`metadata.fields = [<changed keys>]`.

---

## `GET /api/v1/users/{user_id}`

Public profile of any user in the caller's institution. `email` is stripped.

**Response 200**
```json
{
  "data": {
    "id": "636eac45-1638-49c6-ba4f-b7d4dab06321",
    "institution_id": "5a29cbeb-c928-4067-8959-ef215bed07f1",
    "email": null,
    "full_name": "Invitee User",
    "avatar_url": null,
    "bio": null,
    "phone_number": null,
    "status": "offline",
    "is_active": true,
    "is_online": false,
    "last_seen_at": null,
    "created_at": "2026-04-17T22:15:05.897407Z",
    "updated_at": "2026-04-17T22:15:05.897407Z"
  }
}
```

**Errors**
- `401 AUTHENTICATION_FAILED`.
- `404 NOT_FOUND` — unknown user ID **or** user belongs to a different
  institution. Returning 404 (not 403) for cross-institution lookups is
  deliberate per `docs/design/tenant-isolation.md` — an attacker must not be
  able to enumerate other institutions' user IDs.
- `422 VALIDATION_ERROR` — malformed UUID in path.

---

## `GET /api/v1/users/search?q=…&limit=…`

Search users inside the caller's institution by `full_name` or `email`.
Backed by a `pg_trgm` GIN index on `users.full_name` (migration `0004`).

**Query params**
- `q` — required, 1–100 chars.
- `limit` — optional, 1–100, default 20.

**Response 200**
```json
{
  "data": {
    "results": [
      {
        "id": "636eac45-1638-49c6-ba4f-b7d4dab06321",
        "full_name": "Invitee User",
        "avatar_url": null,
        "bio": null,
        "status": "offline",
        "is_online": false
      }
    ],
    "has_more": false,
    "next_cursor": null
  }
}
```

Results exclude soft-deleted (`deleted_at IS NOT NULL`) and inactive
(`is_active = false`) users. Cross-institution results are never returned.

**Errors**
- `401 AUTHENTICATION_FAILED`.
- `422 VALIDATION_ERROR` — empty `q`, `limit` outside 1–100.

---

## `GET /api/v1/users/online?user_ids=…&user_ids=…`

Bulk online/offline lookup. One Redis pipeline call regardless of list size.

**Query params**
- `user_ids` — repeated, 1–200 UUIDs.

**Response 200**
```json
{
  "data": {
    "online": {
      "31d33b44-022d-450c-9b54-c0a011ed6cc8": true,
      "636eac45-1638-49c6-ba4f-b7d4dab06321": false
    }
  }
}
```

Presence is Redis-backed — a key `user:online:{id}` with 60-second TTL is
refreshed by the WebSocket heartbeat (Stage 4d, chat module). For users the
caller doesn't share an institution with, the server simply reports `false`
(no leak of existence).

**Errors**
- `401 AUTHENTICATION_FAILED`.
- `422 VALIDATION_ERROR` — empty or > 200 `user_ids`.

---

## `GET /api/v1/users/me/settings`

Returns the caller's settings row. First call lazily seeds defaults.

**Response 200**
```json
{
  "data": {
    "user_id": "31d33b44-022d-450c-9b54-c0a011ed6cc8",
    "notification_messages": true,
    "notification_groups": true,
    "notification_sound": true,
    "theme": "system",
    "language": "en",
    "created_at": "2026-04-17T22:30:00.000000Z",
    "updated_at": "2026-04-17T22:30:00.000000Z"
  }
}
```

**Errors**
- `401 AUTHENTICATION_FAILED`.

---

## `PATCH /api/v1/users/me/settings`

Partial update of settings. Every field optional.

**Request body**
```json
{
  "notification_messages": true,
  "notification_groups": false,
  "notification_sound": false,
  "theme": "dark",          // one of "light" | "dark" | "system"
  "language": "hi"          // 2–5 chars, lower-cased server-side
}
```

**Response 200** — same shape as `GET /users/me/settings`.

**Errors**
- `401 AUTHENTICATION_FAILED`.
- `422 VALIDATION_ERROR` — `theme` not in the allowed set, `language`
  length out of range.

**Audit:** emits one `user.settings.updated` row with
`metadata.fields = [<changed keys>]`.

---

## Error-code catalog (this module)

| Code | HTTP | Raised by |
|------|------|-----------|
| `NOT_FOUND` | 404 | get user / get settings on an unknown or cross-tenant ID |
| `AUTHENTICATION_FAILED` | 401 | missing / malformed / expired JWT |
| `VALIDATION_ERROR` | 422 | Pydantic request-model validation failure |

All error codes follow the canonical envelope from
`docs/design/api-versioning.md`.
