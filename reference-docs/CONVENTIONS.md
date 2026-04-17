# Coding Conventions

> **Every line of code in this project follows these rules.** No exceptions. If you are unsure, read this document again. If still unsure, ask before writing code.

---

## Python Conventions

We follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) with the additions below.

### Formatting & Linting

| Tool | Purpose | Config File |
|------|---------|-------------|
| **Ruff** | Linter + formatter (replaces black, isort, flake8) | `pyproject.toml` |
| **mypy** | Static type checking (strict mode) | `pyproject.toml` |

```bash
# Run before every commit
ruff check . --fix
ruff format .
mypy .
```

**Every function has type hints. No exceptions.**

```python
# ✅ CORRECT
async def get_user_by_id(user_id: uuid.UUID, db: AsyncSession) -> UserResponse:
    ...

# ❌ WRONG — no type hints
async def get_user_by_id(user_id, db):
    ...
```

### Naming

| Thing | Convention | Example |
|-------|-----------|---------|
| Files | `snake_case.py` | `message_service.py` |
| Classes | `PascalCase` | `MessageService` |
| Functions | `snake_case` | `send_message()` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_MESSAGE_LENGTH` |
| Private methods | `_leading_underscore` | `_validate_content()` |
| Database tables | `snake_case`, plural | `messages`, `group_members` |
| Database columns | `snake_case` | `created_at`, `sender_id` |
| API endpoints | `kebab-case` in URL | `/api/v1/group-members` |
| Environment vars | `UPPER_SNAKE_CASE` | `DATABASE_URL` |

### File Structure Within a Module

Every Python module file follows this order:

```python
"""Module docstring — one line describing what this file does."""

# 1. Standard library imports
import uuid
from datetime import datetime

# 2. Third-party imports
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

# 3. Local imports
from shared.database import get_db
from shared.middleware.auth import get_current_user
from modules.chat.models import Message
from modules.chat.services import MessageService

# 4. Constants
MAX_MESSAGE_LENGTH = 4096
ALLOWED_MEDIA_TYPES = {"image/png", "image/jpeg", "video/mp4"}

# 5. Module code (classes, functions)
```

### Docstrings

Every public function, class, and method has a docstring.

```python
async def send_message(
    conversation_id: uuid.UUID,
    sender_id: uuid.UUID,
    content: str,
    reply_to_message_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Send a message to a conversation.

    Validates the sender's membership in the conversation, persists the
    message, and publishes it to the real-time delivery pipeline.

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
        HTTPException(404): If reply_to_message_id does not exist in this conversation.
        HTTPException(422): If content exceeds MAX_MESSAGE_LENGTH.
    """
```

### Error Handling

**Never raise bare exceptions. Always use structured error responses.**

```python
# ✅ CORRECT — specific, informative
raise HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "error_code": "NOT_CONVERSATION_MEMBER",
        "message": "You are not a member of this conversation.",
        "conversation_id": str(conversation_id),
    },
)

# ❌ WRONG — vague
raise HTTPException(status_code=403, detail="Forbidden")
```

### Logging

Use structured logging. Every log line is JSON-parseable.

```python
import structlog

logger = structlog.get_logger()

# ✅ CORRECT — structured, contextual
logger.info(
    "message_sent",
    conversation_id=str(conversation_id),
    sender_id=str(sender_id),
    message_length=len(content),
)

# ❌ WRONG — unstructured string formatting
logger.info(f"Message sent by {sender_id} to {conversation_id}")
```

---

## Project-Wide Rules

### Git Conventions

**Branch naming:**
```
feature/MOD-{module}-{short-description}
bugfix/MOD-{module}-{short-description}
hotfix/{short-description}

# Examples:
feature/MOD-chat-reply-to-message
bugfix/MOD-auth-token-refresh-race
hotfix/fix-websocket-disconnect
```

**Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/):
```
feat(chat): add reply-to-message support
fix(auth): handle expired refresh token edge case
test(groups): add membership permission tests
docs(acl): document role hierarchy
refactor(media): extract upload validation to separate service
```

**Pull requests:**
- Every PR has a description explaining WHAT and WHY.
- Every PR references the module(s) it touches.
- Every PR must pass all tests before merge.
- Every PR requires at least one review.

### Environment Variables

Never hardcode secrets. Always use environment variables loaded via `pydantic-settings`.

```python
# shared/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    jwt_private_key: str
    jwt_public_key: str
    s3_bucket_name: str
    s3_endpoint_url: str
    fcm_credentials_path: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
```

### No Magic Numbers

```python
# ✅ CORRECT
MAX_MESSAGE_LENGTH = 4096
MAX_GROUP_MEMBERS = 500
MAX_FILE_SIZE_MB = 100
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

if len(content) > MAX_MESSAGE_LENGTH:
    raise ...

# ❌ WRONG
if len(content) > 4096:
    raise ...
```

### Import Rules

- **No wildcard imports** (`from module import *`)
- **No circular imports** — if two modules need each other, extract shared code to `shared/`
- **Explicit is better than implicit** — import the specific thing you need

### Comment Rules

- **Code comments explain WHY, not WHAT.** The code itself says what. The comment says why it's done this way.
- **TODO comments** must include the author and a tracking reference: `# TODO(shreyas): Implement pagination — tracked in issue #42`
- **No commented-out code in main branch.** Delete it. Git has history.
