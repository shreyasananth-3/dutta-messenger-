# Extraction Shopping List — Stage 4e (media) + 4f (notifications)

> Research output from a read-only Explore sub-agent run against
> `origin/track/media` and `origin/track/notifications` on 2026-04-18
> with `main` at commit `0c8a4e1`. The fresh Claude session that picks
> up Stage 4e + 4f should treat this as the pre-flight for both PRs.

---

## PR #5 — Worker B — `track/media`

### Status: essentially landing-ready

No structural surgery required. One clean cherry-pick of the new-only
files, followed by `make preflight` and a commit.

### Files to land (new-only)

- `src/modules/media/__init__.py`
- `src/modules/media/router.py`
- `src/modules/media/docs/MODULE.md` — copy of reference-docs
- `src/modules/media/docs/SCHEMA.sql` — copy of reference-docs
- `src/modules/media/docs/API.md` — generated examples
- `src/modules/media/models/__init__.py`
- `src/modules/media/models/db_models.py`
- `src/modules/media/models/request_models.py`
- `src/modules/media/models/response_models.py`
- `src/modules/media/routes/__init__.py`
- `src/modules/media/routes/media_routes.py`
- `src/modules/media/services/__init__.py`
- `src/modules/media/services/media_service.py`
- `migrations/versions/0005_media_module_schema.py` — correctly numbered
  with `down_revision = "0004_users_module_schema"`
- `docs/design/threat-model-media.md`
- Test suite:
  - `tests/modules/media/__init__.py`
  - `tests/modules/media/conftest.py`
  - `tests/modules/media/factories.py`
  - `tests/modules/media/test_media_service.py` (21 test functions)
  - `tests/modules/media/test_media_routes.py` (34 test functions)

### Conflicts to resolve

None beyond normal cherry-pick hygiene. Worker B's branch correctly
rebased onto the lead's Stage-3 work, so the shared primitives and
users module are already aligned.

### Reinvented infrastructure to delete

None. Worker B uses `src/shared/storage.py`,
`src/shared/middleware/idempotency.py`, `src/shared/celery_app.py`
correctly.

### Compliance checklist

| Requirement | Status |
|---|---|
| `AppException` subclasses (no bare `HTTPException`) | ✅ clean |
| `require_idempotency` on mutating endpoints | ✅ wired on `POST /media/upload/init` |
| `write_audit()` on mutations | ✅ called in `init_upload`, `complete_upload`, `enter_recycle_bin` |
| `tenant_scoped_query()` on tenant-scoped reads | ✅ every service method |
| Ruff findings | Expect ≈ 0 in new-only files (worker followed format) |

### Tests the worker wrote

55 test functions total. Covers the 7-point CLAUDE.md checklist per
endpoint plus cross-tenant fuzz.

### Estimated effort to land cleanly

~30 minutes. Cherry-pick → run `make preflight` → commit as
`feat(media): Stage 4e — upload, presigned URLs, recycle bin` → push.

### Minor cosmetic touches Worker B also made (to be included in the
### cherry-pick or pulled into a follow-up)

- Dash typo fix in `src/shared/storage.py`
- `cast()` removal in `src/shared/storage.py`
- `noqa` comment tweak in `src/shared/middleware/idempotency.py`

None are structural; including them is fine.

---

## PR #4 — Worker C — `track/notifications`

### Status: needs three structural surgeries before landing

Worker C's branch was cut from a pre-users baseline. The PR diff
contains **deletions** of `src/modules/users/`, `src/shared/celery_app.py`,
`src/shared/middleware/idempotency.py`, and `src/shared/storage.py` —
all of which exist on main and MUST NOT be deleted. The cherry-pick
strategy must be: take only the new files in `src/modules/notifications/`
+ its tests; discard every deletion.

### Files to land (new-only, filter out deletions)

- `src/modules/notifications/__init__.py`
- `src/modules/notifications/router.py`
- `src/modules/notifications/docs/MODULE.md`
- `src/modules/notifications/docs/API.md`
- `src/modules/notifications/docs/TEST_REALNESS.md` — worker-written
  testing notes, review for usefulness
- `src/modules/notifications/docs/threat-model-notifications.md`
- `src/modules/notifications/models/__init__.py`
- `src/modules/notifications/models/db_models.py`
- `src/modules/notifications/models/request_models.py`
- `src/modules/notifications/models/response_models.py`
- `src/modules/notifications/routes/__init__.py`
- `src/modules/notifications/routes/feed_routes.py`
- `src/modules/notifications/routes/token_routes.py`
- `src/modules/notifications/services/__init__.py`
- `src/modules/notifications/services/fanout_service.py`
- `src/modules/notifications/services/token_service.py`
- `src/modules/notifications/tasks/__init__.py`
- `src/modules/notifications/tasks/_firebase_client.py`
- `src/modules/notifications/tasks/push_task.py`
- Tests:
  - `tests/modules/notifications/__init__.py`
  - `tests/modules/notifications/conftest.py`
  - `tests/modules/notifications/factories.py`
  - `tests/modules/notifications/test_fanout_service.py` (5 tests)
  - `tests/modules/notifications/test_token_service.py` (12 tests)
  - `tests/modules/notifications/test_push_task.py` (11 tests)
  - `tests/modules/notifications/test_notification_routes.py` (16 tests)
- Also touched (additive, safe to cherry-pick):
  - `src/config.py` — FCM settings
  - `src/shared/observability/metrics.py` — `dutta_notifications_*`
    counter (needed for SLI 5 in `docs/design/slo.md`)
  - `src/shared/security/audit.py` — `NOTIFICATION_TOKEN_REVOKED` etc.
    enum additions

### Conflicts to resolve

**The migration collision.** Worker C's file is
`migrations/versions/0004_notif_fanout_idx.py` with revision
`0004_notif_fanout_idx` and `down_revision="0003_refresh_tokens_updated_at"`.
Main already has `0004_users_module_schema` revising the same parent.

**Surgery:**
- Rename file → `migrations/versions/0006_notifications_schema.py`.
- Inside the file, change `revision = "0004_notif_fanout_idx"` →
  `revision = "0006_notifications_schema"`.
- Change `down_revision = "0003_refresh_tokens_updated_at"` →
  `down_revision = "0005_media_module_schema"` (IF media has landed
  first — media is `0005`, notifications is `0006`).
- Verify the migration body still applies cleanly to a DB that already
  has the users + media schemas.

### Reinvented infrastructure to delete

**`src/modules/notifications/celery_app.py`** — 33 lines duplicating
`src/shared/celery_app.py`. Delete this file. In
`src/modules/notifications/tasks/push_task.py` and anywhere else it's
imported, replace `from src.modules.notifications.celery_app import
celery_app` with `from src.shared.celery_app import celery_app`.

### Compliance checklist

| Requirement | Status |
|---|---|
| `AppException` subclasses | ✅ clean |
| `require_idempotency` on mutating endpoints | ⚠️ `POST /fcm-tokens` needs wiring; `POST /mark-read` is RFC-exempt (bulk mark-as-read is naturally idempotent) |
| `write_audit()` on mutations | ⚠️ **`mark_read` in `feed_routes.py` runs a raw `sqlalchemy.update()` with NO `write_audit()` call** — add `AuditEvent.NOTIFICATIONS_MARKED_READ` (or reuse existing) + `write_audit` call |
| `tenant_scoped_query()` | ⚠️ **`unread_count` uses a raw `select(func.count(...))` scoped only by `user_id` with no `institution_id` filter** — violates tenant isolation. Wrap with `tenant_scoped_query()` or add the filter manually |
| Ruff findings in new-only files | Low — worker ran format. The 71 ruff errors on PR #4's CI were dominated by pre-existing Stage-0/1/2 issues that main has since fixed (commit `eb03730` cleanup) |

### Tests the worker wrote

44 test functions across 4 files + factories.py. Coverage estimate:
~75–80% for `src/modules/notifications/`. If a few % short of the 80%
target, add 2–3 targeted tests for the tenant-isolation fuzz +
`write_audit` verification in `mark_read`.

### Estimated effort to land cleanly

~90 minutes:
- 15 min — cherry-pick with exclusions
- 15 min — migration rename + down_revision update
- 10 min — delete duplicate celery_app + repoint imports
- 20 min — add `institution_id` filter to `unread_count` + tests
- 15 min — add `write_audit` to `mark_read` + test
- 15 min — run `make preflight`, fix any fallout, commit

---

## Migration numbering decision

Once both PRs land, the migration chain is:

```
0001_baseline_schema
  → 0002_align_audit_logs
    → 0003_refresh_tokens_updated_at
      → 0004_users_module_schema           (currently on main)
        → 0005_media_module_schema         (land from PR #5)
          → 0006_notifications_schema      (land from PR #4, after rename)
```

**Media lands first.** Notifications revises media (not users directly),
so the rename + down_revision update on PR #4's migration is the
critical step.

---

## Overall recommendation

**Land media first (PR #5) — it's the easy one.** Clean cherry-pick,
no surgery, green CI on first attempt. Commit as `feat(media): Stage
4e — upload, presigned URLs, recycle bin` and close PR #5 with a
comment crediting Worker B and linking the commit.

**Then land notifications (PR #4)** with the three surgeries called out
above. Do them in order: (1) skip deletions during cherry-pick, (2)
renumber migration, (3) delete + repoint celery_app, (4) compliance
fixes (tenant-scope `unread_count`, audit `mark_read`), (5) run
`make preflight`, (6) commit + close PR #4.

**Do NOT merge either PR directly via `gh pr merge`.** Both contain
content that conflicts with main; a GitHub merge would either fast-
forward broken state or produce an unreviewable merge conflict blob.
Cherry-pick + close is the right play.

Dependabot PRs #1–#3 can merge cleanly once this shopping list is
executed — CI on main is green, so their rerun should go green too.
