# Pick-up Pointer — DuttaMessenger

> **Paste this file's path (or its contents) into a fresh Claude Code session to resume work.**
> Claude will also auto-load `CLAUDE.md` (pre-flight + post-flight rules) and `~/.claude/projects/.../memory/MEMORY.md` (user preferences).

## The four docs any new session / engineer must read

1. **This file** — current state, stage status, pick-up prompts.
2. **`CLAUDE.md`** (repo root) — MANDATORY PRE-FLIGHT + POST-FLIGHT rules. Auto-loads in every session.
3. **`docs/LOCAL_SETUP.md`** — battle-tested setup recipe + troubleshooting for every gotcha we actually hit.
4. **`docs/LOCAL_TESTING.md`** — multi-user scenarios, load testing, chat reaction-time measurement, audit review recipe. **Read this before claiming a module is "ready"**.

For plans and design: `/Users/guru/.claude/plans/now-go-through-the-twinkly-wombat.md` (the full plan).
For contracts: `docs/ui-contract/` (Flutter team's source of truth).

---

## Where we are

| Stage | Status | Commit |
|-------|--------|--------|
| 0 — Tooling, CI, test harness | ✅ done | `58cd5eb` |
| 1 — Observability + security baseline | ✅ done | `7197905` |
| UI contract (auth slice) | ✅ done | `07abfd4` |
| 2 — Backfill tests for `shared/` + `auth` | ✅ done | `c669559` |
| Chore: Makefile + run_tests.sh through venv | ✅ done | `c80828a`, `48062c3` |
| Manual live-server smoke (auth) | ✅ done — recipe at [docs/MANUAL_SMOKE.md](MANUAL_SMOKE.md) | |
| 3 — 7 mini-RFCs | ✅ drafted + implementability-checked (4 drifts fixed) — see [docs/design/](design/) — **7 open questions below need human sign-off before Stage 4** | |
| Stage-0/1 gap fix: celery_app + storage + idempotency middleware | ✅ done | `d8714fc`, `512c0bd`, `3c1e7f5` |
| 4a — `users` module (7 endpoints; 8th deferred to 4b) | ✅ done | `2a9b84d` |
| 4b — `acl` module | ⏳ | |
| 4c — `groups` module | ⏳ | |
| 4d — `chat` module (incl. WebSocket) | ⏳ | |
| 4e — `media` module | ✅ done — extracted from PR #5 ([closed](https://github.com/shreyasananth-3/dutta-messenger-/pull/5)) | `5c83a1a` |
| 4f — `notifications` module | ✅ done — extracted from PR #4 ([closed](https://github.com/shreyasananth-3/dutta-messenger-/pull/4)) | `f654775` |
| 5 — UI contract (all modules) | ⏳ | |
| 6 — Load + **E2E (tests/e2e/)** + seed | ⏳ | |

**Repo:** https://github.com/shreyasananth-3/dutta-messenger-.git
**Branch:** `main` (trunk-based, every module lands behind an `ENABLE_*` feature flag that defaults OFF).

---

## Outstanding work — not on the main stage track

Small items that aren't a full stage but shouldn't be lost:

| Item | Status | Where |
|------|--------|-------|
| **Manual live-server smoke — media (4e) + notifications (4f)** | ⏳ NOT yet run — see "Live-smoke gaps" table below for what's untested and the likely failure modes | [docs/MANUAL_SMOKE.md](MANUAL_SMOKE.md) — extend the recipe |
| **Manual live-server smoke (auth slice)** | ✅ green — recipe + 3 gaps recorded | [docs/MANUAL_SMOKE.md](MANUAL_SMOKE.md) |
| **Gap A — `audit_logs` not written on mutations** (surfaced by smoke) — infra exists, no routes call `audit.log(...)` | ⏳ fix during/after Stage 3 (taxonomy decided in tenant-isolation RFC) | [MANUAL_SMOKE.md § Gap A](MANUAL_SMOKE.md) |
| **Gap B — inconsistent error envelope** (surfaced by smoke) — routes raising `HTTPException(detail=...)` bypass the standard `{error:{code,message,details}}` shape CLAUDE.md mandates | ⏳ quick cleanup (~4 call sites in auth routes) | [MANUAL_SMOKE.md § Gap B](MANUAL_SMOKE.md) |
| **Gap C — refresh tokens not rotated** (surfaced by smoke) — old refresh token stays valid after `/auth/refresh`, no replay detection | ⏳ small service-layer fix + test | [MANUAL_SMOKE.md § Gap C](MANUAL_SMOKE.md) |
| **E2E tests (`tests/e2e/`)** — full-journey pytest: register → invite → accept → create group → send message → read → react → push | ⏳ deferred to Stage 6 | `tests/e2e/` is empty on purpose; E2E needs all modules to exist first |
| **72 pre-existing ruff findings** surfaced by `make lint` (S105 hardcoded-password warnings on `config.py` defaults, I001 import-sort across auth + shared) — was invisible before `c80828a` wired `make lint` to the venv | ⏳ backlog | fix with `make format` pass + review, or add targeted `# noqa` for intentional dev defaults |
| **Push local commits to origin** — chore + smoke + Stage-3 RFC commits are all unpushed | ⏳ after you review Stage 3 open questions | `git push origin main` |

---

## Live-smoke gaps — media (4e) + notifications (4f)

Both modules landed with the full automated test suite green (452 pytest
functions, 95.98% line coverage, real Postgres via asyncpg + real FastAPI
app via ASGITransport). That catches contract correctness, service logic,
tenant isolation, audit writes, and the 7-point checklist. It does NOT
catch integration failures against real Redis / MinIO / Celery / FCM —
the same category of bug that Gaps A/B/C in [MANUAL_SMOKE.md](MANUAL_SMOKE.md)
surfaced for auth.

**Rule of thumb (from auth-slice experience):** every module that touches
an external system beyond Postgres ships with a live-smoke TODO until
the recipe runs green. Record the expected gap here before it surprises us.

| # | Untested path | Why tests don't catch it | Likely failure modes |
|---|---------------|--------------------------|----------------------|
| 1 | **MinIO / S3 presign → PUT → complete round-trip** (media) | `tests/modules/media/conftest.py` monkey-patches every `src.shared.storage` function with an in-process stub. The real `boto3` signing and bucket round-trip never fires. | Clock-skew signature mismatch on `PUT`; CORS denial from browser uploads; bucket-policy denies HEAD during `complete_upload`; 0-byte object slipping past virus-scan gate; multipart path never exercised. |
| 2 | **Full Alembic chain on a fresh DB** (`0001 → 0006`) | CI runs `alembic upgrade head` but only on the ephemeral Postgres service container — never a downgrade round trip and never against a DB that already had `0004_users_module_schema` mid-history. Local dev DB has been incrementally upgraded, never clean-seeded. | `0005_media_module_schema` drops-and-recreates `media_files` — real prod rows would be destroyed (`tests_scripted` feature-flagged OFF masks this); `0006_notifications_schema` creates partial indexes concurrently, could race with a running app. |
| 3 | **Celery worker picking up `notifications.send_push_batch`** | `push_task.send_push_batch` and `_run_batch` carry `# pragma: no cover — Celery entry point`. Tests drive `run_batch()` directly, bypassing the Celery bootstrap. | Task registration failure at worker boot (autodiscovery mis-wired); `asyncio.run()` inside the Celery sync wrapper colliding with worker's own loop policy; JSON serialisation of `uuid.UUID` args; `SessionLocal()` opening a new asyncpg connection per task blowing the pool under retries. |
| 4 | **Real FCM send path** (`FCM_MOCK_MODE=false`) | Every test uses `MockFcmClient`; `src/modules/notifications/tasks/_firebase_client.py::FirebaseAdminClient` is untouched. | Missing `FCM_PROJECT_ID` / `FCM_PRIVATE_KEY` → 500 on first send; endpoint template `/projects/{project_id}/messages:send` never string-formatted with real ID; >500-recipient batch rejected by HTTP v1 API; `UNREGISTERED` response parsing mis-shaped; OAuth token exchange failing silently in the mock shadow. |
| 5 | **Real Redis for the idempotency middleware** (media `POST /upload/init`) | `tests/modules/media/conftest.py` mocks Redis with an in-process dict. `src/shared/middleware/idempotency.py`'s fail-open-on-Redis-down branch fires in mock but was never proven against real Redis DOWN. | TTL not honoured (key sticks past 24h); `SETNX` race between two duplicate requests; fail-open counter `dutta_idempotency_redis_down_total` never incrementing in prod because real Redis is reachable but slow (timeout path untested); serialized response body exceeding 512KB Redis value limit on a large `media` response. |
| 6 | **All four modules enabled simultaneously via `uvicorn`** | `tests/conftest.py` forces `ENABLE_USERS / ENABLE_MEDIA / ENABLE_NOTIFICATIONS` ON for the ASGI app — but never via a real `uvicorn src.main:app --reload` boot with the same env vars. | `src/main.py::create_app()` late-imports each module's `router.py`; a stale `.pyc` or an `ImportError` only surfaces at boot; Prometheus metric registration collision if two modules define the same counter label; OpenAPI path-operation-id collision between media and notifications. |
| 7 | **Refresh-token rotation + FCM-token revocation interaction** | Auth Gap C (refresh tokens not rotated) is still open. A logout should also revoke the device's FCM tokens — the notifications module's `TokenService.revoke_token` exists, but no caller wires it into the logout flow. | User logs out on device A, device A keeps receiving push (security + privacy issue — DPDP §8(3) "right to erasure of consent"). Surfaces only under a multi-device live scenario. |
| 8 | **Cross-institution fuzz on the live server** | Automated tests use pytest fixtures for two institutions; the JWT is always freshly minted in-process. A live server with two real `register` flows + JWT swap between them hasn't been exercised. | Middleware order bug where `institution_id` is read before JWT is verified; Postgres RLS policies (not yet enabled per tenant-isolation.md §1.3) masking bugs that app-layer filter misses. |

**When to work through these:** before the first staging deploy of any
module, run the corresponding row as a live smoke. Track fixes as Gaps
D, E, F… in [MANUAL_SMOKE.md](MANUAL_SMOKE.md) with the same discipline
as Gaps A/B/C on auth.

**When NOT to block on these:** Stage 4b (`acl`) through 4d (`chat`) can
proceed. None of them add new external-system dependencies beyond what
auth already exercises. The live-smoke debt is for 4e + 4f specifically
and for any future module that adds a third-party integration.

---

## Stage 3 open questions — need the human's sign-off before Stage 4

The 7 RFCs ship with status `draft`. Each surfaced one business-side question
the backend can't decide alone. Paste your decision next to each bullet, then
flip the RFC's frontmatter `status: draft → accepted`.

1. **[idempotency.md](design/idempotency.md)** — Redis-backed Idempotency-Key
   TTL: **24 hours** (default) vs **4 hours** (less memory, less tolerant of
   overnight offline retries). Decision needed before chat/media are built.

2. **[tenant-isolation.md](design/tenant-isolation.md)** — Should a read-only
   Postgres role `dm_auditor` exist for ops queries against `audit_logs`
   (no API, DB-only)? Safe to add since `audit_logs` has no RLS, but needs
   a policy call before first production deploy.

3. **[websocket-scaling.md](design/websocket-scaling.md)** — Two new frame
   types (`token.expiring`, `auth.refresh`) were added for in-session token
   rotation. Should `reference-docs/modules/chat/WEBSOCKET.md` be updated
   NOW (Flutter team gets the spec early) or DEFERRED to Stage 4d?

4. **[message-partitioning.md](design/message-partitioning.md)** — Archival
   window is proposed at **18 months**. Any regulatory retention minimum for
   Indian school communications that forces a longer hot window (e.g.,
   5 years for academic records)? If yes, archival window tightens.

5. **[api-versioning.md](design/api-versioning.md)** — Canonical error
   envelope is fixed. Should `details` be `{}` (always present) or omitted
   when empty? UI team preference — talk to Flutter lead.

6. **[privacy-erasure.md](design/privacy-erasure.md)** — Proposed erasure
   SLA is **30 days** (matches DPDP default). If the school's own privacy
   notice commits to something shorter (e.g., 10 days), the Celery task SLA
   in `slo.md` must tighten. **DPDP §9 (child consent) + §13 (Grievance
   Officer) + 72h breach notification** need explicit legal sign-off — not a
   backend decision.

7. **[slo.md](design/slo.md)** — Availability SLO is **99.95%** (≈ 22 min
   downtime budget per month). Does the school's IT lead accept that
   number? And does Saturday morning work as the quarterly 30-min
   maintenance window?

Until these 7 are answered, Stage 4 can START (the code patterns don't
change based on the answers) but modules must keep their RFC-driven
behaviour easy to tune (e.g., TTL via config, not hardcoded).

---

## Stage 3 implementability check — what was verified against real code

After the RFCs landed, we ran a structural verification pass: for every
concrete claim each RFC makes about the existing codebase, grep / read
the actual file and confirm. Not pytest, but the closest equivalent for
design docs.

### ✅ Verified correct

| Claim | Source RFC | Real ground truth |
|---|---|---|
| `write_audit(db, actor_id, institution_id, action, resource_type, resource_id, metadata)` exists | tenant-isolation.md | `src/shared/security/audit.py:72` — matches exactly |
| `tenant_scoped_query(model, institution_id)` exists | tenant-isolation.md | `src/shared/security/tenant.py:32` — matches exactly |
| `dutta_message_delivery_latency_seconds` metric exists | slo.md | `src/shared/observability/metrics.py:31` — matches |
| `dutta_websocket_connections` gauge exists | slo.md, websocket-scaling.md | `metrics.py:37` — matches |
| `http_requests_total{handler,method,status}` is emitted | slo.md | Confirmed by live smoke run — auto-generated by `prometheus-fastapi-instrumentator` |
| Error-code catalog (NOT_FOUND, PERMISSION_DENIED, …) matches shipped `AppException` subclasses | api-versioning.md | `src/shared/exceptions.py` — all 7 codes match |
| Numerical cross-RFC consistency (idempotency 24h, WS 30s ping / 10s pong, msg p95 2s, archive 18mo, trigger 10M, audit retention 7yr, availability 99.95%) | all 7 RFCs | No contradictions found |
| `TenantScopeViolation` → 404 (not 403) so attackers can't distinguish existence | tenant-isolation.md | `src/shared/security/tenant.py:23` docstring confirms design |
| Tables with `institution_id` column: users, user_invitations, roles, groups (+ audit_logs) | tenant-isolation.md | `\d` on dev DB confirms 5 — RFC correctly excludes audit_logs from RLS |

### ⚠️ 4 drifts found and fixed in-place

1. **`api-versioning.md` said "four" bare `HTTPException` call sites — real count is seven.** The RFC repeated the number 4 times (in Context, Decision, implementation notes, and Future Work). All four occurrences corrected to "seven" with a note that the Stage-3 implementability check surfaced the mismatch.

2. **`api-versioning.md` contradicted itself on `RATE_LIMITED` vs `RATE_LIMIT_EXCEEDED`.** The middleware fallback map used `RATE_LIMITED` while the existing-code table used `RATE_LIMIT_EXCEEDED` (the shipped `AppException`). Harmonised: drop the `RATE_LIMITED` alias entirely, keep only the shipped `RATE_LIMIT_EXCEEDED`. One code across the codebase.

3. **`tenant-isolation.md` §2.1 said `actor_id` can be `NULL` for system actors — the shipped `write_audit()` signature does not allow it.** Rather than change the code (which is already covered by 100% tests), the RFC was corrected to mandate a sentinel UUID `00000000-0000-0000-0000-000000000000` exported as `SYSTEM_ACTOR_ID` for seed scripts and background tasks. Zero code change needed; easier to audit than a nullable column.

4. **`tenant-isolation.md` §2.1 implied the `AuditEvent` enum was already complete — it has 14 values today, but the RFC proposes ~32.** Clarified: §2.3's extended enum is the **target state after Stage 4**, not a claim about what exists. Stage-4 module authors must extend `AuditEvent` in the shared module (NOT invent raw strings).

### ❓ Still unverified (implementation validates, not review)

- Does Postgres RLS actually prune queries correctly under `tenant_scoped_query()`? First real cross-tenant fuzz test in Stage 4a will prove it.
- Does the proposed middleware in `api-versioning.md` catch FastAPI's own 422 shape correctly? Needs to be wired before Stage 4 starts.
- Does the websocket backpressure threshold of 1000 messages actually keep p95 delivery under 2s? Proven by Stage 6 load tests, not before.

If any of these fail in Stage 4, the fix is a **minor RFC amendment**, not
a module rewrite. The Stage-3 decisions are still load-bearing.

### Methodology amendment (added after the Stage-4a kickoff surfaced a miss)

The Stage-3 implementability check only verified that functions the RFCs
**NAMED** existed (e.g., `write_audit()`, `tenant_scoped_query()`). It did
NOT verify that functions the RFCs **REQUIRED AS NEW INFRASTRUCTURE**
actually existed. Worker B (Stage 4e, media) correctly flagged three
missing primitives before starting:

- `src/shared/celery_app.py` — listed in CLAUDE.md, never built.
- `src/shared/storage.py` — listed in CLAUDE.md, never built.
- `src/shared/middleware/idempotency.py` — required by idempotency.md,
  never built.

**New rule for every future RFC round:** when verifying implementability,
grep the RFC for every "Files touched / created" table and confirm each
entry either (a) already exists or (b) is a deliberate "to be built in
Stage N" item with Stage N scheduled. If in doubt, attempt a dry-run
import of each named symbol.

This amendment has been applied retroactively to Stage 3's RFCs — the
three primitives above shipped as commits `d8714fc`, `512c0bd`, `3c1e7f5`
before Worker B (media) and Worker C (notifications) are unblocked.

---

## Stage 4 progress — Chain A

| Module | Endpoints | Status | Notes |
|--------|-----------|--------|-------|
| `users` | 7 of 8 | ✅ merged `2a9b84d` | `PATCH /users/{id}/status` deferred to ACL (Stage 4b) — requires `institution.manage_users` permission which ACL owns |
| `acl` | — | ⏳ next | Will wire up admin endpoint on `users` + own ACL endpoints |
| `groups` | — | ⏳ | |
| `chat` | — | ⏳ | |

**Worker B (media) and Worker C (notifications) are now UNBLOCKED.**
Pull main, verify `make test` green (349 tests), then run the worktree
setup + build per their kickoff prompts. One correction to both prompts:
the real import for `BaseModel` is `from src.shared.database import
BaseModel`, not `from src.shared.models import BaseModel`.

---

## Retrospective — why the worktree-parallel experiment didn't pay off

Ran the plan's Option 3 (one lead + two worker Claude sessions in git
worktrees). Result: worker B (media) and worker C (notifications) each
opened a PR, but neither was mergeable. Root causes worth learning from:

1. **Shared `.git/`, drifting HEAD.** Worktrees point at the same object
   store, so when the lead committed locally (but before pushing),
   worker branches that happened to be created after that point silently
   picked up the lead's work. Worker B's PR ended up including ~2 000
   lines of the lead's Stage-3 work + users module as "my diff". Worker
   C's PR came from an older baseline and missed it entirely.
2. **Missing shared primitives weren't discoverable ahead of time.**
   Worker B correctly stopped when it found that
   `src/shared/middleware/idempotency.py`, `src/shared/storage.py`, and
   `src/shared/celery_app.py` didn't exist despite being in CLAUDE.md's
   project structure. But the lead's Stage-3 implementability check only
   verified function names the RFCs cited, not the "Files touched /
   created" rows inside each RFC. The methodology has since been amended
   (see "Methodology amendment" above). Workers blocked for a round.
3. **Workers reinvented primitives anyway.** Worker C, not seeing the
   shared `celery_app` on its older baseline, wrote its own inside
   `src/modules/notifications/celery_app.py` (33 lines) — exactly the
   duplication the worktree pattern was supposed to prevent.
4. **Migration numbering collided.** Worker C's migration was numbered
   `0004_notif_fanout_idx.py`, same revision number as the lead's
   `0004_users_module_schema.py`. Alembic would pick one randomly on
   `upgrade head`. Worker B got this right only by chance (they happened
   to pick 0005 because their branch already contained 0004).
5. **Net time.** ~4 hours of cross-session coordination produced two
   unmergeable PRs. The serial path — lead building each module with full
   context of the primitives and previous modules — would have taken
   ~2 hours.

**Going forward:** abandon the worktree-parallel pattern for this
project. Stage 4b (`acl`), 4c (`groups`), 4d (`chat`), 4e (`media`),
4f (`notifications`) will be built serially by a single lead session on
`main`. Worker B and Worker C's PRs will be closed after extracting
their actual new-only module code (see the fresh-session handoff
below).

---

## Fresh-session handoff — what to do next

Stages 4e and 4f landed on `main` via clean extraction from the two
open PRs — see the stage table above for commits.

### What this session did (2026-04-18)

- **Stage 4e — media — `5c83a1a`.** Extracted PR #5's five Worker B
  commits (AuditEvent additions, migration 0005, module files, tests,
  OpenAPI regen) via `git cherry-pick`, resolved one audit-enum merge
  conflict, squashed into a single `feat(media)` commit. 55 new tests
  pass; total suite 406.
- **Stage 4f — notifications — `f654775`.** Could NOT cherry-pick —
  Worker C's branch was cut from a pre-4a/4e baseline and the PR
  diff deleted `users/`, `media/`, `src/shared/celery_app.py`,
  `src/shared/storage.py`, `src/shared/middleware/idempotency.py`. Used
  `git checkout pr-4 -- <new-only paths>` instead and applied the
  five surgeries from `docs/extraction-shopping-list.md`:
  1. cherry-pick new-only files only;
  2. renamed migration `0004_notif_fanout_idx` →
     `0006_notifications_schema` (revises `0005_media_module_schema`);
  3. deleted `src/modules/notifications/celery_app.py`, repointed
     `push_task.py` to `src.shared.celery_app`;
  4. added explicit `User.institution_id` JOIN to `unread_count` — a
     defence-in-depth filter since `notifications` is transitively
     scoped per tenant-isolation.md §1.2;
  5. added `write_audit(... NOTIFICATIONS_MARKED_READ)` to `mark_read`
     inside the same transaction when rowcount > 0; extended
     `AuditEvent` with 4 new values.
- **Collateral fixes discovered during preflight:**
  - `tests/shared/test_celery_app.py::test_no_tasks_registered_at_import`
    was asserting the shared Celery app had zero user-level tasks;
    that's now stale since notifications registers one. Rewrote as
    `test_only_known_modules_register_tasks` with an allow-list.
  - `ENABLE_NOTIFICATIONS` added to the force-on flag list in
    `tests/conftest.py` alongside `ENABLE_USERS` and `ENABLE_MEDIA`.
- **PRs closed with credit.** PR #5 and PR #4 closed via `gh pr close`
  (NOT merged) with comments pointing to the landing commits. Worker
  branches `track/media` + `track/notifications` left untouched as a
  paper trail.
- **Dependabot:** PR #1 (actions/checkout 4→6) merged clean on a green
  CI after the main push. PR #2 + PR #3 were rebased via
  `@dependabot rebase` comments; merge once CI goes green.

### What the next session should do first

1. `git pull origin main` — expect two new commits: the media landing
   (`5c83a1a`) and the notifications landing (`f654775`).
2. Verify `make test` green — 452 tests passing locally at commit
   `f654775`. Coverage ≥ 80% for both media and notifications (media is
   at 100% / 100% line+branch).
3. **Dependabot PRs #2 + #3.** If still open and now green after
   rebase, `gh pr merge <n> --squash --delete-branch`. If CI red on
   rebase, investigate quickly (these are GitHub-Actions version bumps
   — fix forward, don't let them rot).
4. **Stage 4b (`acl`) is next on the serial path.** Read
   `reference-docs/modules/acl/MODULE.md` and `SCHEMA.sql` before
   coding; follow the pattern established by `src/modules/users/` and
   `src/modules/media/`. The 8th users endpoint
   (`PATCH /users/{id}/status`) was deferred to 4b because it requires
   `institution.manage_users`.
5. **Do NOT resume the worktree-parallel pattern.** Build 4b → 4c →
   4d serially here on `main`, then Stage 5 (UI contract package) +
   Stage 6 (load/E2E).

### Known deviations from the extraction shopping list

- Commit message for 4e matches the prescribed title exactly. Commit
  message for 4f is the prescribed title; body expanded to document
  the five surgeries applied (for the audit trail).
- Two extra compliance tests added to
  `tests/modules/notifications/test_notification_routes.py` (forged-JWT
  unread probe + `mark_read` audit-row presence/absence) — not in the
  shopping list explicitly but required by CLAUDE.md POST-FLIGHT §C
  for the compliance fixes to be considered proven.
- `tests/shared/test_celery_app.py` required an update the shopping
  list did not predict — the existing assertion that "no user tasks
  are registered at import" became false the moment notifications'
  `push_task` module was imported by any pytest in the same session.
  Rewritten as an allow-list assertion.

### Files the fresh session should read first

- `CLAUDE.md` (auto-loaded).
- `docs/NEXT_SESSION.md` (this file).
- `docs/design/rfc.template.md` + any of the 7 RFCs relevant to the next
  module (e.g. `tenant-isolation.md` is mandatory for every module).
- `reference-docs/modules/{name}/MODULE.md` for whichever module is next.
- `src/modules/users/` as a complete working reference implementation
  (schema + services + routes + tests + API.md + OpenAPI export).

### Open questions that still need the human's sign-off

Unchanged from earlier in this file. The 7 Stage-3 RFC decisions are
still in `status: draft` until you answer the business-side
questions (idempotency TTL, dm_auditor role, WS frame additions,
retention minimums, error-envelope shape, DPDP timeline, SLO
acceptance).

---

## The two docs Claude must read before doing anything

1. **`/Users/guru/.claude/plans/now-go-through-the-twinkly-wombat.md`** — the full plan (v3, architect-reviewed, right-sized for a 1,000–5,000 user school). Stages, parallelization strategy, cross-cutting standards, proof checklist.
2. **`CLAUDE.md`** at repo root — MANDATORY PRE-FLIGHT + MANDATORY POST-FLIGHT. Claude auto-loads it.

Everything else (module contracts, schema, conventions) is in `reference-docs/`.

---

## How to resume in a new session

Paste one of these prompts to start:

### "Continue the plan from where we left off"
```
Read /Users/guru/Desktop/Work/Radlabs/DuttaMessenger/docs/NEXT_SESSION.md, then
/Users/guru/.claude/plans/now-go-through-the-twinkly-wombat.md, then CLAUDE.md.
Pick up at the next incomplete stage. Run POST-FLIGHT after each stage. Push
to GitHub after every stage boundary.
```

### "Jump to a specific module"
```
Start Stage 4c (groups module) per the plan. Before writing code:
  1. Read reference-docs/modules/groups/MODULE.md and SCHEMA.sql
  2. Copy them into src/modules/groups/docs/
  3. Fill in a threat model from docs/design/threat-model.template.md
Then build models → service → routes → tests, meeting the 85% coverage
gate, and run POST-FLIGHT before reporting done.
```

### "Explain current state"
```
Read docs/NEXT_SESSION.md and summarise what's done and what's pending.
Do not make changes.
```

---

## The database — where is it?

There is **no committed DB data file**. Three layers to know:

1. **Schema definition (version-controlled):**
   - `migrations/001_init_schema.sql` — the hand-written canonical SQL with all 20 tables.
   - `migrations/versions/0001_baseline_schema.py` — wraps that SQL in an Alembic revision with a **tested** `downgrade()`.
   - Future schema changes: `make migrate-new MSG="..."` creates a new Alembic revision; never another raw SQL file.

2. **Running database — two options on this machine:**

   **Option A (what we actually used): Homebrew Postgres 17.**
   - Already installed and running (`brew services list` shows `postgresql@17 started`).
   - Two databases created: `dutta_messenger` (dev) and `dutta_messenger_test` (pytest).
   - Owned by local user `guru`, no password.
   - `.env` at repo root (gitignored) points at this — see below.

   **Option B: Docker Compose.**
   - `docker compose up -d` → Postgres 16 container. Needs Docker Desktop running.
   - Data in volume `postgres_data`. Wiped by `docker compose down -v`.
   - Connection URL in `.env.example` (`messenger` user).

3. **Applying the schema to a running DB:**
   ```bash
   make migrate    # alembic upgrade head → runs 0001_baseline_schema
   ```
   Verified end-to-end on commit `f76e210`: upgrade → 21 tables, downgrade → 1, upgrade → 21.

**Production will use managed Postgres** (RDS / Cloud SQL) — same command, different `DATABASE_URL`.

### Exact local setup recipe (what worked on this machine)

```bash
# 1. DBs (one-time)
psql -h localhost -U guru -d postgres -c "CREATE DATABASE dutta_messenger;"
psql -h localhost -U guru -d postgres -c "CREATE DATABASE dutta_messenger_test;"

# 2. venv with Python 3.13 (system Python 3.9 is too old for pyproject)
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev,test]"

# 3. .env file at repo root with (at minimum):
#   DATABASE_URL=postgresql+asyncpg://guru@localhost:5432/dutta_messenger
#   TEST_DATABASE_URL=postgresql+asyncpg://guru@localhost:5432/dutta_messenger_test
#   SECRET_KEY=dev-only-secret-do-not-use-in-production

# 4. Apply schema
.venv/bin/alembic upgrade head              # dev DB
DATABASE_URL="postgresql+asyncpg://guru@localhost:5432/dutta_messenger_test" \
  .venv/bin/alembic upgrade head            # test DB

# 5. Smoke test harness
.venv/bin/pytest tests/test_harness_smoke.py -v --no-cov

# 6. Run the API
.venv/bin/uvicorn src.main:app --reload
```

---

## Ground rules Claude must honour (summary — full version in CLAUDE.md)

- **Before writing any module code:** read `reference-docs/modules/{name}/MODULE.md` and `SCHEMA.sql`. Copy them into `src/modules/{name}/docs/`.
- **After every task:** run POST-FLIGHT (sections A–G in CLAUDE.md). Report which boxes were verified.
- **Every module ships behind a feature flag** (`ENABLE_{NAME}` in `config.py`). Default OFF.
- **Every migration has a tested `downgrade()`.**
- **Every endpoint has the 7-point test checklist** (happy/401/403/400/404/idempotent/Unicode).
- **Every mutation writes to `audit_logs`** via `src/shared/security/audit.py`.
- **Every service method on tenant-scoped tables** uses `tenant_scoped_query()` from `src/shared/security/tenant.py`.

---

## What's already built and re-usable

| Purpose | Location |
|---------|----------|
| Async DB engine + session + `get_db` dependency | `src/shared/database.py` |
| JWT middleware (`get_current_user`) + token helpers | `src/shared/middleware/auth.py` |
| ACL decorator | `src/shared/middleware/acl.py` |
| Standard response helpers (`success_response`, `paginated_response`) | `src/shared/responses.py` |
| Exceptions (`AppException`, `NotFoundError`, `PermissionDeniedError`, …) | `src/shared/exceptions.py` |
| Cursor pagination encode/decode | `src/shared/utils/pagination.py` |
| Input validators | `src/shared/utils/validators.py` |
| Datetime helpers | `src/shared/utils/datetime_utils.py` |
| **Observability** (structlog + OTel + Prometheus + Sentry + correlation IDs) | `src/shared/observability/` |
| **Security** (rate limit, tenant scope, audit log, secrets provider) | `src/shared/security/` |
| Test harness (async `db_session`, `client`, `auth_headers`) | `tests/conftest.py` |
| Factories | `tests/factories.py` |
| Test runner with proof folder | `scripts/run_tests.sh` |
| OpenAPI exporter | `scripts/export_openapi.py` |
| Threat-model template | `docs/design/threat-model.template.md` |

---

## Last POST-FLIGHT summary

Stage 2 added the test backfill and corrected several latent bugs that were
silently shipping in Stage 1's code:

- **240 tests passing**, 0 failures, ~14s wall time.
- **Coverage:** `shared/` 95.51%, `modules/auth/` 90.35% — both above their
  CLAUDE.md gates (85% / 90%).
- **Latent bugs fixed while writing tests** (each meets the POST-FLIGHT bar
  "code matches the migration / docs"):
  - `audit_logs` table mismatched `src/shared/security/audit.py` →
    migration `0002_align_audit_logs` aligns columns
    (`actor_id` / `institution_id` / `metadata`).
  - `refresh_tokens` lacked `updated_at` while inheriting `BaseModel` →
    migration `0003_refresh_tokens_updated_at` adds it.
  - `BaseModel.id` was `String(36)` while the schema uses native `UUID` →
    switched to `postgresql.UUID(as_uuid=False)`. Auth FK columns updated to
    match.
  - `BaseModel.updated_at` had a Python-side `onupdate=func.now()` that
    expired the column post-flush and triggered async lazy-loading from
    Pydantic's `from_orm` (`MissingGreenlet`) → removed; the per-table DB
    trigger is the single source of truth.
  - Auth route handlers caught HTTPException as `Exception` and converted
    every 4xx into a 500 → added `except HTTPException: raise` between the
    `AppException` and bare `Exception` branches.
  - `passlib==1.7.4` couldn't read `bcrypt==5.0.0`'s version (no `__about__`)
    and 72-byte-flagged every password → switched `auth_service` to use the
    `bcrypt` library directly with truncation safety.
  - Auth `routes/auth_routes.py` referenced `InvitationResponse` without
    importing it; `Institution` / `User` declared relationships to `Role` /
    `Group` / `UserRole` whose modules don't exist yet → added the missing
    import; dropped the unbuilt-module relationships.
  - `model_to_dict` called `inspect(instance).columns` which doesn't exist
    on `InstanceState` → corrected to `inspect(instance).mapper.columns`.
- **Test infrastructure:**
  - `tests/conftest.py` now auto-loads `.env` so `TEST_DATABASE_URL` works
    out of the box; default falls back to the Homebrew Postgres recipe.
  - `pyproject.toml` pins `asyncio_default_*_loop_scope = "session"` so the
    session-scoped engine and per-test sessions share one loop (no more
    "Future attached to a different loop"). Adds
    `concurrency = ["thread", "greenlet"]` to coverage so coroutine lines
    inside FastAPI handlers are tracked.
  - `scripts/run_tests.sh` heredoc was unquoted, so `${...}` and `{...}` got
    bash-expanded and the per-module summary was empty → switched to
    `<<'PY'` and passed values via env vars.
- **OpenAPI snapshot** regenerated at `docs/ui-contract/openapi.json` —
  6 paths, the auth slice. Diff is deliberate (first commit of the file).
- Other 6 module directories still intentionally empty.
