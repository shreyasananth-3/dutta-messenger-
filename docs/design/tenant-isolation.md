---
title: "Tenant Isolation & Audit Taxonomy"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - src/modules/users/
  - src/modules/acl/
  - src/modules/groups/
  - src/modules/chat/
  - src/modules/media/
  - src/modules/notifications/
---

# Tenant Isolation & Audit Taxonomy

## Context

DuttaMessenger is deployed as one instance per school. Each deployment has a
single `institutions` row today, but the code isolates all data by
`institution_id` so a future multi-school deployment is a configuration change,
not a refactor. A cross-institution data leak is a catastrophic security failure
— a student at School A must never see any data owned by School B, even via a
crafted API request or a bug in a service method.

Two gaps motivate this RFC:

1. **Isolation enforcement is ad-hoc.** `src/shared/security/tenant.py` exists
   and has the right primitives (`tenant_scoped_query`, `assert_same_institution`,
   `TenantScopeViolation`) but there is no written rule for *when* module authors
   must call them. Without a written contract, developers forget.

2. **`audit_logs` is always empty** (Gap A from `docs/MANUAL_SMOKE.md`). The
   `write_audit()` helper in `src/shared/security/audit.py` is implemented and
   unit-tested but no service method calls it. Every Stage-4 module will wire
   audit calls; they need a stable event taxonomy to use.

This RFC is the pattern document every Stage-4 module author copies from.

## Decision

All tenant-scoped service methods use `tenant_scoped_query()` as their
query entry point (app-layer filter), AND all tenant-scoped tables carry a
Postgres Row-Level Security policy as defence-in-depth (RLS filter). Every
service method that mutates state emits exactly one `write_audit()` call inside
the same database transaction as the mutation, using the canonical event shapes
defined in this RFC. System actors (seed script, background jobs) emit audit
rows with `actor_id = NULL` and `metadata = {"system_actor": "<script_name>"}`.

## Details

### Scope

- App-layer isolation: `tenant_scoped_query()` and `assert_same_institution()`
  usage rules for all service methods touching tenant-scoped tables.
- Postgres RLS: which tables get policies, how `institution_id` is injected
  per connection, how admin and system actors bypass RLS.
- Cross-tenant fuzz testing: pytest fixture pattern every module must include.
- Audit taxonomy: the canonical `AuditEvent` enum extension, the field contract
  for every `write_audit()` call, and the idempotency replay rule.
- Bootstrap / system actor audit pattern.

### Non-goals

- Multi-institution UI (admin console showing all schools) — out of scope for
  this deployment size. RLS is still correct; it just always evaluates to one
  institution.
- Audit log retention policy and user erasure from audit rows — see
  `docs/design/privacy-erasure.md`.
- Idempotency-key deduplication of audit writes — see
  `docs/design/idempotency.md`. Short answer: idempotent replays that return a
  cached response MUST NOT write a second audit row.
- Per-topic or per-conversation isolation within a tenant — all such resources
  belong to the same institution and are governed by ACL, not tenant isolation.

---

### Part 1 — Multi-layer Isolation

#### 1.1 App-layer filter — mandatory for every service method

`src/shared/security/tenant.py` already provides:

```python
def tenant_scoped_query(model: Any, institution_id: uuid.UUID) -> Select[Any]:
    """Returns select(model).where(model.institution_id == institution_id)."""

def assert_same_institution(
    resource_institution_id: uuid.UUID | str | None,
    user_institution_id: uuid.UUID | str,
) -> None:
    """Raises TenantScopeViolation if IDs differ or resource_institution_id is None."""
```

**Rule: every service method that reads or mutates a tenant-scoped table must
start its query with `tenant_scoped_query()`.** There are no exceptions. If a
service method joins multiple tenant-scoped tables, apply the filter on the
driving table.

Canonical service method signature:

```python
async def get_group(
    db: AsyncSession,
    institution_id: uuid.UUID,
    group_id: uuid.UUID,
) -> Group:
    """Fetch a group, raising 404 if not found or cross-tenant.

    Args:
        db: Async database session.
        institution_id: The calling user's institution. Scopes the query.
        group_id: Primary key of the requested group.

    Returns:
        The Group ORM object.

    Raises:
        NotFoundError: If no group with that ID exists in this institution.
    """
    result = await db.execute(
        tenant_scoped_query(Group, institution_id).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise NotFoundError("group", group_id)
    return group
```

When a resource's `institution_id` is passed in via a path parameter (e.g.,
`/groups/{group_id}` where the group was fetched from user input), use
`assert_same_institution` to guard:

```python
assert_same_institution(group.institution_id, current_user.institution_id)
```

`TenantScopeViolation` must be caught at the route layer and converted to a
404 response (not 403 — never confirm that the resource exists in another
tenant):

```python
except TenantScopeViolation:
    raise NotFoundError("group", group_id)
```

#### 1.2 Tenant-scoped tables — full list

The following tables carry `institution_id` and therefore require both the
app-layer filter and an RLS policy. Determined from `migrations/001_init_schema.sql`:

| Table | Column |
|---|---|
| `users` | `institution_id` |
| `user_invitations` | `institution_id` |
| `roles` | `institution_id` |
| `groups` | `institution_id` |

The following tables do **not** have `institution_id` directly but are
transitively scoped through a FK join. Their isolation is enforced by ensuring
the parent is fetched through a tenant-scoped query first:

| Table | Scoped through |
|---|---|
| `topics` | `groups.institution_id` |
| `group_members` | `groups.institution_id` |
| `conversations` | `groups.institution_id` |
| `conversation_members` | `conversations → groups.institution_id` |
| `messages` | `conversations → groups.institution_id` |
| `message_reads` | `messages → conversations → groups.institution_id` |
| `media_files` | `users.institution_id` (via `user_id`) |
| `fcm_tokens` | `users.institution_id` (via `user_id`) |
| `notifications` | `users.institution_id` (via `user_id`) |
| `notification_batches` | `users.institution_id` (via `user_id`) |
| `refresh_tokens` | `users.institution_id` (via `user_id`) |

Note: `permissions` and `role_permissions` are global (no `institution_id`) —
they define the permission codespace, not per-institution state.

Note: `audit_logs` does carry `institution_id` in the `write_audit()` call
(added by this RFC, see schema delta in section 1.4). It is NOT subject to RLS
— it is a write-only append table for service code and read-only for operators
via direct Postgres access. No RLS is applied.

#### 1.3 Postgres Row-Level Security

RLS is the second layer of defence. A bug in application code cannot cause a
cross-tenant data leak because Postgres enforces the policy even for raw
queries that bypass the ORM.

**Policy pattern (same for every tenant-scoped table):**

```sql
-- Enable RLS on the table (row-level security blocks by default)
ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;

-- All queries must see only rows matching the current institution
CREATE POLICY tenant_isolation ON {table_name}
    USING (institution_id = current_setting('app.institution_id', true)::uuid);
```

`current_setting('app.institution_id', true)` returns NULL if the setting is
not set; the `true` argument prevents an error and lets the policy evaluate to
FALSE (no rows visible) — the safest default.

**Tables that get RLS policies (those with `institution_id` directly):**

- `users`
- `user_invitations`
- `roles`
- `groups`

Transitively-scoped tables (listed in 1.2) do NOT get RLS because they have no
`institution_id` column. Their parent FK query already passes through an RLS
table. Adding RLS to child tables would require joining back to parents in every
policy, which is expensive and fragile at this scale. The app-layer filter on
the parent is sufficient.

**Setting the session variable per request:**

In `src/shared/database.py`, the `get_db()` dependency sets the variable at
the start of every request session, immediately after the connection is
acquired from the pool:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        # Set institution_id for RLS as early as possible
        institution_id = _get_institution_id_from_context()  # see note
        if institution_id:
            await session.execute(
                text("SELECT set_config('app.institution_id', :iid, true)"),
                {"iid": str(institution_id)},
            )
        yield session
```

`true` as the third argument to `set_config` means the setting is local to the
transaction (reset on commit/rollback), which is correct for connection pooling
— the variable must not bleed from one request to the next on a reused
connection.

`_get_institution_id_from_context()` reads from a ContextVar populated by the
auth middleware after JWT verification. The auth middleware already sets
`current_user` on the request state; a small helper extracts
`current_user.institution_id` from that.

Implementation file: `src/shared/database.py` (modify existing `get_db`).
Auth ContextVar: `src/shared/middleware/auth.py` (populate on each request).

**Admin / system bypass:**

Background jobs and the seed script connect with a superuser role (`dm_admin`)
that bypasses RLS. This is the Postgres `BYPASSRLS` privilege:

```sql
-- One-time setup in migrations
CREATE ROLE dm_admin WITH BYPASSRLS;
GRANT dm_admin TO the_app_user;  -- app user can SET ROLE
```

Background Celery tasks that span institutions (e.g., a metrics aggregation job)
call `SET ROLE dm_admin` at session start. They are responsible for their own
`institution_id` scoping in application code — there is no RLS safety net.
Document this clearly in any Celery task that bypasses RLS.

The `scripts/seed.py` bootstrap script connects as `dm_admin` (or the Postgres
superuser during initial setup) so it can insert the first institution row
without an `app.institution_id` setting. See Part 3 for the audit interaction.

#### 1.4 Schema delta for audit_logs

The current `audit_logs` table (`migrations/001_init_schema.sql`) is missing
`institution_id` and `actor_id`, and uses `user_id` + `changes` columns that
differ from what `write_audit()` in `src/shared/security/audit.py` actually
inserts. A migration is required to align the schema with the existing
`write_audit()` implementation.

Current schema:

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id UUID NOT NULL,
    changes JSONB,
    ip_address VARCHAR(50),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Required schema (to match `write_audit()` and this RFC's taxonomy):

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    -- NULL for system actors (seed script, background jobs)
    institution_id UUID REFERENCES institutions(id) ON DELETE CASCADE NOT NULL,
    action VARCHAR(100) NOT NULL,
    -- format: "{resource_type}.{verb}" e.g. "user.registered"
    resource_type VARCHAR(100) NOT NULL,
    resource_id UUID,
    -- nullable: system events (e.g. institution.created) have no pre-existing resource
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_actor ON audit_logs(actor_id);
CREATE INDEX idx_audit_logs_institution ON audit_logs(institution_id, created_at DESC);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_logs_action ON audit_logs(action, created_at DESC);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at DESC);
```

An Alembic migration must:
1. Rename `user_id` → `actor_id`.
2. Add `institution_id UUID NOT NULL` (backfill with the single existing institution for any existing rows, or delete the rows if the table is always empty in dev).
3. Drop `changes`, `ip_address`, `user_agent`.
4. Change `resource_id` to nullable.
5. Rename `changes` data into `metadata` if any rows exist.

Migration file: `migrations/versions/0002_align_audit_logs.py`.

---

### Part 2 — Audit Event Taxonomy

#### 2.1 Canonical event shape

Every `write_audit()` call uses these fields:

| Field | Type | Nullable | Rule |
|---|---|---|---|
| `actor_id` | `uuid.UUID \| None` | Yes | NULL only for system actors |
| `institution_id` | `uuid.UUID` | No | Always required; never inferred from actor |
| `action` | `AuditEvent` | No | Must be a named member of `AuditEvent` enum |
| `resource_type` | `str` | No | Singular noun: `"user"`, `"group"`, `"message"` |
| `resource_id` | `uuid.UUID \| None` | Yes | NULL only when the resource doesn't exist yet at audit time (e.g. `institution.created` before the row is committed) |
| `metadata` | `dict[str, Any]` | — | Small, structured, no PII unless required. Max ~20 keys |
| `created_at` | timestamptz | — | Set server-side by Postgres `NOW()` |

#### 2.2 Action naming convention

Actions use dot-separated `{noun}.{verb}` format. The noun matches
`resource_type`. Verbs are past-participle or state-change words. Keep the list
stable — operators build queries and dashboards against these strings.

#### 2.3 Canonical AuditEvent enum

The full initial set (extends `src/shared/security/audit.py`):

```python
class AuditEvent(StrEnum):
    # --- Auth ---
    INSTITUTION_CREATED = "institution.created"
    INVITATION_SENT = "invitation.sent"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_EXPIRED = "invitation.expired"       # background job
    USER_REGISTERED = "user.registered"
    USER_LOGIN_SUCCESS = "user.login.success"
    USER_LOGIN_FAILURE = "user.login.failure"       # actor_id may be NULL (unknown user)
    USER_PASSWORD_CHANGED = "user.password.changed"
    USER_DELETED = "user.deleted"
    TOKEN_REFRESHED = "token.refreshed"
    TOKEN_REVOKED = "token.revoked"

    # --- ACL ---
    ROLE_CREATED = "role.created"
    ROLE_DELETED = "role.deleted"
    ROLE_GRANTED = "acl.role.granted"
    ROLE_REVOKED = "acl.role.revoked"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_REVOKED = "permission.revoked"

    # --- Groups ---
    GROUP_CREATED = "group.created"
    GROUP_UPDATED = "group.updated"
    GROUP_ARCHIVED = "group.archived"
    GROUP_MEMBER_ADDED = "group.member.added"
    GROUP_MEMBER_REMOVED = "group.member.removed"
    GROUP_MEMBER_ROLE_CHANGED = "group.member.role_changed"
    TOPIC_CREATED = "topic.created"
    TOPIC_DELETED = "topic.deleted"

    # --- Chat ---
    MESSAGE_SENT = "message.sent"
    MESSAGE_EDITED = "message.edited"
    MESSAGE_DELETED = "message.deleted"

    # --- Media ---
    MEDIA_UPLOADED = "media.uploaded"
    MEDIA_DELETED = "media.deleted"

    # --- Notifications ---
    NOTIFICATION_TOKEN_REGISTERED = "notification.token.registered"
    NOTIFICATION_TOKEN_REVOKED = "notification.token.revoked"
```

**Rule: no new audit event may be added to a module without also adding it to
this enum in `src/shared/security/audit.py`.** The enum is the single source
of truth; raw string actions are prohibited.

#### 2.4 Placement rule — same transaction as the mutation

The `write_audit()` call goes inside the service method, inside the same
`AsyncSession` transaction as the mutation. This guarantees audit rows are
committed atomically with the data change:

```python
async def register_user(
    db: AsyncSession,
    institution_id: uuid.UUID,
    email: str,
    password: str,
    full_name: str,
    invitation: UserInvitation,
) -> User:
    """Register a new user, consuming the invitation token.

    Writes one audit row inside the same transaction as the INSERT.

    Args:
        db: Async database session (transaction managed by caller).
        institution_id: The institution this user belongs to.
        email: User's email address (unique within institution).
        password: Plaintext password (hashed in this method).
        full_name: Display name.
        invitation: The accepted UserInvitation ORM object.

    Returns:
        The newly created User.

    Raises:
        ConflictError: If email is already registered in this institution.
    """
    user = User(
        institution_id=institution_id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
    )
    db.add(user)
    await db.flush()  # assigns user.id without committing

    # Mark invitation consumed
    invitation.accepted_at = datetime.utcnow()
    invitation.accepted_user_id = user.id

    # Audit — same transaction
    await write_audit(
        db,
        actor_id=user.id,
        institution_id=institution_id,
        action=AuditEvent.USER_REGISTERED,
        resource_type="user",
        resource_id=user.id,
        metadata={"email": email, "invited_by": str(invitation.invited_by_user_id)},
    )
    # Caller commits the transaction; audit row commits with it.
    return user
```

`write_audit` swallows exceptions internally (see existing implementation) — an
audit write failure must never fail the user's mutation. The exception is logged
as `audit_write_failed` with structlog.

#### 2.5 Idempotency replay rule

When the idempotency layer (see `docs/design/idempotency.md`) returns a cached
response, the service method is NOT called again — no second mutation, no second
audit write. This is the correct behaviour by construction: the service code
that contains `write_audit()` is simply not executed on a replay. Module authors
must not put `write_audit()` in the route layer where it could fire on replays.

#### 2.6 Metadata guidelines

Keep `metadata` small and structured. Avoid PII where possible; include it only
when it is required for operator investigation (e.g., `email` on
`user.registered` is needed to trace "who registered this account"). Never
store passwords, tokens, or encryption keys in metadata. The privacy-erasure
RFC (`docs/design/privacy-erasure.md`) will address how PII in audit metadata
is handled on user deletion — expect a redaction pass, not deletion of audit
rows.

Recommended keys by event:

| Event | Suggested metadata keys |
|---|---|
| `user.registered` | `{"email": "…", "invited_by": "uuid"}` |
| `user.login.success` | `{"ip": "…"}` (from request if available) |
| `user.login.failure` | `{"email_attempted": "…", "reason": "bad_password"}` |
| `invitation.sent` | `{"to_email": "…"}` |
| `invitation.accepted` | `{"invitation_id": "uuid"}` |
| `group.created` | `{"name": "…", "mode": "simple|topics"}` |
| `group.member.added` | `{"target_user_id": "uuid", "role": "member"}` |
| `message.deleted` | `{"conversation_id": "uuid", "soft_delete": true}` |
| `acl.role.granted` | `{"target_user_id": "uuid", "role_name": "…"}` |
| `media.uploaded` | `{"file_type": "…", "file_size_bytes": 12345}` |

---

### Part 3 — Bootstrap / System Actors

When `scripts/seed.py` creates the first institution and admin user, there is
no authenticated user and no JWT. The audit calls use:

```python
await write_audit(
    db,
    actor_id=None,            # No authenticated user
    institution_id=institution.id,
    action=AuditEvent.INSTITUTION_CREATED,
    resource_type="institution",
    resource_id=institution.id,
    metadata={"system_actor": "seed", "institution_name": institution.name},
)
```

`actor_id=None` maps to `NULL` in the `audit_logs.actor_id` column (the
FK is nullable). Operators querying `audit_logs` filter on
`actor_id IS NULL AND metadata->>'system_actor' IS NOT NULL` to distinguish
system events from human events.

Background Celery tasks follow the same pattern: `actor_id=None` if no user
triggered the task (e.g., an invitation expiry sweep), or the originating
`user.id` if the task was enqueued as a side-effect of a user action (e.g.,
a push notification task triggered by a message send — use the sender's ID).

**Seed script Postgres connection:** the seed script connects as `dm_admin`
(see 1.3 for the `BYPASSRLS` setup). It does NOT call
`SET app.institution_id` because it is creating the institution, not scoped to
one. All seed SQL uses `dm_admin` privileges; RLS does not apply.

---

### Alternatives Considered

**Single-layer app filter only (no RLS)**
Rejected. A single ORM bug silently exposes another institution's data with no
backstop. RLS as defence-in-depth is ~10 lines of SQL per table and an
observable one-time setup cost. The payoff is catastrophic-bug prevention.

**RLS on all tables including transitively-scoped ones**
Rejected for transitively-scoped tables (messages, media_files, etc.). Writing
an RLS policy that joins back through three FKs to reach `institution_id` adds
query complexity and makes EXPLAIN plans harder to read. App-layer scoping on
the parent table is sufficient and cheaper to audit.

**Store audit logs in a separate database**
Overkill for 5k users. A single Postgres instance is correct. Revisit if
audit_logs exceeds 10M rows or if compliance requires tamper-evident storage.

**Per-column RLS granularity**
Not needed. Institution-level row isolation is the correct granularity for a
single-tenant-per-deployment product.

---

## Consequences

### Positive

- A module author cannot accidentally omit the tenant filter — it is visible as
  a missing call in code review.
- RLS provides a database-level backstop independent of application code.
- Audit log is populated consistently from day one; Gap A is closed.
- Idempotency replays never double-log.
- System actors are unambiguously identified in audit queries.
- The event enum prevents typos in action strings and is greppable.

### Negative / Tradeoffs

- `get_db()` executes one extra SQL statement (`set_config(...)`) per request.
  At 5k users this is negligible (microseconds per call); revisit if profiling
  shows it in the hot path.
- The `audit_logs` schema migration is a breaking change to the existing table;
  requires a tested downgrade path.
- Celery tasks that bypass RLS carry full data-access responsibility — no
  safety net. Compensated by documentation and mandatory code-review checklist.
- `write_audit()` is best-effort (swallows exceptions). Audit completeness is
  not guaranteed under Postgres failure. Monitoring via `audit_write_failed` log
  counter is the detection mechanism.

### Future Work

- When `audit_logs` exceeds 5M rows, evaluate partitioning by `created_at`
  (month-range). The schema is already append-only which makes partitioning
  trivially safe.
- If a compliance requirement for tamper-evidence arises, evaluate appending
  a row hash chain or shipping audit rows to an immutable store (e.g., AWS
  CloudTrail, a WORM S3 bucket).
- `privacy-erasure.md` will define the policy for redacting PII fields in
  `audit_logs.metadata` after a user deletion request. This RFC defers to that
  document.
- **Open question:** should the ops team have a direct read-only Postgres role
  that can query `audit_logs` without going through the API? RLS is not applied
  to `audit_logs`, so a `dm_auditor` role with `SELECT` on `audit_logs` only
  would be sufficient and safe. Decide before first production deploy.

## Cross-references

- Related RFC: `docs/design/idempotency.md` — idempotency replay MUST NOT
  re-emit audit rows; this is guaranteed by placing `write_audit()` in the
  service layer, not the route layer.
- Related RFC: `docs/design/privacy-erasure.md` — PII in `audit_logs.metadata`
  is subject to redaction on user erasure; that RFC defines the retention
  policy.
- Consumed by: `src/modules/users/`, `src/modules/acl/`, `src/modules/groups/`,
  `src/modules/chat/`, `src/modules/media/`, `src/modules/notifications/` —
  all Stage-4 modules must follow this pattern.
- Existing primitives: `src/shared/security/tenant.py` (already implemented),
  `src/shared/security/audit.py` (already implemented, `write_audit()` ready
  to call, `AuditEvent` enum to be extended per 2.3).
- Schema source: `migrations/001_init_schema.sql` — `audit_logs` table needs
  the delta migration described in 1.4.
- Gap reference: `docs/MANUAL_SMOKE.md` Gap A — this RFC's implementation of
  `write_audit()` calls in every service mutation closes that gap.

---

## Cross-Tenant Fuzz Test Fixture

Every Stage-4 module includes at least one cross-tenant fuzz test. Use this
fixture pattern from `tests/conftest.py`:

```python
import pytest
import uuid
from httpx import AsyncClient

@pytest.fixture
async def two_institutions(db_session):
    """Create two isolated institutions with one user each.

    Yields:
        Tuple of (user_a_headers, user_b_headers, institution_a_id, institution_b_id)
        where user_a belongs to institution_a and user_b belongs to institution_b.
    """
    from tests.factories import InstitutionFactory, UserFactory
    inst_a = await InstitutionFactory.create(db_session)
    inst_b = await InstitutionFactory.create(db_session)
    user_a = await UserFactory.create(db_session, institution_id=inst_a.id)
    user_b = await UserFactory.create(db_session, institution_id=inst_b.id)
    headers_a = await make_auth_headers(user_a)
    headers_b = await make_auth_headers(user_b)
    return headers_a, headers_b, inst_a.id, inst_b.id


# Example usage in a module test:
async def test_group_cross_tenant_returns_404(
    client: AsyncClient,
    two_institutions,
):
    """User from institution B cannot read institution A's group — gets 404."""
    headers_a, headers_b, inst_a_id, _ = two_institutions

    # Create a group in institution A
    resp = await client.post(
        "/api/v1/groups",
        json={"name": "Secret Group A"},
        headers=headers_a,
    )
    assert resp.status_code == 201
    group_id = resp.json()["data"]["id"]

    # User B attempts to access institution A's group
    resp = await client.get(f"/api/v1/groups/{group_id}", headers=headers_b)
    assert resp.status_code == 404  # NOT 403, NOT 500
```

**Rule:** the cross-tenant fuzz test must assert `404`, never `403` or `500`.
`403` would confirm the resource exists. `500` indicates a bug that bypassed
both app-layer and RLS.
