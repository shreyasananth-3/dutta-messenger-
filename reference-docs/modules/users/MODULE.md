# Module: Users

> **This module manages user profiles, search, online status, and user settings.**

---

## What This Module Does

- View and update user profiles (display name, avatar, bio)
- Search users within the institution
- Track online/offline status (via Redis)
- Manage user settings (notification preferences, theme, language)
- Deactivate/reactivate user accounts (admin only)

## What This Module Does NOT Do

- Registration/login → see [auth/MODULE.md](../auth/MODULE.md)
- Permission management → see [acl/MODULE.md](../acl/MODULE.md)

---

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | Must be authenticated to access user endpoints |

---

## Online Status Design

Online status is tracked in Redis, NOT in PostgreSQL. It changes too frequently for the database.

```
Redis key:   user:online:{user_id}
Value:       "1"
TTL:         60 seconds (auto-renewed by WebSocket heartbeat)
```

**Flow:**
1. User connects via WebSocket → set Redis key with 60s TTL.
2. Every WebSocket heartbeat (30s) → refresh TTL to 60s.
3. User disconnects → delete Redis key immediately.
4. If WebSocket dies without clean disconnect → key expires after 60s automatically.

**Checking status:**
```python
async def is_user_online(user_id: uuid.UUID, redis: Redis) -> bool:
    return await redis.exists(f"user:online:{user_id}")

async def get_online_users(user_ids: list[uuid.UUID], redis: Redis) -> set[uuid.UUID]:
    pipeline = redis.pipeline()
    for uid in user_ids:
        pipeline.exists(f"user:online:{uid}")
    results = await pipeline.execute()
    return {uid for uid, exists in zip(user_ids, results) if exists}
```

---

## User Search

Users can search for other users within their institution by name or email.

```
GET /api/v1/users/search?q=raj&limit=20
```

**Implementation:** PostgreSQL `ILIKE` with trigram index for partial matching.

```sql
-- Index for search
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_users_search_trgm
    ON users USING gin (display_name gin_trgm_ops);
```

---

## API Endpoints

| Method | Path | Purpose | Permission |
|--------|------|---------|-----------|
| `GET` | `/api/v1/users/me` | Get current user's profile | any authenticated |
| `PATCH` | `/api/v1/users/me` | Update own profile | any authenticated |
| `GET` | `/api/v1/users/{id}` | Get another user's profile | any authenticated |
| `GET` | `/api/v1/users/search` | Search users in institution | any authenticated |
| `GET` | `/api/v1/users/online` | Get online status for a list of user IDs | any authenticated |
| `PATCH` | `/api/v1/users/{id}/status` | Activate/deactivate user | `institution.manage_users` |
| `GET` | `/api/v1/users/me/settings` | Get user settings | any authenticated |
| `PATCH` | `/api/v1/users/me/settings` | Update user settings | any authenticated |

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `users` | Core user record (shared with auth module — auth owns the table, users module reads/updates profile fields) |
| `user_settings` | Per-user preferences: notification settings, theme, language |

---

## Profile Fields

| Field | Editable By User | Editable By Admin | Max Length |
|-------|-----------------|-------------------|-----------|
| `display_name` | Yes | Yes | 100 chars |
| `avatar_url` | Yes (via media upload) | Yes | — |
| `bio` | Yes | Yes | 500 chars |
| `email` | No | Yes | — |
| `status` (active/suspended) | No | Yes | — |
| `last_seen_at` | Auto (system) | No | — |

---

## User Settings Schema

Stored in `user_settings` table as structured columns (not JSONB — these are queried individually).

```sql
CREATE TABLE user_settings (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    notification_messages   BOOLEAN NOT NULL DEFAULT TRUE,
    notification_groups     BOOLEAN NOT NULL DEFAULT TRUE,
    notification_sound      BOOLEAN NOT NULL DEFAULT TRUE,
    theme                   VARCHAR(10) NOT NULL DEFAULT 'system'
                            CHECK (theme IN ('light', 'dark', 'system')),
    language                VARCHAR(5) NOT NULL DEFAULT 'en',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
