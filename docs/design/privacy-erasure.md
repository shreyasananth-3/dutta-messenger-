---
title: "Privacy Erasure — Right to Access, Rectification, and Erasure"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - src/modules/users/
  - src/modules/chat/
  - src/modules/media/
  - src/modules/auth/
---

# Privacy Erasure — Right to Access, Rectification, and Erasure

## Context

DuttaMessenger is deployed to Indian schools (1000–5000 users per deployment, single-tenant
per school). The **DPDP Act 2023** (Digital Personal Data Protection Act) is the primary
legal obligation. GDPR is applied as a second layer for good engineering practice and
future exportability, but DPDP is the controlling statute.

This RFC decides the erasure semantics before Stage 4 module code is written. Without a
clear decision, the users module, chat module, and media module will make conflicting
assumptions about what "delete user" means for persistent conversation history.

---

## Decision

Users are **tombstoned, never hard-deleted**. All PII fields on the `users` row are
nulled or hashed at erasure time. Messages are **anonymised in place by default** (content
preserved, sender identity removed); users may optionally elect content erasure at
erasure-request time. Media files owned by the erased user enter a **30-day recycle bin**
before permanent S3 purge. Audit logs survive all user erasure for a **7-year legal hold**.

---

## Details

### Scope

This RFC covers:

- `GET /api/v1/me/export` — Subject Access Request (SAR) bundle endpoint.
- `DELETE /api/v1/me` — User self-erasure endpoint.
- `POST /api/v1/admin/users/{id}/export` — Institution-admin data access.
- Tombstone semantics for `users`, `messages`, and `media_files` tables.
- Retention defaults for all stored data categories.
- Institution-level offboarding path.
- Interaction with cold-storage archives (cross-reference `message-partitioning.md`).
- DPDP-specific callouts requiring human/legal signoff.

### Non-goals

- End-to-end encryption of message content at rest — deferred per the project plan.
- Granular per-message erasure initiated by the user outside the erasure flow — message
  soft-delete (the existing `DELETE /api/v1/chat/messages/{id}`) already handles this.
- Cross-tenant scenarios — each deployment is a single tenant, so "tenant purge" is the
  institution offboarding path described below.
- Formal legal opinion on DPDP obligations — this RFC flags the obligations; legal counsel
  provides the opinion.

### Decision matrix

| Request type | Endpoint | Server action | Data outcome |
|---|---|---|---|
| SAR (self) | `GET /api/v1/me/export` | Queue Celery task | Async bundle: JSON profile + message list + signed media URLs; delivered in-app or email link |
| SAR (admin) | `POST /api/v1/admin/users/{id}/export` | Queue Celery task, audit-log | Same bundle; admin receives download link |
| Rectification | `PATCH /api/v1/users/me` | Synchronous update | Profile fields updated in-place; no new endpoint needed |
| Erasure (self) | `DELETE /api/v1/me` | Queue Celery task | Tombstone user; anonymise/erase messages; recycle-bin media |
| Institution offboard | Internal admin action | Queue Celery task (bulk) | 30-day grace export window, then full tenant row purge |

---

### Right to Access — Subject Access Request (SAR)

#### Endpoint

```
GET /api/v1/me/export
```

**Rate limit:** one request per user per 24 hours (enforced in Redis with key
`sar:cooldown:{user_id}`, TTL 86400 s). A second request within the window returns
HTTP 429 with `error_code: SAR_RATE_LIMITED` and the `Retry-After` header set to the
remaining seconds. This aligns with `idempotency.md` — the SAR endpoint is idempotent
within the window (repeat calls return the same in-flight or completed job ID).

#### Flow

1. Route handler validates auth, checks cooldown key in Redis.
2. Creates a `DataExportJob` record in Postgres (`status = pending`).
3. Enqueues `celery_tasks.build_sar_bundle(job_id=..., user_id=...)`.
4. Returns HTTP 202 with `{ "data": { "job_id": "...", "estimated_minutes": 5 } }`.
5. Celery worker assembles the bundle (see format below).
6. On completion: worker stores the bundle in a time-limited S3 presigned URL (expiry:
   48 hours), updates `DataExportJob.status = done`, and sends an in-app notification
   (plus optional email) with the download link.
7. Client polls `GET /api/v1/me/export/status` or receives a WebSocket push event
   `sar.ready`.

#### Bundle format (high level)

```json
{
  "generated_at": "2026-04-18T10:00:00Z",
  "user_id": "<uuid>",
  "profile": {
    "display_name": "...",
    "email": "...",
    "phone": "...",
    "bio": "...",
    "created_at": "...",
    "last_seen_at": "..."
  },
  "settings": { ... },
  "conversations": [
    {
      "conversation_id": "...",
      "type": "dm | group | topic",
      "messages": [
        {
          "message_id": "...",
          "sent_at": "...",
          "content": "...",
          "media_urls": ["<signed-url-48h>", ...]
        }
      ]
    }
  ],
  "media_files": [
    {
      "media_file_id": "...",
      "file_name": "...",
      "mime_type": "...",
      "uploaded_at": "...",
      "download_url": "<signed-url-48h>"
    }
  ]
}
```

Only messages where `sender_id = requesting_user_id` are included. The bundle does not
include other users' messages in shared conversations (those belong to other data subjects).

---

### Right to Rectification

No new endpoint is required. The existing `PATCH /api/v1/users/me` endpoint (users module)
handles profile field updates. Updated fields are timestamped via `users.updated_at`. This
satisfies DPDP's correction obligation. Call out in the users module API.md that this
doubles as the DPDP rectification mechanism.

---

### Right to Erasure — User Tombstoning

#### User row

The `users` row is **never hard-deleted**. Rationale: messages have `sender_id` FK
references; hard-deleting the user row would orphan or cascade-null those FKs in ways that
destroy conversation context for remaining members. The DPDP Act requires erasure of
**personal data**, not erasure of all records that ever referenced the person.

On erasure:

| `users` column | Action |
|---|---|
| `email` | Set to `NULL` |
| `phone` | Set to `NULL` |
| `full_name` | Set to `NULL` |
| `display_name` | Set to `"Former member"` |
| `avatar_url` | Set to `NULL`; queued for S3 deletion |
| `bio` | Set to `NULL` |
| `deleted_at` | Set to `NOW()` |
| `hashed_identity` | Set to `sha256(user_id::text)` — keeps a non-reversible token for de-duplication and audit cross-reference without storing PII |

All other columns (`id`, `institution_id`, `created_at`, etc.) are retained — these are
operational metadata, not personal data.

Auth tokens for the user are revoked immediately (all refresh tokens for this `user_id`
are soft-deleted in `refresh_tokens`).

Redis presence key `user:online:{user_id}` is deleted.

#### Messages — two erasure modes

The requesting user chooses at erasure time via a request body flag:

```
DELETE /api/v1/me
Content-Type: application/json

{ "message_erasure_mode": "anonymise" | "erase_content" }
```

Default (omitted): `anonymise`.

**Mode A — anonymise (default)**

- `messages.sender_id` is kept pointing to the tombstoned user row.
- `messages.content` is unchanged.
- `messages.metadata` edit history is preserved.
- The UI renders the sender as "Former member" (resolved from the tombstoned
  `display_name`). Conversation context is fully preserved for remaining members.

**Mode B — erase_content**

- `messages.content` is replaced with `"[message deleted by user]"`.
- `messages.sender_id` is kept (FK to tombstoned row; not PII since the user row is
  already anonymised at this point).
- Reactions, read receipts, and pinned status are preserved as counts/flags — these
  are not personal data.
- `messages.metadata` edit history is cleared (JSONB set to `{}`).
- Media attachments in `message_media` are scheduled for recycle-bin (see Media section).

Both modes are processed asynchronously via a Celery task (`celery_tasks.erase_user_data`).
The task is idempotent: it records a checkpoint in Redis so a retry after failure resumes
from where it left off rather than re-processing already-anonymised messages.

#### Why not hard-delete messages?

Hard-deleting messages breaks conversation coherence for other participants — a group chat
with 50 messages becomes a conversation with gaps that make no sense. The UK ICO and EU
EDPB both accept anonymisation-in-place as equivalent to erasure when the data can no
longer be attributed to an identified natural person. The tombstoned row contains no
reversible PII after the erasure action, so DPDP's requirement is met.

---

### Media — 30-Day Recycle Bin

Media files uploaded by the erased user are **not immediately purged from S3**. Rationale:
an erasure request may be fraudulent (e.g., a student submitting evidence to a teacher and
then attempting to erase it). Admins need a contestation window.

1. On erasure: `media_files.recycle_bin_at = NOW()` is set for all files owned by the user.
2. Files remain accessible via existing signed download URLs (already time-limited to 1h)
   for 30 days after `recycle_bin_at`.
3. After 30 days: a nightly Celery beat task (`celery_tasks.purge_recycle_bin`) deletes
   S3 objects and sets `media_files.deleted_at`. The `media_files` row is then
   hard-deleted (no business value in retaining the metadata row).
4. Institution admins can accelerate the purge (set `recycle_bin_at` to a past date) or
   restore a file (clear `recycle_bin_at`) during the 30-day window.

The `media_files` table requires two new columns:

```sql
ALTER TABLE media_files
    ADD COLUMN recycle_bin_at TIMESTAMPTZ,
    ADD COLUMN deleted_at     TIMESTAMPTZ;

-- Supports: nightly recycle-bin purge task
CREATE INDEX idx_media_files_recycle_bin
    ON media_files (recycle_bin_at)
    WHERE recycle_bin_at IS NOT NULL AND deleted_at IS NULL;
```

---

### Audit Logs — 7-Year Legal Hold

Audit log entries are **never subject to user erasure**. They survive user tombstoning
regardless of erasure mode chosen.

Retention period: **7 years from creation date**. Justification: Indian corporate
and tax law (Income Tax Act, Companies Act) typically requires financial and operational
records to be retained for 7 years. School boards may be subject to state-level record
retention rules that also fall in the 5–7 year range. 7 years is conservative and safe.

The audit log schema is owned by `tenant-isolation.md`. This RFC only mandates the
retention constraint. Implementation: a nightly Celery beat task purges `audit_logs` rows
where `created_at < NOW() - INTERVAL '7 years'`.

Audit entries are emitted for:
- Erasure request received (`user.erasure_requested`)
- Erasure task started (`user.erasure_started`)
- Erasure task completed (`user.erasure_completed`)
- SAR export requested (`user.sar_requested`)
- SAR export completed and downloaded (`user.sar_downloaded`)
- Admin export requested (`admin.user_export_requested`)

---

### Institution-Admin Data Access

Institution admins may request a data export for any user in their institution (use case:
safeguarding a student, responding to a parental request under DPDP's parental consent
provisions for minors).

```
POST /api/v1/admin/users/{id}/export
```

- Requires permission: `institution.manage_users`.
- Audit-logged: emits `admin.user_export_requested` with `{ admin_id, target_user_id,
  reason }` (reason is a required request body field — forces the admin to state why).
- Rate-limited: 10 admin exports per institution per day (Redis counter
  `admin_sar:daily:{institution_id}`, TTL until midnight).
- Bundle format: same as self-SAR, but includes ALL messages sent by the user (not just
  sent messages — also metadata about conversations the user participated in, without
  other users' message content).
- Delivery: presigned S3 URL, 24h expiry, returned to the admin synchronously after
  task completion (or via webhook if async duration exceeds 30s).

---

### Institution Offboarding — Bulk Erasure

When an entire institution is offboarded (contract ends, school migrates away):

1. Institution owner submits an offboarding request to the platform operator.
2. Platform operator sets `institutions.offboarding_at = NOW() + 30 days`.
3. For 30 days: institution admins can export data via `POST /api/v1/admin/bulk-export`
   (triggers a full institution SAR bundle).
4. After 30 days: a Celery task (`celery_tasks.purge_institution`) executes:
   - Tombstones all users in the institution (same PII-nulling as individual erasure).
   - Deletes all `media_files` from S3.
   - Hard-deletes all conversation, message, and group rows for the institution.
   - Sets `institutions.deleted_at`.
   - Retains `audit_logs` for the 7-year window (they are not institution-scoped for
     deletion — they are evidence of what happened).
5. Bulk purge is irreversible. The platform operator must confirm via a two-step process
   before the Celery task is enqueued.

---

### Interaction with Cold-Storage Archives (message-partitioning.md)

When messages older than the archival threshold (defined in `message-partitioning.md`)
are moved to cold storage (e.g., Postgres table partitions or S3 Parquet files), an
erasure request for a user must reach those archives.

Design decision: **do not block the erasure flow on archive processing**. Instead:

1. The primary erasure task anonymises/erases all hot-storage messages and marks the user
   as `deleted_at` on the `users` row.
2. A separate async task (`celery_tasks.erase_user_in_archives`) is enqueued with the
   `user_id` and `erasure_mode`.
3. The archive task processes partitions sequentially, updating rows where
   `sender_id = user_id`. For Parquet-based cold storage, this requires a rewrite of the
   affected partition files — acceptable because archives are accessed rarely.
4. The `DataExportJob` record tracks both tasks; `status = complete` only after both
   finish.
5. The DPDP Act does not specify a sub-day deadline for archive erasure — 30 days total
   is acceptable. Cross-reference `slo.md` for the erasure SLA target.

---

### Retention Defaults

| Data category | Default retention | Justification |
|---|---|---|
| Messages | Indefinite within institution | Teachers and admins need historical context |
| `audit_logs` | 7 years | Indian tax / corporate law retention baseline |
| `refresh_tokens` (expired/revoked) | 30 days after expiry/revocation, then purged | No business value; reduces PII surface |
| Media files (active) | Follows message retention | File belongs to the message |
| Media files (recycle bin) | 30 days after `recycle_bin_at` | Admin contestation window |
| SAR bundle on S3 | 48 hours after generation | Minimise PII exposure; user re-requests if needed |
| `DataExportJob` rows | 90 days | Audit trail of SAR requests; after 90 days hard-delete |

---

### Implementation Sketch

#### New/modified tables

```sql
-- users table: add tombstone columns (migration required)
ALTER TABLE users
    ADD COLUMN deleted_at      TIMESTAMPTZ,
    ADD COLUMN hashed_identity TEXT;       -- sha256(user_id) post-erasure

-- media_files table: add recycle bin columns (migration required)
ALTER TABLE media_files
    ADD COLUMN recycle_bin_at TIMESTAMPTZ,
    ADD COLUMN deleted_at     TIMESTAMPTZ;

-- New table: data export jobs
CREATE TABLE data_export_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id),
    requested_by    UUID NOT NULL REFERENCES users(id),  -- self or admin
    job_type        VARCHAR(20) NOT NULL
                    CHECK (job_type IN ('self_sar', 'admin_sar', 'erasure')),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed')),
    bundle_url      TEXT,           -- presigned S3 URL when done
    bundle_expires_at TIMESTAMPTZ,
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_data_export_jobs_user
    ON data_export_jobs (user_id, created_at DESC);
```

#### Key files

- `src/modules/users/routes/erasure.py` — `GET /api/v1/me/export`,
  `DELETE /api/v1/me`, `GET /api/v1/me/export/status`
- `src/modules/users/routes/admin_export.py` — `POST /api/v1/admin/users/{id}/export`
- `src/modules/users/services/erasure_service.py` — tombstoning logic, PII nulling
- `src/modules/users/services/sar_service.py` — bundle assembly
- `src/shared/celery_tasks/erasure.py` — `erase_user_data`, `erase_user_in_archives`,
  `build_sar_bundle`, `purge_recycle_bin`, `purge_institution`
- `migrations/versions/XXXX_add_tombstone_columns.py`
- `migrations/versions/XXXX_add_data_export_jobs.py`

#### Pseudocode: erasure task

```python
async def erase_user_data(
    user_id: uuid.UUID,
    message_erasure_mode: str,  # "anonymise" | "erase_content"
    db: AsyncSession,
) -> None:
    # 1. Null PII on users row
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            email=None, phone=None, full_name=None,
            display_name="Former member", avatar_url=None, bio=None,
            deleted_at=func.now(),
            hashed_identity=func.encode(
                func.digest(cast(user_id, Text), "sha256"), "hex"
            ),
        )
    )
    # 2. Revoke refresh tokens
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .values(revoked_at=func.now())
    )
    # 3. Erase/anonymise messages if mode B
    if message_erasure_mode == "erase_content":
        await db.execute(
            update(Message)
            .where(Message.sender_id == user_id, Message.deleted_at.is_(None))
            .values(content="[message deleted by user]", metadata={})
        )
    # 4. Recycle-bin media
    await db.execute(
        update(MediaFile)
        .where(MediaFile.uploader_id == user_id, MediaFile.recycle_bin_at.is_(None))
        .values(recycle_bin_at=func.now())
    )
    # 5. Emit audit log (see `tenant-isolation.md` for the write_audit taxonomy)
    await write_audit(
        db=db,
        action="user.erasure_completed",
        resource_type="user",
        resource_id=str(user_id),
        actor_id=str(user_id),
        institution_id=str(institution_id),
        metadata={"mode": mode},  # "anonymise" or "erase_content"
    )
```

---

### Alternatives Considered

1. **Hard-delete the user row** — Rejected. Cascades to `sender_id` FK on `messages`
   either break referential integrity or force cascade-null on all message rows, which has
   the same effect as Mode B erasure but without the user's informed choice. It also makes
   it impossible to cross-reference audit logs against the user who performed actions.

2. **Hard-delete messages in Mode B** — Rejected. Deleting message rows removes them
   from conversation flow, creating unexplainable gaps for other participants. Replacing
   content with a tombstone string is the same approach already used for `DELETE
   /api/v1/chat/messages/{id}` — consistent with existing behaviour.

3. **No recycle bin for media (immediate S3 purge)** — Rejected. A student could share
   assignment evidence in a group chat and then immediately request erasure to destroy it.
   The 30-day contestation window gives admins and parents time to act.

---

## DPDP Act 2023 — Specific Callouts Requiring Human/Legal Signoff

The following items are **flagged here for legal review** and are NOT resolved by this RFC.
Do not ship any of these without legal counsel sign-off.

1. **Child data (under 18) — parental consent requirement.** DPDP §9 requires verifiable
   parental consent before processing data of minors. Schools are the primary deployment
   target. The registration/invite flow must implement an age gate and a parental consent
   collection mechanism. This is not in any current module. Needs a legal opinion on what
   constitutes "verifiable" consent in the Indian school context.

2. **Breach notification — 72 hours.** DPDP requires the Data Fiduciary (the school /
   platform operator) to notify the Data Protection Board and affected users within 72
   hours of a data breach. The system currently has no breach-detection or notification
   workflow. A minimal implementation (Sentry alert → manual email) may satisfy the
   obligation; legal counsel to confirm.

3. **Purpose limitation and consent withdrawal.** DPDP requires that data is only
   processed for the stated purpose the user consented to. When a user exercises erasure,
   the remaining `hashed_identity` and audit logs must be defensible as "not personal
   data" under DPDP. Legal counsel to confirm the sha256(user_id) token qualifies as
   anonymised under DPDP's definition.

4. **Grievance officer designation.** DPDP §13 requires every Data Fiduciary to designate
   a Grievance Officer. The platform should surface the officer's name and contact in the
   app. Engineering concern: add a `GRIEVANCE_OFFICER_NAME` and `GRIEVANCE_OFFICER_EMAIL`
   config field (src/config.py) surfaced via `GET /api/v1/legal/grievance-officer`. Legal
   to provide the officer details.

5. **Erasure SLA.** DPDP does not specify a fixed erasure deadline (unlike GDPR's 30
   days). The `slo.md` RFC proposes 30 days as a conservative target. Legal to confirm
   this is defensible and whether the school's own privacy policy commits to a shorter
   window.

---

## Consequences

### Positive

- DPDP compliance path is explicit and implementable without breaking existing chat/media
  module designs.
- Conversation integrity is preserved for remaining participants — tombstone approach is
  consistent with how WhatsApp, Telegram, and Signal handle departed users.
- 7-year audit log retention protects the institution against legal challenges.
- 30-day media recycle bin is a safeguarding-friendly design for school deployments.
- Erasure is fully async (Celery) — no synchronous blocking of the API under load.

### Negative / Tradeoffs

- Every query that renders user display names must handle the tombstone case (`deleted_at
  IS NOT NULL → display "Former member"`). This is a cross-cutting UI concern.
- The `data_export_jobs` table adds a new migration and a new Celery queue.
- Archive erasure (`message-partitioning.md` interaction) may take longer than hot-storage
  erasure — the SLA clock includes archive processing time.
- The 30-day recycle bin means a user's media remains on S3 for up to 30 days after they
  requested erasure. This must be disclosed in the platform's privacy notice.

### Future Work

- When messages table exceeds 10M rows (see `message-partitioning.md`), revisit the
  archive erasure task performance — partition rewrites may need to be parallelised.
- If DPDP implementing regulations introduce a specific erasure deadline, update `slo.md`
  and tighten the Celery task SLA accordingly.
- Consider an in-app "erasure status" screen so users can track progress without polling
  the API.

---

## Cross-references

- Related RFC: [idempotency.md](idempotency.md) — SAR endpoint is rate-limited per day
  (idempotent within the window; second call returns same job ID).
- Related RFC: [tenant-isolation.md](tenant-isolation.md) — audit_logs schema is defined
  there; this RFC mandates a 7-year retention constraint on those rows.
- Related RFC: [message-partitioning.md](message-partitioning.md) — cold-storage archives
  complicate erasure; async archive erasure task is the bridge.
- Related RFC: [slo.md](slo.md) — erasure completion SLA target (proposed: 30 days total,
  including archive processing).
- Related RFC: [api-versioning.md](api-versioning.md) — erasure and SAR endpoints use the
  canonical error envelope defined there.
- Consumed by: `src/modules/users/` — erasure and SAR endpoints implemented here.
- Consumed by: `src/modules/chat/` — message anonymisation/erasure logic.
- Consumed by: `src/modules/media/` — recycle bin and S3 purge logic.
- Reference doc: `reference-docs/modules/users/MODULE.md` — users module owns the
  `users` table; erasure extends it with `deleted_at` and `hashed_identity` columns.
- Reference doc: `reference-docs/modules/chat/SCHEMA.sql` — `messages.sender_id`
  is `ON DELETE SET NULL`; tombstone approach keeps FK valid with the anonymised user row.
- Reference doc: `reference-docs/modules/media/MODULE.md` — media security model;
  recycle bin extends it with `recycle_bin_at` and `deleted_at` columns.
