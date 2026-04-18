# Threat Model — Media

> Living doc. Update when the module changes.

## 1. Scope

- **What it does:** Issues presigned URLs so clients PUT file bytes directly
  to S3/MinIO, tracks file metadata in Postgres, issues presigned download
  URLs, and implements a 30-day recycle bin before permanent S3 purge.
- **Data owned:** `media_files` rows (id, institution_id, uploader_id,
  file_name, file_size, mime_type, storage_key, thumbnail_key, metadata,
  upload_status, recycle_bin_at, deleted_at, created_at, updated_at) + S3
  objects at `{institution_id}/originals/{year}/{month}/{id}.{ext}` and
  `{institution_id}/thumbnails/{id}_thumb.jpg`.
- **External interfaces:**
  - HTTP: `POST /api/v1/media/upload/init`, `POST /api/v1/media/upload/complete`,
    `GET /api/v1/media/{id}`, `GET /api/v1/media/{id}/download`,
    `DELETE /api/v1/media/{id}`.
  - S3/MinIO via `src/shared/storage.py` (presigned PUT/GET, HEAD, DELETE).
  - Celery: thumbnail generation + recycle-bin purge sweep (task definitions
    deferred — module ships the enqueue points behind `ENABLE_MEDIA=false`).

## 2. Trust boundaries

- **Callers:** authenticated users only (`get_current_user` on every route).
  Anonymous access is rejected at middleware — there is no open endpoint.
- **Tenant boundary:** `media_files.institution_id` (column enforces the
  isolation; `tenant_scoped_query(MediaFile, institution_id)` is used on
  every lookup).  S3 object keys are also institution-prefixed so an
  operator manually inspecting a bucket cannot cross tenants by mistake.
- **External credentials:** S3/MinIO access key + secret key live in
  `src/config.py` (loaded from `.env`). Presigned URLs are signed with
  these credentials but expire in ≤ 1 hour (configurable), so leakage
  of a URL is time-bound.

## 3. STRIDE analysis

| Threat | Applies? | Mitigation |
|---|---|---|
| **S**poofing — can an attacker impersonate a user? | yes | JWT verified by `get_current_user` middleware on every route; no anonymous access. |
| **T**ampering — can data be modified in transit or at rest? | yes | TLS on wire (deployment concern); parameterised SQL throughout; presigned PUT URLs lock `Content-Type` so a client can't silently change it; audit-log entries on every mutation. |
| **R**epudiation — can a user deny they did something? | yes | `write_audit()` row in the same DB transaction as every mutation (`media.uploaded`, `media.deleted`, `media.recycle_bin_entered`, `media.permanently_deleted`). |
| **I**nformation disclosure — can one tenant see another's data? | yes | `tenant_scoped_query` gate + `assert_same_institution` on resource lookups; cross-tenant access returns 404 (not 403). Dedicated cross-tenant fuzz test per route. S3 keys prefixed with `{institution_id}/` so path enumeration on the bucket also fails at the tenant boundary. |
| **D**enial of service — can the module be flooded? | partially | Rate limiter (`src/shared/security/rate_limit.py`) applies a default 300/min per client; file size cap rejects oversized uploads at `init` before any S3 call. Not mitigated: a flood of `init` calls with tiny files could fill the `pending` rows table — a Celery sweep of stale pending rows > 1h old is listed under §7 open risks. |
| **E**levation of privilege — can a user do something they shouldn't? | yes | Delete is uploader-only; admin-mass-delete requires a permission check deferred to ACL (Stage 4b) per the kickoff prompt. |

## 4. Abuse cases (module-specific)

- **Cross-tenant enumeration via media UUID.** Attacker from institution A
  guesses a media UUID belonging to institution B. → `tenant_scoped_query`
  returns no row, route raises `NotFoundError` (404). Never 403, never 500.
  Covered by `test_cross_tenant_returns_404`.
- **Idempotency-Key replay to mint a second presigned URL for the same
  upload.** Would let an attacker stockpile signed URLs if allowed. →
  `require_idempotency("media.upload.init")` on `POST /media/upload/init`;
  the first response bytes are replayed verbatim, no new URL minted.
- **Upload of a disallowed MIME type (e.g., `.exe` masquerading as
  `image/jpeg`).** → MIME type validated at `init` against an allow-list per
  file class; `head_object` at `complete` verifies the object exists but we
  also trust the presigned URL's locked `Content-Type` (client cannot
  silently change it). Executable extensions are explicitly blocked by an
  ext-based allow-list before mime resolution.
- **Oversized file upload exhausting the S3 quota.** → `content_length_max`
  is set on the presigned PUT signature so S3/MinIO itself rejects
  oversized PUTs. Size is also declared at `init` and validated before
  the URL is minted.
- **User attempts to delete another user's media.** → Delete route compares
  `uploader_id` to the caller's `user_id`; mismatch raises
  `PermissionDeniedError` (403). Admin-mass-delete (ACL-backed) is deferred
  to Stage 4b, documented in `docs/API.md` §Deferred.

## 5. Data handling

- **PII touched:** uploaded file bytes may contain PII (photos of students,
  documents with names). We store only the metadata (file name, size, MIME)
  in Postgres; the bytes live on S3/MinIO.
- **Retention:** Active files follow message retention (indefinite within
  the institution per `docs/design/privacy-erasure.md`). Recycle-bin files
  live 30 days after `recycle_bin_at`, then a nightly Celery sweep deletes
  the S3 object and hard-deletes the row.
- **Encryption at rest:** MinIO / S3 default server-side encryption
  (deployment-level concern, not enforced in application code).
- **Right-to-erasure path:** `DELETE /api/v1/me` (owned by users module)
  sets `media_files.recycle_bin_at = NOW()` for every file where
  `uploader_id = erased_user_id`. The nightly sweep then permanently
  deletes the S3 object and the row. See `docs/design/privacy-erasure.md`
  §Media recycle bin.

## 6. Logging & monitoring

- **Structlog events:** `media_upload_initiated`, `media_upload_completed`,
  `media_upload_verify_missing`, `media_recycle_bin_entered`,
  `media_permanently_deleted`, `media_denied_mime_type`,
  `media_denied_size_exceeded`.
- **Prometheus:** existing `http_requests_total{handler,...}` and
  `http_request_duration_seconds` cover the REST surface. `/api/v1/media/upload`
  carries the dedicated p95 < 3000 ms SLO carve-out per `docs/design/slo.md`.
  No new counters added by this module.
- **Alerts:** SLO breach alerts already defined in `docs/design/slo.md`
  (upload p95 > 3000 ms sustained for ≥ 3 windows → backlog ticket).

## 7. Open risks

- **Pending-row buildup.** A client can call `init` and never `complete`,
  leaving a `pending` row forever. Index `idx_media_files_pending` exists
  for the sweep but no Celery task is registered in this module. → Follow-up
  task in Stage 4 cleanup: `celery_tasks.sweep_stale_pending_media`
  (reject after 1 hour).
- **Admin bulk delete.** Deferred to Stage 4b (ACL). Today there is no
  endpoint to delete media belonging to another user; only the uploader or
  the users-module erasure flow can flip `recycle_bin_at`. Non-issue for
  the pilot but should be revisited before multi-tenant admin UX.
- **Thumbnail generation.** Queue-enqueue stubs will ship with ACL
  decorators and Celery task bodies in a follow-up. Today `complete`
  records the completed status but does not set `thumbnail_key`; the UI
  must tolerate `thumbnail_key IS NULL` until the Celery task lands.
- **Virus scanning** (ClamAV) is NOT implemented. The SLO budget allows
  3 seconds for upload; adding ClamAV would need separate sizing. Noted for
  future work; would run inside the `complete` handler.
