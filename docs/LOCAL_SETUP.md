# Local Setup Guide

**Audience:** anyone new to the repo — backend devs, Flutter devs who want a live server to test against, QA, ops.

**What you'll have at the end:** a running API on `http://localhost:8000`, a dev database with all tables, a test database, and the ability to run the test suite.

**Time:** ~10 minutes on a clean Mac. ~15 minutes on Linux/Windows (WSL recommended).

> Every command below has been run and verified on macOS (Homebrew Postgres 17 + Python 3.13). Commands that tripped on real bugs are documented in §6 "Troubleshooting — things that will go wrong".

---

## 1. Prerequisites

| Tool | Version | How to check | How to install (macOS) |
|------|---------|--------------|-----------------------|
| Python | **3.12+** | `python3 --version` | `brew install python@3.13` |
| Postgres | **14+** | `psql --version` | `brew install postgresql@17 && brew services start postgresql@17` |
| Redis | 7+ (optional for auth-only) | `redis-cli --version` | `brew install redis && brew services start redis` |
| Git | any recent | `git --version` | preinstalled or `brew install git` |

**System Python on macOS is 3.9 — too old.** Use `/opt/homebrew/bin/python3.13` or `python3.12` explicitly. Do not assume `python3` points at a recent version.

Docker is **not** required if you already run Postgres locally. If you prefer Docker, see §7.

---

## 2. Clone and enter the repo

```bash
git clone https://github.com/shreyasananth-3/dutta-messenger-.git
cd dutta-messenger-
```

---

## 3. Create the databases

We need **two**: one for the app, one for the test suite.

```bash
psql -h localhost -U "$USER" -d postgres -c "CREATE DATABASE dutta_messenger;"
psql -h localhost -U "$USER" -d postgres -c "CREATE DATABASE dutta_messenger_test;"
```

Verify:
```bash
psql -h localhost -U "$USER" -l | grep dutta
# should show both:
#   dutta_messenger
#   dutta_messenger_test
```

> **If you use the docker-compose Postgres instead** (user `messenger` / password `messenger_pass`), run the same two `CREATE DATABASE` statements inside the container and skip the `.env` changes in §5 — the defaults in `.env.example` already match.

---

## 4. Create a Python virtualenv

**Important:** use Python 3.12 or newer, not system Python.

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python --version    # → Python 3.13.x
```

Install runtime + dev + test dependencies:
```bash
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev,test]"
```

This takes 1–3 minutes. It installs FastAPI, SQLAlchemy, Alembic, Redis client, OpenTelemetry, Prometheus, Sentry, pytest, ruff, mypy, factory-boy, and everything else.

---

## 5. Create the `.env` file

The repo ships `.env.example` pointing at a docker-compose Postgres. For Homebrew Postgres, create a local `.env`:

```bash
cat > .env <<'EOF'
DEBUG=true
ENVIRONMENT=development
LOG_LEVEL=debug

DATABASE_URL=postgresql+asyncpg://YOUR_MAC_USER@localhost:5432/dutta_messenger
TEST_DATABASE_URL=postgresql+asyncpg://YOUR_MAC_USER@localhost:5432/dutta_messenger_test

REDIS_URL=redis://localhost:6379/0
SECRET_KEY=dev-only-secret-do-not-use-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# All modules default OFF. Flip individually while developing.
ENABLE_USERS=false
ENABLE_ACL=false
ENABLE_GROUPS=false
ENABLE_CHAT=false
ENABLE_MEDIA=false
ENABLE_NOTIFICATIONS=false
EOF
```

Replace `YOUR_MAC_USER` with your shell user (`echo $USER`).

`.env` is **gitignored** — never commit it.

---

## 6. Apply the database schema

```bash
.venv/bin/alembic upgrade head
```

Expected output (last two lines):
```
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_baseline, baseline schema (wraps 001_init_schema.sql)
```

Verify — you should see 21 rows:
```bash
psql -h localhost -U "$USER" -d dutta_messenger -c "\dt" | tail -25
```

Apply to the **test DB** too (pytest will use it):
```bash
DATABASE_URL="postgresql+asyncpg://$USER@localhost:5432/dutta_messenger_test" \
  .venv/bin/alembic upgrade head
```

### Prove the migration is reversible

Always test downgrade before trusting a new migration:
```bash
# Tear down
DATABASE_URL="postgresql+asyncpg://$USER@localhost:5432/dutta_messenger_test" \
  .venv/bin/alembic downgrade base

# Count should be 1 (just alembic_version):
psql -h localhost -U "$USER" -d dutta_messenger_test \
  -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"

# Bring it back
DATABASE_URL="postgresql+asyncpg://$USER@localhost:5432/dutta_messenger_test" \
  .venv/bin/alembic upgrade head
# Count should be 21 again.
```

---

## 7. Run the API server

```bash
.venv/bin/uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

Expected log line at the end of startup:
```
fastapi_app_created api_version=/api/v1 enabled_modules=[]
```

Open in a browser:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health
- Prometheus metrics: http://localhost:8000/metrics

Quick curl smoke test:
```bash
curl -s http://localhost:8000/health
# → {"status":"healthy"}
```

To turn modules on (e.g. when Stage 4a lands):
```bash
ENABLE_USERS=true .venv/bin/uvicorn src.main:app --reload
```

---

## 8. Run the tests

```bash
make test
```

`make test` runs `./scripts/run_tests.sh` which produces a timestamped proof folder:
```
tests/results/2026-04-18_021530/
  junit.xml
  coverage.xml
  coverage.json
  coverage-html/index.html
  pytest-output.txt
  summary.md
```

Quick smoke:
```bash
.venv/bin/pytest tests/test_harness_smoke.py -v --no-cov
# → 2 passed
```

---

## 9. Docker Compose option (if you prefer containers)

```bash
# In one terminal:
docker compose up -d

# Then:
.venv/bin/alembic upgrade head     # uses defaults from .env.example
.venv/bin/uvicorn src.main:app --reload
```

`docker compose up -d` starts Postgres 16, Redis 7, and MinIO. Data lives in named volumes (`postgres_data`, `redis_data`, `minio_data`); `docker compose down -v` wipes them.

The docker-compose Postgres uses username `messenger` / password `messenger_pass` and database `dutta_messenger` — these match the defaults in `.env.example`, so you can delete or leave the local `.env` and it just works.

---

## 10. Troubleshooting — things that will go wrong (they did for us)

### `ImportError: cannot import name 'HTTPAuthCredentials'`
**Cause:** FastAPI symbol is `HTTPAuthorizationCredentials`, not `HTTPAuthCredentials`. Already fixed in `main`. If you see this on a branch, grep the whole tree: `git grep -n HTTPAuthCredentials`.

### `NameError: name 'Any' is not defined`
**Cause:** `from typing import Any` missing at top of file. Happens most often in middleware and Redis helpers. Fix: add the import.

### `sqlalchemy.exc.ArgumentError: 'SchemaItem' object ... got <class 'int'>`
**Cause:** someone wrote `Column(int, ...)` instead of `Column(Integer, ...)`. Python's built-in `int` is not a SQLAlchemy type. Use `from sqlalchemy import Integer`.

### `pydantic_core.ValidationError: Extra inputs are not permitted — TEST_DATABASE_URL`
**Cause:** `pydantic-settings` defaults to strict mode. Either declare every field in `Settings` or set `Config.extra = "ignore"` (we did the latter). Already fixed in `main`.

### `ImportError: cannot import name 'db_models' from 'src.modules.auth'`
**Cause:** `from src.modules.auth import db_models` is wrong. Correct path: `from src.modules.auth.models import db_models`. Already fixed in `migrations/env.py`.

### `asyncpg.exceptions.PostgresSyntaxError: cannot insert multiple commands into a prepared statement`
**Cause:** asyncpg cannot execute a multi-statement SQL blob as a single prepared statement. The baseline migration splits statements for this reason (see `migrations/versions/0001_baseline_schema.py`). If you hit this in a new migration, split the SQL into individual `op.execute(...)` calls.

### `ValueError: the greenlet library is required to use this function`
**Cause:** `greenlet` isn't transitively installed in some Python 3.13 setups. Fix: `.venv/bin/pip install greenlet`. Added to runtime deps going forward.

### `Python 3.9` but pyproject needs 3.12+
**Cause:** you used system Python. Fix: always use `/opt/homebrew/bin/python3.13 -m venv .venv` (or `python3.12`), not `python3 -m venv`.

### Postgres role does not exist
**Cause:** you tried `psql -U messenger` but are on Homebrew Postgres where the only role is your Mac username. Either create the role (`CREATE ROLE messenger LOGIN PASSWORD 'messenger_pass' SUPERUSER;`) or use your Mac username in `DATABASE_URL`.

### `connection refused` to Postgres
**Cause:** Postgres not running. Fix: `brew services start postgresql@17` (Homebrew) or `docker compose up -d postgres` (Docker).

### `403 Forbidden` / tests fail with `TenantScopeViolation`
**Cause:** correct — a service call tried to read cross-tenant data. Rewrite the test so the user's `institution_id` matches the resource's.

---

## 11. Stopping / cleaning up

```bash
# Stop the server: Ctrl-C in the uvicorn terminal.
# Stop background services:
brew services stop postgresql@17   # Homebrew path
docker compose down                # Docker path (keeps data)
docker compose down -v             # Docker path (WIPES data)

# Wipe the local DB and start fresh (Homebrew):
psql -h localhost -U "$USER" -d postgres -c "DROP DATABASE dutta_messenger;"
psql -h localhost -U "$USER" -d postgres -c "DROP DATABASE dutta_messenger_test;"
```

---

## 12. What's running vs what's just code

| Area | Code lives | Running needed | Started by |
|------|-----------|----------------|------------|
| FastAPI app | `src/main.py` | yes | `uvicorn src.main:app` |
| Postgres | external (brew or docker) | yes | `brew services` / `docker compose` |
| Redis | external | optional for auth-only | `brew services` / `docker compose` |
| Celery workers | `src/shared/celery_app.py` | not yet (Stage 4f) | — |
| MinIO (file storage) | external | not yet (Stage 4e) | `docker compose` |
| Prometheus scraping | external | optional | your `prometheus.yml` pointing at `/metrics` |
| OTel collector (Jaeger etc.) | external | optional | set `OTEL_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT` |

---

## 13. Where to go next

- **Backend dev:** read `docs/NEXT_SESSION.md` and `reference-docs/modules/{name}/MODULE.md` for the module you're building.
- **Flutter dev:** read `docs/ui-contract/README.md` and `docs/ui-contract/auth.md`. Copy `docs/ui-contract/CLAUDE_FLUTTER.md` into your Flutter repo.
- **QA / tester:** import `docs/ui-contract/postman_collection.json` into Postman/Insomnia. All auth endpoints are clickable.
