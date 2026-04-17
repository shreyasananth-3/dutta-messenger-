---
title: "Message Table Partitioning and Archival Strategy"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - src/modules/chat/
  - migrations/
---

# Message Table Partitioning and Archival Strategy

## Context

The `messages` table is the highest-write table in DuttaMessenger. At steady state for a
5 000-user deployment, row counts are manageable for years on a plain heap table with good
indexes. However, the chat module (Stage 4d) must be built with partitioning awareness so
the eventual migration to a partitioned table is a non-event — not a schema rewrite.

This RFC specifies:

1. The exact row-count / disk-size trigger that kicks off partitioning work.
2. The chosen partition strategy and migration path when that trigger is hit.
3. The archival policy for old partitions (cold storage after 18 months).
4. The async "request archive" flow for users who need old messages.
5. How tombstoned users (soft-deleted) are handled across partitions.

Without this decision on paper now, the Stage-4 chat author will make ad-hoc choices that
are expensive to undo later.

## Decision

Keep `messages` as a **plain, unpartitioned heap table** until it crosses **10 million rows
or 100 GB on disk, whichever occurs first**. When either threshold is hit, migrate to
`PARTITION BY RANGE (created_at)` with monthly child partitions, using `pg_partman` for
ongoing maintenance. Messages older than 18 months are archived to S3 (cold), and the
corresponding partition is detached from the live table.

## Details

### Scope

- The `messages` table defined in `reference-docs/modules/chat/SCHEMA.sql`.
- All indexes on `messages`.
- The archival Celery task and the "request archive" API flow.
- The tombstoning cross-reference for soft-deleted users inside archived rows.

### Non-goals

- Partitioning `conversations`, `conversation_members`, `message_reads`, or `message_media`
  — these tables grow slowly and do not need partitioning at this scale.
- Multi-region replication of archived data — out of scope for a single-VPS deployment.
- Real-time search inside archived partitions — archived messages are offline export only.
- Row-level security across partitions — see `docs/design/tenant-isolation.md`.
  RLS policies attached to the parent table propagate automatically to child partitions in
  Postgres 16; no extra work required here.

### Row-count trajectory

Activity levels assume a single 5 000-user institution. "Low" = 20 msgs/user/day,
"Medium" = 50 msgs/user/day (design assumption), "High" = 100 msgs/user/day.

| Activity level | msgs/day | msgs/month | Months to 10M rows | Years to 10M rows |
|----------------|----------|------------|--------------------|--------------------|
| Low (20/user/day) | 100 000 | 3 000 000 | ~83 months | ~6.9 years |
| Medium (50/user/day) | 250 000 | 7 500 000 | ~40 months | ~3.3 years |
| High (100/user/day) | 500 000 | 15 000 000 | ~20 months | ~1.7 years |

**Expected runway before trigger fires: 3–4 years at medium activity.**

Even at high activity, the table will remain fast with the existing indexes until the
trigger. Postgres heap tables with B-tree indexes handle tens of millions of rows
comfortably when queries are selective (conversation-scoped range scans, not full-table).

### Phase 0 — Pre-trigger (now through Stage 4)

No partitioning. Maintain these indexes exactly as defined in `SCHEMA.sql`:

```sql
-- Hot path: paginated message list
CREATE INDEX idx_messages_conversation_created
    ON messages (conversation_id, created_at DESC);

-- Idempotency
CREATE UNIQUE INDEX idx_messages_client_message_id
    ON messages (client_message_id);

-- Pinned messages per conversation
CREATE INDEX idx_messages_pinned
    ON messages (conversation_id, pinned_at DESC)
    WHERE pinned_at IS NOT NULL;

-- Unread count
CREATE INDEX idx_messages_conversation_sender_created
    ON messages (conversation_id, sender_id, created_at DESC)
    WHERE deleted_at IS NULL;
```

Add a monitoring query to `scripts/check_table_size.py` (run weekly via cron):

```python
SELECT
    pg_size_pretty(pg_total_relation_size('messages')) AS total_size,
    (SELECT COUNT(*) FROM messages)                    AS row_count;
```

Alert when row count > 8 000 000 (80% of trigger) or size > 80 GB.

### Phase 1 — Trigger fires: migrate to partitioned table

**Trigger condition** (either):
- `SELECT COUNT(*) FROM messages` exceeds 10 000 000, OR
- `pg_total_relation_size('messages')` exceeds 100 GB.

**Strategy**: `PARTITION BY RANGE (created_at)`, one child partition per calendar month.

```sql
-- New parent (no storage of its own)
CREATE TABLE messages_partitioned (
    LIKE messages INCLUDING ALL
) PARTITION BY RANGE (created_at);

-- Example monthly child partitions
CREATE TABLE messages_2024_01
    PARTITION OF messages_partitioned
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE messages_2024_02
    PARTITION OF messages_partitioned
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- ... and so on
```

**Indexes per partition**: Postgres propagates indexes defined on the parent to each child.
The existing four indexes survive automatically. The per-partition planner can prune all
partitions outside the query's date range.

For the hot-path query, partition pruning works because `created_at` is the partition key
and every message list query includes an implicit `created_at` bound (via cursor pagination):

```sql
-- EXPLAIN (ANALYZE, BUFFERS)
SELECT id, content, sender_id, created_at
FROM messages
WHERE conversation_id = '550e8400-e29b-41d4-a716-446655440000'
  AND created_at < '2024-03-15 12:00:00+00'
ORDER BY created_at DESC
LIMIT 50;

-- Expected EXPLAIN output (with partitioning):
-- Append  (cost=...)
--   ->  Index Scan Backward using messages_2024_03_conversation_id_created_at_idx
--         on messages_2024_03  (rows=50 ...)
--         Index Cond: (conversation_id = '...' AND created_at < '2024-03-15 ...')
-- (partitions messages_2024_01, messages_2024_02, messages_2024_04, ...
--  are pruned — planner eliminates them because their range cannot satisfy the bound)
```

Partition pruning guarantee: Postgres 16 supports both static and runtime pruning.
The cursor pagination encoding (see `src/shared/utils/pagination.py`) always produces a
`created_at` bound, so the planner will prune all months outside the query window.
The `enable_partition_pruning = on` server setting is the Postgres 16 default; verify
it is not overridden in `postgresql.conf`.

**`client_message_id` unique index**: The `UNIQUE` constraint on `client_message_id`
cannot span partitions in Postgres natively. Resolve by:

1. Keeping the unique index per partition (idempotency within a single month is enforced).
2. Adding a Redis check in `message_service.send_message()` before the INSERT — store
   `client_message_id` in Redis with a 7-day TTL. The Redis check provides cross-month
   idempotency for the realistic retry window. This is an acceptable tradeoff: retries
   that arrive >7 days after the original send are vanishingly rare in practice.

This Redis-assisted idempotency pattern is documented further in
`docs/design/idempotency.md`.

### Phase 2 — Archival (18-month window)

**Policy**: Partitions whose `valid_to` date is older than 18 months from today are
eligible for archival. Archival runs monthly via a Celery beat task.

**Chosen approach: `DETACH PARTITION` + `pg_dump` to S3.**

Rationale over dual-write to Glacier:
- Simpler: no dual-write code path, no risk of write-time divergence.
- The partition is already a self-contained Postgres relation; `pg_dump` preserves full
  schema and row fidelity.
- S3 Standard-IA is cheap enough for the data volume (<1 GB per month partition typically).
- Glacier retrieval latency (hours) is acceptable for the "request archive" flow anyway.
- Dual-write adds complexity without meaningful cost saving at this scale.

**Archival Celery task** (`src/modules/chat/tasks/archive_partition.py`):

```
1. Identify partitions where valid_to < NOW() - INTERVAL '18 months'.
2. For each eligible partition:
   a. DETACH PARTITION messages_{yyyy_mm} — partition is now a standalone table,
      still queryable but not part of messages routing.
   b. pg_dump messages_{yyyy_mm} to a compressed file.
   c. Upload to S3 bucket: s3://{ARCHIVE_BUCKET}/messages/{yyyy_mm}/dump.gz
      with server-side encryption (SSE-S3).
   d. Write an entry to archive_manifests table (see schema below).
   e. DROP TABLE messages_{yyyy_mm} — removes from live DB.
3. Emit structured log entry: archive_completed, partition=..., s3_key=..., row_count=...
4. Emit Prometheus counter: chat_partitions_archived_total.
```

```sql
-- New table: tracks what is in cold storage
CREATE TABLE archive_manifests (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    partition_name  VARCHAR(50) NOT NULL,       -- e.g. 'messages_2024_01'
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    row_count       BIGINT NOT NULL,
    s3_key          TEXT NOT NULL,              -- full S3 object key
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    restored_at     TIMESTAMPTZ,               -- NULL = still in cold storage
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_archive_manifests_period
    ON archive_manifests (period_start, period_end);
```

### Retrieval of archived messages — "request archive" flow

Archived messages are **not available in real-time**. The retrieval flow is async and
rate-limited.

**API endpoint** (added to chat module in Stage 4):

```
POST /api/v1/chat/conversations/{id}/archive-request
Headers: Authorization: Bearer <token>
Body:    { "period_start": "2023-01-01", "period_end": "2023-06-30" }

Response 202 Accepted:
{
  "data": {
    "request_id": "<uuid>",
    "estimated_ready_minutes": 15,
    "message": "Your archive export is being prepared. You will receive an email with a download link."
  }
}
```

**Rate limit**: One request per user per 24-hour window, enforced via Redis key
`archive_request:{user_id}` with 86 400-second TTL. Return HTTP 429 if key exists.

**Celery task** (`src/modules/chat/tasks/restore_archive.py`):

```
1. Validate the requested date range overlaps at least one archive_manifests entry
   for the given conversation_id.
2. Download dump.gz from S3 for each matching partition.
3. Filter rows to conversation_id requested (partition may contain other conversations).
4. Serialize filtered rows to JSONL, re-compress to .gz.
5. Upload result to S3 presigned URL (24-hour expiry).
6. Send email to requesting user via notifications module with the presigned URL.
7. Update archive_manifests.restored_at = NOW() for auditing.
```

The requesting user receives an email with a one-time download link (24-hour expiry).
The link serves a JSONL file that the Flutter client can render locally.

### Tombstoning integration

Users referenced by archived messages are **never hard-deleted**. The `users` table has a
`deleted_at` column (soft delete). When a user is deleted:

- `users.deleted_at` is set; the row is retained.
- The UI layer maps any `sender_id` where `users.deleted_at IS NOT NULL` to display
  name "Former member" and a generic avatar.
- This mapping applies equally to live partitions and to archived JSONL exports — the
  client must handle the "Former member" case for any sender whose profile is absent or
  marked deleted.

For the archive JSONL export specifically: the export task resolves display names at
export time and embeds `"sender_display_name": "Former member"` directly in the row
when `users.deleted_at IS NOT NULL`, so the Flutter client does not need a live user
lookup when rendering an offline export.

**Erasure vs retention tension**: The right-to-erasure flow (DPDP/GDPR) conflicts with
message retention. The full policy — including what happens to message content when a
user requests erasure — is deferred to `docs/design/privacy-erasure.md`. This RFC only
guarantees that archived partitions on S3 do not expose PII beyond what `privacy-erasure.md`
permits. The archival task must re-run erasure redaction before restoring an archive if
any erasure requests were received after the partition was archived.

### Alternatives considered

**1. Partition by `conversation_id` (hash partitioning)**
Rejected: hot-path query always bounds on `created_at`; hash partitioning provides no
pruning for date-range queries and makes archival by age impossible without a full scan.

**2. TimescaleDB hypertables**
Rejected: adds a major extension dependency to a self-hosted Postgres instance. The
operational complexity is not justified at this scale. Revisit if we ever exceed 50M rows.

**3. Keep flat table forever with aggressive vacuuming**
Rejected: a 100 GB+ heap table with frequent soft-deletes creates bloat and autovacuum
contention. Partitioning is the standard Postgres solution; it is not premature at 10M rows.

**4. Dual-write to S3 Glacier at message-send time**
Rejected for archival: adds latency to the hot write path and complicates the message
service. Batch archival on detached partitions is simpler and safer.

### Concrete migration recipe (for Stage-4d author when threshold is hit)

When monitoring alerts that messages row count is approaching 10M:

1. **Create the partitioned parent table** in a new Alembic migration:
   ```sql
   CREATE TABLE messages_partitioned ( ... ) PARTITION BY RANGE (created_at);
   ```
   Include `upgrade()` and `downgrade()`.

2. **Backfill**: Insert all rows from `messages` into `messages_partitioned` in batches
   of 100 000 rows during off-peak hours. Use `pg_partman`'s `run_maintenance()` to
   auto-create monthly child partitions as the insert progresses.
   ```bash
   psql -c "SELECT partman.create_parent('public.messages_partitioned', 'created_at', 'native', 'monthly');"
   ```

3. **Cutover** (maintenance window, estimated 2–5 minutes for rename):
   ```sql
   BEGIN;
   ALTER TABLE messages RENAME TO messages_old;
   ALTER TABLE messages_partitioned RENAME TO messages;
   -- Verify foreign keys and indexes still resolve correctly.
   COMMIT;
   ```
   Foreign keys from `message_reads.last_read_message_id` and `message_media.message_id`
   reference `messages(id)` by name; after the rename they point to the new partitioned
   table automatically.

4. **Verify** with EXPLAIN on the hot-path query (see partition pruning example above).
   Drop `messages_old` after a 48-hour soak period.

## Consequences

### Positive

- Zero schema changes in Stage 4 — the chat module is built on the plain table and
  migrates transparently later.
- Partition pruning makes "last N messages in conversation X" queries O(1) in partition
  count regardless of total table size.
- Monthly detach + dump is operationally simple: it is a standard Postgres + S3 operation
  with no exotic tooling.
- Archival reduces live DB size and vacuum load over time.
- Tombstoning ensures message history is coherent even after user deletion.

### Negative / tradeoffs

- The `client_message_id` unique constraint can no longer be enforced entirely in Postgres
  after partitioning; Redis-assisted check adds a network round-trip on every send.
- Cross-partition queries (e.g., "search all messages ever sent by user X") become slower
  after archival because they must query across live partitions and cold S3.
- Archival introduces operational burden: S3 bucket lifecycle policy, IAM permissions,
  and the `archive_manifests` table must be maintained.
- The "request archive" flow adds a code surface (Celery task, email integration, presigned
  URL generation) that requires its own tests and monitoring.
- The erasure-after-archival re-redaction step adds complexity to `privacy-erasure.md`'s
  implementation.

### Future work

- When / if a full-text search feature is added (Stage N), consider a separate
  Elasticsearch / OpenSearch index that ingests messages in real-time and handles
  cross-partition search without touching Postgres.
- If the institution grows beyond 5 000 users or activity spikes significantly, revisit
  the 18-month archival window — a shorter window (12 months) may be appropriate.
- `pg_partman` scheduled maintenance (`run_maintenance()`) should be added to the Celery
  beat schedule so new monthly partitions are created automatically without manual
  intervention.
- Revisit TimescaleDB if row counts exceed 50M (implies >10 years at medium activity or
  a much larger institution).

## Cross-references

- Related RFC: `docs/design/tenant-isolation.md` — RLS policies must hold across
  partitions. Postgres 16 propagates parent-table RLS to child partitions automatically;
  verify this behaviour in the Stage-4d integration test suite.
- Related RFC: `docs/design/websocket-scaling.md` — WebSocket resume-from-cursor reads
  recent messages. With partitioning active, the cursor query hits at most 1–2 child
  partitions (current + previous month); no special handling needed in the WS layer.
- Related RFC: `docs/design/idempotency.md` — the Redis-assisted `client_message_id`
  check replaces the cross-partition uniqueness gap; implementation details live there.
- Related RFC: `docs/design/privacy-erasure.md` — erasure of PII in archived partitions
  is handled in that RFC. This RFC defers all DPDP/GDPR specifics to it.
- Consumed by: `src/modules/chat/` — message_service, archive tasks.
- Consumed by: `migrations/` — the Phase-1 partitioning migration lives here.
- Reference doc: `reference-docs/modules/chat/SCHEMA.sql` — source of truth for
  `messages` table definition. The partitioned parent must match column names and types
  exactly.
