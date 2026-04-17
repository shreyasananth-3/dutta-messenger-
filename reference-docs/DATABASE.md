# Database Design

> **PostgreSQL is the single source of truth.** Every piece of application state lives here. Redis is a cache and message bus, never a primary store.

---

## Principles

1. **Every table has a UUID primary key** — never auto-incrementing integers (prevents enumeration attacks).
2. **Every table has `created_at` and `updated_at` timestamps** — always set by the server, never the client.
3. **Soft delete where appropriate** — messages use `deleted_at` (users can "delete" but admins can audit). Users use hard delete (GDPR).
4. **Foreign keys everywhere** — relational integrity is non-negotiable.
5. **Indexes are deliberate** — every query pattern has a supporting index. No index without a known query.
6. **Migrations are forward-only** — never edit a migration after it's been applied. Write a new one.

---

## Schema Overview

```
┌──────────────────┐     ┌──────────────────────┐
│     users         │     │    institutions       │
│─────────────────  │     │──────────────────────│
│ id (PK)           │◄────│ id (PK)              │
│ institution_id(FK)│     │ name                  │
│ email             │     │ slug                  │
│ password_hash     │     │ settings (JSONB)      │
│ display_name      │     │ created_at            │
│ avatar_url        │     └──────────────────────┘
│ status            │
│ last_seen_at      │
│ created_at        │
│ updated_at        │
└──────┬───────────┘
       │
       │ 1:N
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│  conversations       │     │  groups               │
│──────────────────────│     │──────────────────────│
│ id (PK)              │◄────│ id (PK)              │
│ type (dm|group)      │     │ conversation_id (FK)  │
│ created_at           │     │ name                  │
│ updated_at           │     │ description           │
└──────┬───────────────┘     │ avatar_url            │
       │                     │ created_by (FK→users) │
       │ 1:N                 │ settings (JSONB)      │
       ▼                     │ created_at            │
┌──────────────────────┐     └──────────────────────┘
│  conversation_members│
│──────────────────────│
│ id (PK)              │
│ conversation_id (FK) │
│ user_id (FK)         │
│ role (owner|admin|   │
│       member)        │
│ joined_at            │
│ muted_until          │
└──────────────────────┘

       │
       │ 1:N
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│  messages            │     │  message_reads        │
│──────────────────────│     │──────────────────────│
│ id (PK)              │     │ id (PK)              │
│ conversation_id (FK) │     │ message_id (FK)      │
│ sender_id (FK→users) │     │ user_id (FK→users)   │
│ content              │     │ read_at              │
│ reply_to_id (FK→self)│     └──────────────────────┘
│ client_message_id    │
│ message_type         │
│ metadata (JSONB)     │
│ deleted_at           │
│ created_at           │
│ updated_at           │
└──────┬───────────────┘
       │
       │ 1:N
       ▼
┌──────────────────────┐
│  message_media       │
│──────────────────────│
│ id (PK)              │
│ message_id (FK)      │
│ media_file_id (FK)   │
│ sort_order           │
└──────────────────────┘

┌──────────────────────┐
│  media_files         │
│──────────────────────│
│ id (PK)              │
│ uploader_id (FK)     │
│ file_name            │
│ file_size            │
│ mime_type            │
│ storage_key          │  ← S3/MinIO object key
│ thumbnail_key        │
│ metadata (JSONB)     │  ← dimensions, duration, etc.
│ created_at           │
└──────────────────────┘

┌──────────────────────┐     ┌──────────────────────┐
│  roles               │     │  permissions          │
│──────────────────────│     │──────────────────────│
│ id (PK)              │     │ id (PK)              │
│ institution_id (FK)  │     │ codename             │
│ name                 │     │ description          │
│ is_system_role       │     └──────────────────────┘
│ created_at           │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│  role_permissions    │     │  user_roles           │
│──────────────────────│     │──────────────────────│
│ role_id (FK)         │     │ user_id (FK)         │
│ permission_id (FK)   │     │ role_id (FK)         │
│ (composite PK)       │     │ assigned_by (FK)     │
└──────────────────────┘     │ assigned_at          │
                             └──────────────────────┘
```

---

## Migration Strategy (Alembic)

### Creating a Migration

```bash
# Auto-generate from model changes
alembic revision --autogenerate -m "add_reply_to_id_to_messages"

# Manual migration (for data migrations, complex changes)
alembic revision -m "backfill_conversation_types"
```

### Migration Rules

1. **Every migration has both `upgrade()` and `downgrade()`** — even if downgrade is just `pass` with a comment explaining why it's irreversible.
2. **Data migrations are separate from schema migrations** — never mix `ALTER TABLE` with `UPDATE` in the same migration.
3. **Test migrations against a copy of production data** before applying.
4. **Migration files are named**: `{revision_id}_{description}.py`

### Running Migrations

```bash
# Apply all pending
alembic upgrade head

# Apply one step
alembic upgrade +1

# Rollback one step
alembic downgrade -1

# Check current state
alembic current
```

---

## Index Strategy

Every index must have a comment explaining which query it supports.

```sql
-- Supports: GET /api/v1/chat/conversations/{id}/messages?cursor=...
-- Query: SELECT * FROM messages WHERE conversation_id = ? AND created_at < ? ORDER BY created_at DESC LIMIT 50
CREATE INDEX idx_messages_conversation_created
    ON messages (conversation_id, created_at DESC);

-- Supports: Idempotency check on message creation
-- Query: SELECT * FROM messages WHERE client_message_id = ?
CREATE UNIQUE INDEX idx_messages_client_message_id
    ON messages (client_message_id);

-- Supports: User's conversation list
-- Query: SELECT * FROM conversation_members WHERE user_id = ?
CREATE INDEX idx_conversation_members_user
    ON conversation_members (user_id);

-- Supports: Read receipt lookup
-- Query: SELECT * FROM message_reads WHERE message_id = ?
CREATE INDEX idx_message_reads_message
    ON message_reads (message_id);

-- Supports: User search by email or name
-- Query: SELECT * FROM users WHERE institution_id = ? AND (email ILIKE ? OR display_name ILIKE ?)
CREATE INDEX idx_users_institution_search
    ON users (institution_id, email, display_name);
```

---

## JSONB Usage

Use `JSONB` columns for semi-structured data that varies between instances but doesn't need relational queries.

**Good uses of JSONB:**
- `media_files.metadata` → `{"width": 1920, "height": 1080, "duration_seconds": 120}`
- `groups.settings` → `{"allow_member_invite": true, "max_members": 100}`
- `institutions.settings` → `{"default_role": "member", "file_size_limit_mb": 50}`

**Bad uses of JSONB (use proper columns instead):**
- User email, name, status → these are queried constantly, need indexes
- Message content → full-text search needs a proper column
- Foreign key relationships → JSONB can't enforce referential integrity

---

## Connection Pooling

```python
# shared/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    settings.database_url,
    pool_size=20,              # Base pool connections
    max_overflow=10,           # Extra connections under load
    pool_pre_ping=True,        # Verify connections before use
    pool_recycle=3600,         # Recycle connections every hour
    echo=settings.debug,       # Log SQL in debug mode only
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```
