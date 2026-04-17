# AI Prompt Guide for Development

> **This document tells you how to use AI assistants (Claude, etc.) when building features for this project.** The AI does not know our conventions unless you tell it. This guide ensures every AI-assisted coding session produces code that meets our standards.

---

## The Master Prompt

**Copy this prompt and paste it at the start of every AI conversation about this project.** Fill in the `[TASK]` section with your specific request.

```
You are helping me build a feature for DuttaMessenger, a private institutional
messaging platform.

TECH STACK:
- Backend: Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis 7
- Frontend: Flutter (separate team, not your concern unless asked)
- WebSocket: FastAPI WebSocket with Redis Pub/Sub
- File Storage: S3-compatible (MinIO in dev)
- Task Queue: Celery + Redis

CODING STANDARDS (follow these exactly):
- Google Python Style Guide
- Type hints on EVERY function (args and return)
- Docstrings on every public function (Google style: Args, Returns, Raises)
- Structured logging with structlog (never f-string logs)
- Pydantic models for ALL request/response bodies
- SQLAlchemy async sessions for all DB operations
- No raw SQL string formatting — parameterized queries only
- Error responses follow our standard format:
  {"error": {"code": "ERROR_CODE", "message": "Human readable", "details": {}}}
- UUIDs for all primary keys, never auto-increment integers
- Cursor-based pagination, never offset-based
- Every function must be testable — inject dependencies, don't use globals

PROJECT STRUCTURE:
- Each feature lives in backend/modules/{module_name}/
- Module contains: routes/ services/ models/ tests/
- Module has: MODULE.md (docs), API.md (endpoint docs), SCHEMA.sql (tables)
- Shared code in backend/shared/ (middleware, database, utils)

TESTING REQUIREMENTS:
- Write pytest tests for every function you create
- Test happy path, auth failure, validation failure, not-found, edge cases
- Use pytest-asyncio for async tests
- Use httpx AsyncClient for API endpoint tests
- Test names: test_{what}_{scenario}_{expected}

[TASK]:
{Describe your specific task here}
```

---

## Prompt Templates for Common Tasks

### Creating a New API Endpoint

```
[TASK]:
I need to create a new API endpoint in the {module_name} module.

Endpoint: {HTTP_METHOD} /api/v1/{path}
Purpose: {What it does}
Auth required: Yes
Permission required: {permission_codename or "any authenticated user"}

Request body:
{Describe the fields}

Response:
{Describe what should come back}

Business rules:
- {Rule 1}
- {Rule 2}

Please create:
1. The Pydantic request/response models (in models/)
2. The service function with business logic (in services/)
3. The route handler (in routes/)
4. Complete pytest tests covering happy path, auth, validation, not-found
5. The API.md documentation entry for this endpoint
```

### Creating a New Database Table

```
[TASK]:
I need to create a new database table for the {module_name} module.

Table name: {table_name}
Purpose: {What data it stores}

Columns:
- {column_name}: {type} — {description}
- {column_name}: {type} — {description}

Relationships:
- {foreign_key} → {referenced_table}

Queries this table needs to support:
- {Describe query 1 and when it runs}
- {Describe query 2 and when it runs}

Please create:
1. The SQLAlchemy model class
2. The Alembic migration
3. The necessary indexes (with comments explaining which query each supports)
4. The SCHEMA.sql documentation entry
```

### Adding a WebSocket Event

```
[TASK]:
I need to add a new WebSocket event to the chat module.

Event name: {event_type}
Direction: client → server | server → client | bidirectional
Purpose: {What triggers it, what it does}

Client sends:
{JSON structure}

Server responds/broadcasts:
{JSON structure}

Business rules:
- {Rule 1}
- {Rule 2}

Please create:
1. The event handler in the WebSocket manager
2. Pydantic models for the event payload
3. The WEBSOCKET.md documentation entry
4. Tests for the event handling logic
```

### Writing Tests for Existing Code

```
[TASK]:
I need comprehensive tests for the following existing code:

{Paste the code}

This code is in: backend/modules/{module}/services/{file}.py

Please write pytest tests covering:
- All happy paths
- Authentication/authorization failures
- Input validation failures
- Resource not found scenarios
- Edge cases (empty strings, max values, Unicode, concurrent access)
- Idempotency where applicable

Follow our test naming convention: test_{what}_{scenario}_{expected}
Use our standard fixtures (client, auth_headers, db_session).
```

---

## What to Include in Every Prompt

Always give the AI:

1. **Which module** you're working in
2. **What already exists** — paste the MODULE.md or relevant existing code
3. **Our standards** — the master prompt covers this, but emphasize specific rules if the AI starts drifting
4. **Business rules** — the AI can't guess these, you must spell them out
5. **What you want back** — be explicit (code, tests, docs, all three?)

---

## What to Review in AI Output

Before committing AI-generated code, verify:

| Check | How |
|-------|-----|
| Type hints on all functions | Visual scan |
| Docstrings present | Visual scan |
| Structured logging (no f-strings in logs) | Search for `logger.` |
| Pydantic models for request/response | Check route handlers |
| Parameterized SQL (no string formatting) | Search for `.format(` or `f"SELECT` |
| UUID primary keys | Check model classes |
| Error response format matches standard | Check HTTPException details |
| Tests exist and cover key scenarios | Run `pytest` |
| No hardcoded secrets or URLs | Search for `http://`, passwords, keys |
| No TODO comments without tracking | Search for `TODO` |

---

## Anti-Patterns to Watch For

The AI will sometimes produce code with these problems. Catch them:

| Anti-Pattern | What to Look For | Fix |
|-------------|-----------------|-----|
| Global database session | `db = Session()` at module level | Use `Depends(get_db)` |
| Sync code in async context | Missing `await`, using `requests` instead of `httpx` | Add `await`, use async libraries |
| Bare exception handling | `except Exception: pass` | Catch specific exceptions, log them |
| Business logic in route handler | 50+ lines of logic in the route function | Extract to service layer |
| Missing error handling | No try/except around DB or external calls | Add structured error handling |
| Offset pagination | `?page=2&per_page=20` | Use cursor-based pagination |
| Sequential integer IDs | `id: int = Column(Integer, primary_key=True)` | Use `id: UUID = Column(UUID, primary_key=True, default=uuid4)` |
| Inconsistent response format | Returning raw dicts instead of Pydantic models | Use response models |

---

## Module-Specific Prompt Context

When working on a specific module, include that module's `MODULE.md` in the prompt so the AI understands the full context. Example:

```
Here is the documentation for the module I'm working in:

---
{Paste contents of backend/modules/chat/MODULE.md}
---

[TASK]:
I need to add support for editing sent messages...
```

This prevents the AI from making assumptions that contradict existing architecture decisions.
