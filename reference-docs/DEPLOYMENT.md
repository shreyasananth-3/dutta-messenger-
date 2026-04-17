# Deployment & Infrastructure

> **How to run locally, how CI/CD works, and how to deploy to production.**

---

## Local Development Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.12+
- Node.js 20+ (for any tooling scripts)

### One-Command Start

```bash
# Clone the repo
git clone git@github.com:infinitybox/messenger.git
cd messenger

# Copy environment template
cp .env.example .env

# Start everything
docker compose up -d

# Run database migrations
docker compose exec api alembic upgrade head

# Seed default data (institution, super_admin, roles, permissions)
docker compose exec api python scripts/seed.py

# API is now running at http://localhost:8000
# API docs at http://localhost:8000/docs (Swagger UI)
# MinIO console at http://localhost:9001
# Redis at localhost:6379
# PostgreSQL at localhost:5432
```

### Docker Compose Services

```yaml
services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [postgres, redis, minio]
    environment:
      - DATABASE_URL=postgresql+asyncpg://messenger:messenger@postgres:5432/messenger
      - REDIS_URL=redis://redis:6379/0
      - S3_ENDPOINT_URL=http://minio:9000

  postgres:
    image: postgres:16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: messenger
      POSTGRES_USER: messenger
      POSTGRES_PASSWORD: messenger
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  minio:
    image: minio/minio:latest
    ports: ["9000:9000", "9001:9001"]
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin

  celery_worker:
    build: ./backend
    command: celery -A shared.celery_app worker --loglevel=info
    depends_on: [postgres, redis]

volumes:
  postgres_data:
```

---

## CI/CD Pipeline (GitHub Actions)

```
On Pull Request:
  1. Lint (ruff check, ruff format --check, mypy)
  2. Unit tests (pytest -m "not integration")
  3. Integration tests (pytest -m integration — spins up test DB via testcontainers)
  4. Coverage check (fail if below thresholds)
  5. Build Docker image (verify it builds)

On Merge to main:
  1. All of the above
  2. Build and push Docker image to container registry
  3. Deploy to staging environment
  4. Run E2E tests against staging
  5. Manual approval gate for production

On Release Tag (v*):
  1. Deploy to production
  2. Run smoke tests
  3. Notify team
```

---

## Environment Configuration

### Required Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/testdb

# Redis
REDIS_URL=redis://host:6379/0

# JWT
JWT_PRIVATE_KEY=<RS256 private key, PEM format>
JWT_PUBLIC_KEY=<RS256 public key, PEM format>
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# S3 / MinIO
S3_ENDPOINT_URL=https://s3.amazonaws.com (or http://minio:9000 for local)
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET_NAME=infinitybox-messenger-media
S3_REGION=ap-south-1

# FCM (Push Notifications)
FCM_CREDENTIALS_PATH=/secrets/firebase-credentials.json

# App
APP_ENV=development|staging|production
LOG_LEVEL=DEBUG|INFO|WARNING|ERROR
CORS_ORIGINS=["http://localhost:3000"]
```

---

## Production Checklist

Before going to production, verify:

- [ ] All environment variables set (no `.env.example` values)
- [ ] JWT keys are unique, not the dev defaults
- [ ] Database has connection pooling configured
- [ ] S3 bucket has proper access policies
- [ ] TLS configured on Nginx
- [ ] Rate limiting enabled
- [ ] Structured logging shipping to log aggregator
- [ ] Database backups configured (daily, 30-day retention)
- [ ] Redis persistence configured (AOF or RDB)
- [ ] Health check endpoints responding (`/health`, `/ready`)
- [ ] Monitoring alerts set up for: API errors > 1%, response time > 2s, disk > 80%
