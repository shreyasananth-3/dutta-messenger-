# Testing Strategy

> **Every feature ships with tests. No exceptions.** Tests are not optional. They are part of the definition of done.

---

## Testing Pyramid

```
          ┌──────────┐
          │   E2E    │  ← Few (critical paths only)
          │  Tests   │
         ─┼──────────┼─
         │ Integration │  ← Moderate (API endpoints, DB queries)
         │   Tests     │
        ─┼─────────────┼─
        │   Unit Tests   │  ← Many (services, validators, utils)
        └────────────────┘
```

| Level | What It Tests | Database? | Speed |
|-------|--------------|-----------|-------|
| **Unit** | Pure functions, business logic, validators, utilities | No (mocked) | < 1 second each |
| **Integration** | API endpoints, database queries, service layer with real DB | Yes (test DB) | < 5 seconds each |
| **E2E** | Full user flows (register → login → send message → receive) | Yes (test DB) | < 30 seconds each |

---

## Tools

| Tool | Purpose |
|------|---------|
| **pytest** | Test runner |
| **pytest-asyncio** | Async test support |
| **httpx** | Async HTTP client for API tests |
| **factory-boy** | Test data factories |
| **pytest-cov** | Coverage reporting |
| **testcontainers** | Spin up PostgreSQL + Redis in Docker for integration tests |

---

## Directory Structure

```
backend/
├── modules/
│   └── chat/
│       └── tests/
│           ├── __init__.py
│           ├── test_message_service.py          ← Unit tests
│           ├── test_message_routes.py           ← Integration tests (API)
│           ├── test_message_models.py           ← Model validation tests
│           └── factories.py                     ← Test data factories
│
├── tests/
│   ├── conftest.py                              ← Shared fixtures (db, client, auth)
│   ├── e2e/
│   │   ├── test_messaging_flow.py               ← Full send/receive flow
│   │   └── test_group_management_flow.py
│   ├── fixtures/
│   │   └── sample_files/                        ← Test images, PDFs, etc.
│   └── results/                                 ← Test run output (see below)
│       ├── 2025-01-15T10-30-00_results.json
│       └── 2025-01-15T10-30-00_summary.md
```

---

## How to Write Tests

### Unit Test Example

```python
"""Tests for message content validation."""

import pytest
from modules.chat.services.message_service import validate_message_content


class TestValidateMessageContent:
    """Tests for the validate_message_content function."""

    def test_valid_message(self):
        """A normal text message passes validation."""
        result = validate_message_content("Hello, world!")
        assert result.is_valid is True

    def test_empty_message_rejected(self):
        """An empty string is rejected."""
        result = validate_message_content("")
        assert result.is_valid is False
        assert result.error_code == "CONTENT_EMPTY"

    def test_whitespace_only_rejected(self):
        """A whitespace-only message is rejected."""
        result = validate_message_content("   \n\t  ")
        assert result.is_valid is False
        assert result.error_code == "CONTENT_EMPTY"

    def test_max_length_exceeded(self):
        """A message exceeding 4096 characters is rejected."""
        long_content = "a" * 4097
        result = validate_message_content(long_content)
        assert result.is_valid is False
        assert result.error_code == "CONTENT_TOO_LONG"

    def test_max_length_boundary(self):
        """A message of exactly 4096 characters is accepted."""
        content = "a" * 4096
        result = validate_message_content(content)
        assert result.is_valid is True
```

### Integration Test Example (API Endpoint)

```python
"""Integration tests for the message sending endpoint."""

import pytest
from httpx import AsyncClient
from uuid import uuid4


@pytest.mark.asyncio
class TestSendMessageEndpoint:
    """Tests for POST /api/v1/chat/conversations/{id}/messages."""

    async def test_send_message_success(
        self, client: AsyncClient, auth_headers: dict, conversation_factory
    ):
        """A member can send a message to their conversation."""
        conversation = await conversation_factory.create()

        response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            headers=auth_headers,
            json={
                "content": "Hello!",
                "client_message_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["content"] == "Hello!"
        assert data["conversation_id"] == str(conversation.id)

    async def test_send_message_not_member(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A non-member cannot send a message to a conversation."""
        other_conversation_id = uuid4()

        response = await client.post(
            f"/api/v1/chat/conversations/{other_conversation_id}/messages",
            headers=auth_headers,
            json={
                "content": "Intruder!",
                "client_message_id": str(uuid4()),
            },
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NOT_CONVERSATION_MEMBER"

    async def test_send_message_duplicate_idempotent(
        self, client: AsyncClient, auth_headers: dict, conversation_factory
    ):
        """Sending the same client_message_id twice returns the original, not a duplicate."""
        conversation = await conversation_factory.create()
        client_id = str(uuid4())

        response1 = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            headers=auth_headers,
            json={"content": "Hello!", "client_message_id": client_id},
        )
        response2 = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            headers=auth_headers,
            json={"content": "Hello!", "client_message_id": client_id},
        )

        assert response1.status_code == 201
        assert response2.status_code == 201
        assert response1.json()["data"]["id"] == response2.json()["data"]["id"]

    async def test_reply_to_nonexistent_message(
        self, client: AsyncClient, auth_headers: dict, conversation_factory
    ):
        """Replying to a message that doesn't exist returns 404."""
        conversation = await conversation_factory.create()

        response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            headers=auth_headers,
            json={
                "content": "Reply!",
                "reply_to_message_id": str(uuid4()),
                "client_message_id": str(uuid4()),
            },
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MESSAGE_NOT_FOUND"
```

### Shared Fixtures (`conftest.py`)

```python
"""Shared test fixtures for all test modules."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from main import app
from shared.database import get_db
from shared.config import settings


@pytest_asyncio.fixture
async def db_session():
    """Create a fresh database session for each test, rolled back after."""
    engine = create_async_engine(settings.test_database_url)
    async with engine.begin() as conn:
        session = AsyncSession(bind=conn)
        yield session
        await conn.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """Async HTTP client pointed at the test app."""
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, user_factory):
    """Auth headers for a test user."""
    user = await user_factory.create()
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "testpassword123"},
    )
    token = login_response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
```

---

## Test Naming Convention

```
test_{what_is_being_tested}_{scenario}_{expected_outcome}
```

Examples:
```
test_send_message_valid_content_returns_201
test_send_message_empty_content_returns_422
test_create_group_non_admin_returns_403
test_upload_file_exceeds_size_limit_returns_413
```

---

## Running Tests

```bash
# Run all tests
pytest

# Run tests for a specific module
pytest backend/modules/chat/tests/

# Run with coverage
pytest --cov=backend --cov-report=html --cov-report=json

# Run and save results (see next section)
./scripts/run_tests.sh
```

---

## Storing Test Results

Every test run saves its results to `backend/tests/results/`. This creates an audit trail.

### `scripts/run_tests.sh`

```bash
#!/bin/bash
TIMESTAMP=$(date +%Y-%m-%dT%H-%M-%S)
RESULTS_DIR="backend/tests/results"

mkdir -p "$RESULTS_DIR"

# Run tests, output JSON report
pytest \
  --tb=short \
  --json-report \
  --json-report-file="${RESULTS_DIR}/${TIMESTAMP}_results.json" \
  --cov=backend \
  --cov-report=json:"${RESULTS_DIR}/${TIMESTAMP}_coverage.json" \
  2>&1 | tee "${RESULTS_DIR}/${TIMESTAMP}_output.log"

# Generate human-readable summary
python scripts/summarize_test_results.py \
  "${RESULTS_DIR}/${TIMESTAMP}_results.json" \
  "${RESULTS_DIR}/${TIMESTAMP}_coverage.json" \
  > "${RESULTS_DIR}/${TIMESTAMP}_summary.md"

echo "Results saved to ${RESULTS_DIR}/${TIMESTAMP}_summary.md"
```

### Summary File Format

Each run produces a markdown summary like:

```markdown
# Test Run Summary — 2025-01-15T10:30:00

## Results
- **Total**: 142
- **Passed**: 139
- **Failed**: 2
- **Skipped**: 1
- **Duration**: 45.2 seconds

## Failed Tests
1. `test_send_message_offline_user_gets_push` — AssertionError: Expected FCM call
2. `test_upload_large_video_returns_413` — Timeout after 30s

## Coverage
- **Overall**: 87.3%
- **auth module**: 94.1%
- **chat module**: 82.6%  ← Below 85% threshold
- **groups module**: 91.0%
- **acl module**: 88.4%

## Action Items
- [ ] Fix push notification test (mock FCM client)
- [ ] Investigate video upload timeout
- [ ] Increase chat module coverage to 85%+
```

---

## Coverage Requirements

| Module | Minimum Coverage |
|--------|-----------------|
| `auth` | 90% |
| `chat` | 85% |
| `groups` | 85% |
| `acl` | 90% |
| `media` | 80% |
| `notifications` | 80% |
| `shared/` | 85% |

**CI/CD blocks merge if coverage drops below these thresholds.**

---

## What to Test (Checklist)

For every API endpoint, test:

- [ ] Happy path (valid request → correct response)
- [ ] Authentication required (no token → 401)
- [ ] Authorization check (wrong role → 403)
- [ ] Validation failure (bad input → 400/422)
- [ ] Resource not found (bad ID → 404)
- [ ] Idempotency (duplicate request → same result)
- [ ] Edge cases (empty strings, max length, special characters, Unicode)
- [ ] Pagination (first page, middle page, last page, empty list)

For every service function, test:

- [ ] Valid input → correct output
- [ ] Invalid input → correct error
- [ ] Boundary conditions (0, 1, max, max+1)
- [ ] Null/None handling
