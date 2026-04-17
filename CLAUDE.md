# CLAUDE.md — DuttaMessenger

## MANDATORY PRE-FLIGHT — Do This BEFORE Writing ANY Code

**STOP. Before you write a single line of code, complete these steps in order. Do not skip any. If you catch yourself writing code without having done these, stop and restart.**

### Step 1: Read the reference doc for what you're working on

| If you're working on... | Read this FIRST |
|--------------------------|----------------|
| Any module | `reference-docs/modules/{name}/MODULE.md` |
| Database/schema | `reference-docs/DATABASE.md` AND `reference-docs/modules/{name}/SCHEMA.sql` |
| API endpoints | `reference-docs/API_STANDARDS.md` AND `reference-docs/modules/{name}/API.md` (if exists) |
| WebSocket events | `reference-docs/modules/chat/WEBSOCKET.md` |
| Tests | `reference-docs/TESTING.md` |
| Deployment/Docker | `reference-docs/DEPLOYMENT.md` |

**You must actually use the Read tool on these files. "I know what it says" is not acceptable — the reference docs contain exact table definitions, exact field names, exact endpoint contracts. If you guess instead of reading, you WILL get it wrong.**

### Step 2: When starting a new module, copy docs first

Before creating any code files in `src/modules/{name}/`, do this:
1. Create `src/modules/{name}/docs/` folder
2. Copy all files from `reference-docs/modules/{name}/` into it
3. These become the module's living documentation

### Step 3: Use SCHEMA.sql as the source of truth for database tables

Do NOT invent your own table definitions. The `SCHEMA.sql` files in reference-docs have the exact column names, types, indexes, and constraints. Copy them exactly.

### Step 4: Verify your code matches the reference docs

Before finishing any module, check:
- Do your SQLAlchemy models match SCHEMA.sql column names exactly?
- Do your API endpoints match the paths/methods in MODULE.md?
- Do your request/response models match the contracts in API.md?

---

## MANDATORY POST-FLIGHT — Self-Review After EVERY Task Completion

**Before telling the user "done", run this checklist. Re-open files and verify — do not rely on memory. If any box is unchecked, the task is NOT complete.**

### A. Re-read the reference docs for what you just built
Open `reference-docs/modules/{name}/MODULE.md`, `SCHEMA.sql`, `API.md`, `WEBSOCKET.md` (if relevant) again. Compare line-by-line against what you wrote. Memory drifts; docs are truth.

### B. Code correctness checklist
- [ ] Every DB column name/type/nullability matches `SCHEMA.sql` exactly.
- [ ] Every API path/method/status code matches `API.md` exactly.
- [ ] Every Pydantic request/response field matches the documented contract.
- [ ] Every public function has type hints AND a Google-style docstring.
- [ ] No `print()` — only `structlog` with key=value.
- [ ] No f-string log messages.
- [ ] No raw SQL string formatting — only parameterized SQLAlchemy.
- [ ] No `except Exception: pass` — specific exceptions, logged.
- [ ] No business logic in route handlers (max 15 lines, delegate to service).
- [ ] No hardcoded secrets — everything via `src/config.py`.
- [ ] No commented-out code.
- [ ] Module imports obey the build order (auth → users → acl → groups → chat → media → notifications).

### C. Edge-case coverage (per endpoint / per service method)
For every endpoint you added or touched, a test exists for each:
- [ ] Happy path → correct response + correct side effects.
- [ ] No auth token → 401.
- [ ] Wrong role / cross-institution access → 403.
- [ ] Bad input (missing field, wrong type, malformed UUID) → 400/422.
- [ ] Resource not found → 404.
- [ ] Duplicate request (same `Idempotency-Key`) → idempotent result, not a duplicate write.
- [ ] Empty string input.
- [ ] Max-length input (content = 4096 chars for messages).
- [ ] Unicode input (emoji 😀, Hindi नमस्ते, Chinese 你好, RTL عربي).
- [ ] Concurrent request scenario where it matters (double-send, race on read-receipt).
- [ ] Soft-deleted / tombstoned resource is not leaked.
- [ ] Cross-tenant fuzz: a user from institution A cannot see/modify institution B data.

### D. Test artifacts exist
- [ ] Ran `make test` (or `scripts/run_tests.sh`) after the change.
- [ ] New test file(s) mirror source file(s): `foo_service.py` ↔ `test_foo_service.py`.
- [ ] Coverage for the touched module meets its threshold (auth/acl 90%, chat/groups 85%, media/notifications 80%, shared 85%).
- [ ] Branch coverage enabled, not just line coverage.
- [ ] A timestamped folder under `tests/results/` was generated as proof.

### E. Non-functional checks
- [ ] Rate limit applied where appropriate.
- [ ] Audit log entry emitted on mutations.
- [ ] Correlation ID propagates through logs for any new async path.
- [ ] Prometheus metric updated if a new critical path was added.
- [ ] Migration has BOTH `upgrade()` AND tested `downgrade()`.
- [ ] Feature flag wired if this is a new module or risky change.

### F. UI / contract impact
- [ ] OpenAPI snapshot regenerated — diff is intentional, not accidental.
- [ ] Module's `docs/API.md` updated with real request/response examples (copy from test fixtures).
- [ ] Every new error code is listed in the module's API doc.

### G. Final human-visible report
When reporting "done" to the user, state:
1. What was added/changed (file paths).
2. Which checklist boxes above were verified (by re-reading, not from memory).
3. Any box that could NOT be checked — explain why, don't hide it.
4. Path to the `tests/results/` folder that proves it.

**If you are tempted to skip the post-flight because "the change is small" — that is exactly when bugs ship. Run it anyway.**

---

## What This Project Is

A private institutional messaging platform (Telegram-like, self-hosted). Invite-only. No public sign-up. Groups can be simple (one chat, like WhatsApp) or topic-enabled (multiple subchannels, like Telegram Topics). No voice/video calling.

---

## Reference Documentation

All architecture, schema, API specs, and module designs are in the `reference-docs/` folder at the project root.

```
reference-docs/
├── ARCHITECTURE.md              ← System design, tech stack, data flows, dual group mode
├── CONVENTIONS.md               ← Coding standards (Google Python Style)
├── API_STANDARDS.md             ← REST API rules, response format, error codes, pagination
├── DATABASE.md                  ← DB principles, migrations, index strategy
├── TESTING.md                   ← Test pyramid, how to write tests, result storage
├── DEPLOYMENT.md                ← Docker, CI/CD, environment setup
├── PROMPT_GUIDE.md              ← How to prompt AI tools for this project
├── flutter-architecture.md      ← Flutter app structure (for frontend team)
├── adr/
│   └── 001-why-not-firebase.md  ← Why we moved away from Firebase
└── modules/
    ├── auth/
    │   ├── MODULE.md            ← Auth flows, JWT design, security
    │   └── SCHEMA.sql           ← Tables: institutions, users, invitations
    ├── users/
    │   └── MODULE.md            ← Profiles, search, online status
    ├── acl/
    │   ├── MODULE.md            ← Roles, permissions, three-level access control
    │   └── SCHEMA.sql           ← Tables: roles, permissions, role_permissions, user_roles
    ├── groups/
    │   ├── MODULE.md            ← Dual mode (simple + topics), membership, pinning
    │   └── SCHEMA.sql           ← Tables: groups, topics
    ├── chat/
    │   ├── MODULE.md            ← Core messaging, business rules
    │   ├── API.md               ← Every REST endpoint with request/response JSON
    │   ├── WEBSOCKET.md         ← Every WebSocket event with payloads
    │   └── SCHEMA.sql           ← Tables: conversations, messages, message_reads, etc.
    ├── media/
    │   ├── MODULE.md            ← Upload flow, file limits, S3 storage
    │   └── SCHEMA.sql           ← Tables: media_files
    └── notifications/
        └── MODULE.md            ← FCM push flow, token lifecycle, batching
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Framework | FastAPI (async) |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) with Alembic migrations |
| Cache / Pub/Sub | Redis 7 |
| File Storage | MinIO (dev) / S3 (prod) |
| Task Queue | Celery + Redis |
| Push Notifications | Firebase Cloud Messaging (FCM) — only push service, not the database |
| Frontend | Flutter (separate repo) |
| Containerization | Docker + Docker Compose |

---

## Project Structure

```
DuttaMessenger/
│
├── CLAUDE.md                              ← This file
├── reference-docs/                        ← Architecture & design docs (read-only reference)
├── README.md
├── pyproject.toml                         ← Ruff, mypy, pytest config
├── alembic.ini
├── docker-compose.yml                     ← PostgreSQL, Redis, MinIO
├── Dockerfile
├── Makefile                               ← make test, make lint, make migrate, etc.
├── .env.example
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── main.py                            ← FastAPI app, middleware, router registration
│   ├── config.py                          ← Pydantic Settings (loads .env)
│   │
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── database.py                    ← Async engine, session factory, get_db dependency
│   │   ├── redis.py                       ← Redis connection
│   │   ├── storage.py                     ← S3/MinIO abstraction
│   │   ├── celery_app.py                  ← Celery instance
│   │   ├── exceptions.py                  ← AppException, NotFoundError, PermissionDeniedError, etc.
│   │   ├── responses.py                   ← success_response(), paginated_response(), error handler
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py                    ← JWT verification, get_current_user dependency
│   │   │   ├── acl.py                     ← require_permission() decorator
│   │   │   ├── rate_limiter.py
│   │   │   └── request_logger.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── pagination.py              ← Cursor encode/decode
│   │       ├── validators.py
│   │       └── datetime_utils.py
│   │
│   └── modules/
│       ├── auth/                          ← SEE MODULE ANATOMY BELOW
│       ├── users/
│       ├── chat/
│       ├── groups/
│       ├── acl/
│       ├── media/
│       └── notifications/
│
├── migrations/
│   ├── env.py                             ← Async Alembic config
│   ├── script.py.mako
│   └── versions/
│
├── tests/
│   ├── conftest.py                        ← Shared fixtures: db_session, client, auth_headers
│   ├── e2e/
│   └── results/                           ← Timestamped test run reports
│
└── scripts/
    ├── seed.py                            ← Seed institution, roles, permissions
    ├── run_tests.sh                       ← Run tests + save results
    └── generate_keys.py                   ← Generate RS256 JWT key pair
```

---

## Module Anatomy — EVERY Module Follows This

```
src/modules/{module_name}/
├── __init__.py                  ← Exports the router
├── router.py                    ← FastAPI APIRouter, includes all route files
├── docs/                        ← Module documentation (copied from reference-docs/ initially, then updated as module evolves)
│   ├── MODULE.md                ← What it does, dependencies, business rules
│   ├── API.md                   ← Endpoint specs (create as endpoints are built)
│   └── SCHEMA.sql               ← Table definitions
├── routes/
│   ├── __init__.py
│   └── {resource}.py            ← One file per resource
├── services/
│   ├── __init__.py
│   └── {resource}_service.py    ← Business logic (no HTTP concerns)
├── models/
│   ├── __init__.py
│   ├── db_models.py             ← SQLAlchemy ORM models
│   ├── request_models.py        ← Pydantic request schemas
│   └── response_models.py       ← Pydantic response schemas
└── tests/
    ├── __init__.py
    ├── test_{service}.py         ← Unit tests
    ├── test_{routes}.py          ← Integration tests
    └── factories.py              ← Factory-boy test data
```

**Rules:**
- Routes are THIN — validate (Pydantic), call service, return response. Max 15 lines.
- Services contain ALL business logic. Fully testable without HTTP.
- Models are in THREE files always: `db_models.py`, `request_models.py`, `response_models.py`.
- Tests mirror code: `foo_service.py` → `test_foo_service.py`.
- Docs live INSIDE the module. When building `chat`, everything is in `src/modules/chat/docs/`.
- When creating a new module, copy the relevant files from `reference-docs/modules/{name}/` into the module's `docs/` folder as the starting point.

---

## Module Build Order

Build in this order. A module can ONLY import from modules above it in this list.

```
1. shared/          ← Foundation (database, redis, middleware, utils)
2. auth             ← No module dependencies
3. users            ← Depends on: auth
4. acl              ← Depends on: auth, users
5. groups           ← Depends on: auth, users, acl
6. chat             ← Depends on: auth, users, acl, groups
7. media            ← Depends on: auth
8. notifications    ← Depends on: auth, users
```

**NEVER import downward.** Auth must never import from chat. If shared logic is needed, put it in `shared/`.

---

## Coding Standards — Non-Negotiable

We follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) with these specific rules:

### Type Hints — MANDATORY on every function

```python
# CORRECT
async def send_message(
    conversation_id: uuid.UUID,
    sender_id: uuid.UUID,
    content: str,
    reply_to_message_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    ...
```

### Docstrings — MANDATORY on every public function (Google style)

```python
async def send_message(...) -> MessageResponse:
    """Send a message to a conversation.

    Validates the sender's membership, persists the message, and publishes
    it to the real-time delivery pipeline.

    Args:
        conversation_id: The target conversation.
        sender_id: The authenticated user sending the message.
        content: Message text content (max 4096 characters).
        reply_to_message_id: If replying to a specific message, its ID.
        db: Database session (injected).

    Returns:
        The created message with server-assigned ID and timestamp.

    Raises:
        HTTPException(403): If sender is not a member of the conversation.
        HTTPException(404): If reply_to_message_id doesn't exist.
    """
```

### Structured Logging — ALWAYS structlog, NEVER f-strings

```python
import structlog
logger = structlog.get_logger()

# CORRECT
logger.info("message_sent", conversation_id=str(conv_id), sender_id=str(uid))

# WRONG
logger.info(f"Message sent by {uid} to {conv_id}")
```

### Error Responses — ALWAYS standard format

```python
raise HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error_code": "NOT_CONVERSATION_MEMBER",
        "message": "You are not a member of this conversation.",
        "conversation_id": str(conversation_id),
    },
)
```

### Naming

| Thing | Convention | Example |
|-------|-----------|---------|
| Files | `snake_case.py` | `message_service.py` |
| Classes | `PascalCase` | `MessageService` |
| Functions | `snake_case` | `send_message()` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_MESSAGE_LENGTH = 4096` |
| Private | `_leading_underscore` | `_validate_content()` |
| DB tables | `snake_case`, plural | `messages`, `group_members` |
| DB columns | `snake_case` | `created_at`, `sender_id` |
| API URLs | `kebab-case` | `/api/v1/group-members` |

### Absolute Prohibitions

- **No `import *`**
- **No raw SQL string formatting** — SQLAlchemy parameterized queries only
- **No `except Exception: pass`** — catch specific exceptions, log them
- **No hardcoded secrets** — use `src/config.py` which reads from .env
- **No `print()` statements** — use `structlog` logger
- **No commented-out code** in commits — Git has history
- **No TODO without tracking** — `# TODO(name): description — issue #N`
- **No offset pagination** — cursor-based only (see reference-docs/API_STANDARDS.md)
- **No auto-increment integer IDs** — UUID4 primary keys everywhere
- **No business logic in route handlers** — extract to service layer

---

## Database Rules

- **ORM**: SQLAlchemy 2.0 async
- **Migrations**: Alembic — every schema change is a migration
- **Primary keys**: UUID4 always
- **Timestamps**: Every table has `created_at` and `updated_at` (TIMESTAMPTZ)
- **Foreign keys**: Always. Referential integrity is non-negotiable.
- **Indexes**: Every index has a SQL comment explaining which query it supports
- **Soft deletes**: `deleted_at` column where needed (messages). Hard delete for users.
- **JSONB**: Only for semi-structured metadata, never for queried/FK data

---

## API Design Rules

- All endpoints under `/api/v1/`
- Resources are plural nouns, kebab-case: `/api/v1/group-members`
- IDs are UUIDs
- Cursor-based pagination only

### Response Format

```json
// Single resource
{ "data": { ... } }

// List
{ "data": [ ... ], "pagination": { "has_more": true, "next_cursor": "...", "limit": 50 } }

// Error
{ "error": { "code": "ERROR_CODE", "message": "Human readable", "details": { ... } } }
```

---

## Testing Rules

- **Framework**: pytest + pytest-asyncio + httpx + factory-boy
- **Every function gets tests. No exceptions.**
- **Test naming**: `test_{what}_{scenario}_{expected}`
- **Test file mirrors source**: `message_service.py` → `test_message_service.py`

### Checklist for Every API Endpoint

- [ ] Happy path -> correct response
- [ ] No auth token -> 401
- [ ] Wrong role -> 403
- [ ] Bad input -> 400/422
- [ ] Resource not found -> 404
- [ ] Duplicate request -> idempotent result
- [ ] Edge cases: empty strings, max length, Unicode

### Coverage Thresholds (CI blocks merge below these)

| Module | Minimum |
|--------|---------|
| auth | 90% |
| chat | 85% |
| groups | 85% |
| acl | 90% |
| media | 80% |
| notifications | 80% |
| shared | 85% |

---

## Git Conventions

### Branches
```
feature/MOD-{module}-{description}
bugfix/MOD-{module}-{description}
hotfix/{description}
```

### Commits (Conventional Commits)
```
feat(chat): add reply-to-message support
fix(auth): handle expired refresh token edge case
test(groups): add topic permission tests
docs(acl): document role hierarchy
```
