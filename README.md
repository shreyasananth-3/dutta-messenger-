# DuttaMessenger Backend

A private institutional messaging platform with Flutter frontend. Built with FastAPI, PostgreSQL, Redis, and MinIO.

## Quick Start

### Prerequisites
- Python 3.12+
- PostgreSQL 16
- Redis 7
- MinIO (for file storage)

### Setup

1. **Clone and install dependencies:**
```bash
cd /Users/guru/Desktop/Work/Radlabs/DuttaMessenger
pip install -e .
```

2. **Setup environment:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Start services (Docker):**
```bash
docker-compose up -d
```

4. **Run database migrations:**
```bash
psql -U messenger -d dutta_messenger < migrations/001_init_schema.sql
```

5. **Start the server:**
```bash
python -m uvicorn src.main:app --reload
```

Server runs on `http://localhost:8000`

---

## Architecture Overview

### Project Structure
```
DuttaMessenger/
├── src/
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Configuration (Pydantic)
│   ├── shared/              # Shared utilities & infrastructure
│   │   ├── database.py      # SQLAlchemy async setup
│   │   ├── redis.py         # Redis client
│   │   ├── exceptions.py    # Custom exceptions
│   │   ├── responses.py     # Response formatting
│   │   ├── middleware/      # Auth, ACL, logging
│   │   └── utils/           # Validators, pagination, datetime
│   └── modules/             # Feature modules
│       ├── auth/            # Authentication (✅ Complete)
│       ├── users/           # User profiles
│       ├── acl/             # Roles & permissions
│       ├── groups/          # Groups & topics
│       ├── chat/            # Messaging
│       ├── media/           # File uploads
│       └── notifications/   # Push notifications
├── migrations/              # Database migrations
├── tests/                   # Test suite
└── docs/                    # Documentation
```

### Module Build Order
1. **shared** - Foundation (database, redis, middleware)
2. **auth** - Authentication & JWT ✅
3. **users** - User profiles
4. **acl** - Roles & permissions
5. **groups** - Groups & topics
6. **chat** - Core messaging
7. **media** - File uploads
8. **notifications** - Push notifications

---

## API Endpoints (Auth Module)

### Authentication
- `POST /api/v1/auth/register` - Register via invitation
- `POST /api/v1/auth/login` - Login with email/password
- `POST /api/v1/auth/refresh` - Refresh access token
- `POST /api/v1/auth/change-password` - Change password

### Institution Management
- `POST /api/v1/institutions` - Create institution
- `POST /api/v1/auth/invite` - Invite user to institution

---

## Request/Response Format

### Success Response
```json
{
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "John Doe",
    ...
  }
}
```

### Paginated Response
```json
{
  "data": [...],
  "pagination": {
    "has_more": true,
    "next_cursor": "base64_encoded_cursor",
    "limit": 50
  }
}
```

### Error Response
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {...}
  }
}
```

---

## Database

### Schema
Complete schema is in `migrations/001_init_schema.sql` with:
- Auth tables (institutions, users, invitations)
- ACL tables (roles, permissions, user_roles)
- Group tables (groups, topics, members)
- Chat tables (conversations, messages, reads)
- Media tables (files)
- Notification tables (FCM tokens, notifications)

### Key Patterns
- **Primary Keys**: UUID4
- **Timestamps**: TIMESTAMPTZ (UTC)
- **Pagination**: Cursor-based, no offset
- **Soft Deletes**: messages use deleted_at, users use hard delete
- **Indexes**: All FKs and search fields indexed

---

## Code Standards

### Type Hints (Mandatory)
```python
async def send_message(
    conversation_id: uuid.UUID,
    sender_id: uuid.UUID,
    content: str,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    ...
```

### Docstrings (Google Style)
```python
async def send_message(...) -> MessageResponse:
    """Send a message to a conversation.

    Args:
        conversation_id: The target conversation.
        sender_id: The authenticated user sending the message.

    Returns:
        The created message with server-assigned ID and timestamp.

    Raises:
        HTTPException(403): If sender is not a member.
    """
```

### Structured Logging
```python
import structlog
logger = structlog.get_logger()

logger.info("message_sent", conversation_id=str(conv_id), sender_id=str(uid))
```

### Error Handling
```python
from src.shared.exceptions import NotFoundError, PermissionDeniedError

raise NotFoundError("Conversation", str(conversation_id))
raise PermissionDeniedError("You must be a group member")
```

---

## Testing

Every module has tests covering:
- ✅ Happy path
- ✅ Authentication (401)
- ✅ Authorization (403)
- ✅ Validation errors (422)
- ✅ Not found (404)
- ✅ Conflicts (409)
- ✅ Edge cases

Run tests:
```bash
pytest                          # All tests
pytest tests/test_auth.py       # Single module
pytest --cov=src               # With coverage
```

---

## Configuration

Environment variables in `.env`:

```
# FastAPI
DEBUG=true
ENVIRONMENT=development

# Database
DATABASE_URL=postgresql+asyncpg://messenger:pass@localhost:5432/dutta_messenger

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT
SECRET_KEY=your-secret-key-change-in-prod
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# File Storage
STORAGE_TYPE=minio
MINIO_URL=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# Firebase
FCM_PROJECT_ID=your-project-id
FCM_PRIVATE_KEY=your-private-key
```

---

## Development Workflow

1. **Create a branch:**
   ```bash
   git checkout -b feature/MOD-{module}-{description}
   ```

2. **Build module following the pattern** in BUILD_PROGRESS.md

3. **Write tests** for every function

4. **Run linting:**
   ```bash
   ruff check src/
   mypy src/
   ```

5. **Run tests:**
   ```bash
   pytest --cov=src.modules.{module}
   ```

6. **Commit with conventional commits:**
   ```bash
   git commit -m "feat(module): description"
   ```

---

## Key Files for Reference

- **CLAUDE.md** - Project intelligence, standards, architecture
- **BUILD_PROGRESS.md** - Build status and next steps
- **migrations/001_init_schema.sql** - Complete database schema
- **src/shared/** - Foundation (database, redis, exceptions)
- **src/modules/auth/** - Complete working example

---

## Common Issues

### Database Connection
```bash
# Check PostgreSQL is running
docker-compose ps

# Verify connection string in .env
DATABASE_URL=postgresql+asyncpg://messenger:pass@localhost:5432/dutta_messenger
```

### Redis Connection
```bash
# Check Redis is running
docker-compose ps

# Test connection
redis-cli ping
```

### Port Already in Use
```bash
# Change FastAPI port
python -m uvicorn src.main:app --port 8001
```

---

## Next Steps

See **BUILD_PROGRESS.md** for:
- ✅ Completed components
- 📋 Next modules to build
- 📐 File structure templates
- 🧪 Testing examples

Start building the **Users module** next!

---

## License

Private - Radlabs only

## Support

For issues and questions, refer to CLAUDE.md for project intelligence.
