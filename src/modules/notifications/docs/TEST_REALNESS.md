# Notifications — Test Realness Matrix

> What the current Stage 4f test suite really exercises, what it mocks,
> and what a "real-world" integration test would still need. Keep this
> up to date when tests change — especially when Stage 6 load/integration
> work lands.

Test layers sit on a continuum from **real** (executes the actual code
path against the actual dependency) through **mocked** (the dependency
is replaced by a test double) to **bypassed** (the path isn't exercised
at all). Each row below picks one.

## Legend

- ✅ **Real** — the test runs production code against the real dependency.
- 🧪 **Mocked** — the test runs production code against a controlled double.
- ⏭️ **Bypassed** — the production path isn't touched by tests; an
  alternate entry point (usually async-native) is tested instead.
- ⛔ **Not covered** — intentionally excluded from unit tests (see
  "pending" rows below for the integration plan).

## Current test coverage (as of 2026-04-18, PR #4)

| Layer / Component | Status | Notes |
|---|---|---|
| PostgreSQL (`dutta_messenger_test`) | ✅ Real | Homebrew Postgres 17; nested-transaction-rollback per test via `db_session`. Every INSERT / UPDATE / SELECT runs for real. |
| SQLAlchemy ORM, relationships, JSONB, UUID[] | ✅ Real | No ORM mocks; errors surface as real SQLAlchemy exceptions. |
| `tenant_scoped_query()`, `assert_same_institution()` | ✅ Real | `TestCrossTenantFuzz` asserts 404 (not 403) on cross-institution access. |
| `write_audit(...)` writes to `audit_logs` | ✅ Real | `TestRegisterToken::test_first_registration_persists_row_and_audit` reads the row back via raw SQL. |
| FastAPI routing + request/response | ✅ Real | Routes run through real middleware / dependency injection via `httpx.ASGITransport`. No network socket, but no shortcut either. |
| JWT encode/decode (`get_current_user`) | ✅ Real | Tests generate tokens via `create_access_token` and the route's `Depends(get_current_user)` verifies them. |
| Pydantic request/response validation | ✅ Real | Includes 422 tests, unicode round-trip, max-length boundaries. |
| Prometheus counter (`dutta_notifications_delivered_total`) | ✅ Real | `test_success_marks_batch_sent_and_increments_metric` reads `.labels(result="success")._value.get()` before and after. |
| structlog events | ✅ Real | Emitted; not asserted in tests (logs aren't the contract). |
| **FCM client** | 🧪 Mocked | `MockFcmClient` in `tests/modules/notifications/factories.py`. The prompt explicitly requires this — "do NOT call real FCM in CI." Real FCM needs service-account credentials, a real device token, network, and burns quota. |
| **Celery broker (Redis)** | ⏭️ Bypassed | `conftest.no_enqueue` monkey-patches `fanout_service._enqueue_batch` to record calls instead of calling `.delay()`. No Redis is contacted. |
| **`FanoutService._enqueue_batch`** | ⏭️ Bypassed | Replaced by the `no_enqueue` recorder. The one-line call to `send_push_batch.delay(...)` is never executed in tests. |
| **`send_push_batch` (Celery task wrapper)** | ⛔ Not covered | Marked `# pragma: no cover`. `asyncio.run(...)` cannot nest inside pytest-asyncio's running loop, so the sync wrapper can't be unit-tested from within the async test suite. |
| **`_run_batch(batch_id, institution_id)`** — production path that opens its own `SessionLocal` | ⛔ Not covered | Also `# pragma: no cover`. Would require the production DB engine + commit semantics. Tests instead drive `run_batch(db, batch_id=..., institution_id=...)`, which takes the test session and skips the commit/rollback dance. |
| **`FirebaseAdminClient`** (`tasks/_firebase_client.py`) | ⛔ Not covered | Pure `firebase-admin` SDK wrapper. Only imported when `FCM_MOCK_MODE=False`. Zero statements executed in CI. |
| Redis (anywhere) | ⏭️ Bypassed | This module doesn't use Redis directly. Idempotency middleware that would use Redis is a future RFC. |
| WebSocket delivery of notifications | ⛔ Not covered | Out of scope — online delivery belongs to the chat module. |
| Alembic migration applied via `alembic upgrade head` | 🧪 Weakly covered | Migration round-trip (up / down / up) was manually verified on the test DB during development. The test *suite* asserts behaviour, not schema shape, so a missing index would slow queries but not fail a test. The parallel `track/users` worker overwrote `alembic_version` to `0004_users_module_schema` after my round-trip — my indexes remain in place because partial-index DDL is additive, but `alembic_version` no longer names my revision until `alembic merge` lands. |

## Pending for a "real-world" integration test

Each item below is a test we *don't* have today and the dependency that
makes it awkward to add in a unit-test run. Group is the stage where it
fits cleanly.

| Pending test | What's missing | Where it fits |
|---|---|---|
| `send_push_batch` executed via Celery `.apply()` in eager mode | Celery's eager mode still wraps the body in `asyncio.run(...)`, which conflicts with pytest-asyncio's running loop. Workaround: run the one eager-mode test in a subprocess, or use `nest_asyncio` / a dedicated event-loop-per-test test file. | Stage 4f follow-up (low-risk, ~1 file). |
| Celery worker + Redis broker end-to-end | Needs `docker compose up -d redis` and a worker process. The test has to enqueue a batch, poll for completion, and assert the batch's `status` + audit row + metric increment. | Stage 6 integration suite. |
| Real FCM send | Service-account credentials, a real device, a network-egress rule, and a quota budget. Only ever runnable in a manual pre-release smoke — never in CI. | Stage 6 manual smoke checklist. |
| Schema-assertion fixture | A `pytest` fixture that runs `SELECT 1 FROM pg_indexes WHERE indexname = 'idx_fcm_tokens_user_active'` and fails if the index is missing. Catches the "migration skipped" regression the current suite would miss. | Stage 4f follow-up (≤ 10 lines). |
| `_run_batch` with production `SessionLocal` | Needs the production DB engine (or an injected factory) so the short-lived session's commit/rollback is exercised. Viable with a subprocess or a second test engine that commits instead of rolling back. | Stage 6. |
| Rate-limit behaviour on token register under burst | The global `RATE_LIMIT_DEFAULT` applies, but no test drives ≥ N requests to prove the 429 path. | Stage 4f follow-up (cheap). |
| Cross-worker token re-binding under concurrency | Two async tasks register the same FCM string for different users simultaneously. The `UNIQUE(token)` constraint + our revoke-then-insert flow must serialise cleanly. | Stage 6 (needs concurrent sessions). |

## When this doc goes stale

- When Stage 4f follow-up commits add any of the pending tests above,
  move the row from "Pending" to "Current test coverage" and update the
  status column.
- When Stage 6 brings up a real Celery + Redis integration test, the
  ⛔ rows for `send_push_batch` and `_run_batch` should flip to ✅.
- If the FCM mock is ever replaced by a recorded VCR-style cassette,
  mark it 🧪 with a pointer to the cassette file.
