# Media module — API reference (Stage 4e)

All routes require a valid JWT via `Authorization: Bearer <token>`. Responses
follow the canonical envelope from
[`docs/design/api-versioning.md`](../../../../docs/design/api-versioning.md):

- Success: `{ "data": { ... } }` — shape of `data` documented per endpoint.
- Error:   `{ "error": { "code": "...", "message": "...", "details": {...} } }`.

Fixtures in this document come from the real tests under
[`tests/modules/media/`](../../../../tests/modules/media/). If the contract
drifts, the tests fail — the doc and the behaviour can't diverge silently.

---

## `POST /api/v1/media/upload/init` — mint a presigned PUT URL

Creates a `media_files` row in `pending` state and returns a short-lived
S3/MinIO URL the client uses to upload file bytes directly.

### Request

```http
POST /api/v1/media/upload/init
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000

{
  "file_name": "photo.jpg",
  "file_size": 245000,
  "mime_type": "image/jpeg"
}
```

Headers:
- `Idempotency-Key` (required, UUID4) — per `docs/design/idempotency.md`.
  Client-generated. The server caches the response bytes for 24 hours and
  replays them if the same key is re-used with the same body.

### 201 Created

```json
{
  "data": {
    "upload_id": "beeab234-86fd-4e0e-91fa-db657f685d46",
    "upload_url": "https://minio.test/600e8b5f-...originals/2026/04/beeab234-....jpg?put-signed=yes",
    "storage_key": "600e8b5f-.../originals/2026/04/beeab234-....jpg",
    "expires_in": 3600
  }
}
```

### Error codes

| Code | HTTP | When | Example `details` |
|------|------|------|-------------------|
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | `Idempotency-Key` header missing | `{"header": "Idempotency-Key"}` |
| `IDEMPOTENCY_KEY_INVALID` | 400 | Header present but not a UUID4 | `{"header": "Idempotency-Key", "value": "not-a-uuid"}` |
| `IDEMPOTENCY_COLLISION` | 409 | Same key, different body | `{"resource_type": "idempotency_key"}` |
| `VALIDATION_ERROR` | 422 | `mime_type`, `file_size`, or extension rejected | `{"field": "mime_type"}` or `{"field": "file_size"}` or `{"field": "file_name"}` |
| `AUTHENTICATION_FAILED` | 401 | No / invalid bearer token | `{}` |

### File-type allow-list

| Class | Max size | Allowed MIME types |
|-------|----------|--------------------|
| Image | 10 MB | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Video | 100 MB | `video/mp4`, `video/quicktime`, `video/webm` |
| Audio | 20 MB | `audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/aac` |
| Document | 50 MB | `application/pdf`, `application/msword`, `application/vnd.openxmlformats-officedocument.*` (Word / Excel / PowerPoint OOXML) |

Executable extensions (`.exe`, `.sh`, `.bat`, `.cmd`, `.com`, `.scr`,
`.ps1`, `.msi`, `.dll`, `.jar`, `.app`, `.pkg`) are always rejected,
regardless of declared MIME.

---

## `POST /api/v1/media/upload/complete` — confirm the upload finished

The server HEADs the S3 key to verify the object exists, then flips
`upload_status` from `pending` → `completed`. Writes one `media.uploaded`
audit row inside the same transaction.

### Request

```http
POST /api/v1/media/upload/complete
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "upload_id": "beeab234-86fd-4e0e-91fa-db657f685d46"
}
```

### 200 OK

```json
{
  "data": {
    "id": "beeab234-86fd-4e0e-91fa-db657f685d46",
    "institution_id": "600e8b5f-4fc7-4391-b150-a5bba1f8e081",
    "uploader_id": "961fea5a-4048-4cda-affc-c250b659bb2b",
    "file_name": "photo.jpg",
    "file_size": 245000,
    "mime_type": "image/jpeg",
    "storage_key": "600e8b5f-.../originals/2026/04/beeab234-....jpg",
    "thumbnail_key": null,
    "metadata": {"verified_size_bytes": 12345},
    "upload_status": "completed",
    "recycle_bin_at": null,
    "deleted_at": null,
    "created_at": "2026-04-18T04:50:00Z",
    "updated_at": "2026-04-18T04:50:02Z"
  }
}
```

### Error codes

| Code | HTTP | When |
|------|------|------|
| `NOT_FOUND` | 404 | No row with that `upload_id` in the caller's institution (also returned for cross-tenant attempts — RFC requires 404, not 403). |
| `PERMISSION_DENIED` | 403 | Row exists in the caller's institution but the caller is not `uploader_id`. |
| `VALIDATION_ERROR` | 422 | `upload_id` is not a UUID, or the S3 HEAD returned NoSuchKey (client never finished the PUT). |

Calling `complete` on an already-completed row is a safe no-op: the same
200 response is returned and no second audit row is written.

---

## `GET /api/v1/media/{id}` — metadata only

Returns the same `MediaFileResponse` shape as `/upload/complete`, without
minting a download URL.

### 200 OK

Shape identical to `/upload/complete` above.

### Error codes

| Code | HTTP | When |
|------|------|------|
| `NOT_FOUND` | 404 | Unknown id or cross-tenant. |
| `VALIDATION_ERROR` | 422 | `id` is not a UUID. |
| `AUTHENTICATION_FAILED` | 401 | No / invalid bearer token. |

---

## `GET /api/v1/media/{id}/download` — mint a presigned GET URL

Returns a 1-hour signed URL for the bytes. The `Content-Disposition` header
on the signed GET response includes the original filename.

### 200 OK

```json
{
  "data": {
    "download_url": "https://minio.test/600e8b5f-.../originals/2026/04/beeab234-....jpg?get-signed=yes",
    "expires_in": 3600
  }
}
```

### Error codes

| Code | HTTP | When |
|------|------|------|
| `NOT_FOUND` | 404 | Unknown id or cross-tenant. |
| `VALIDATION_ERROR` | 422 | Media is still `pending` — call `/upload/complete` first. |
| `AUTHENTICATION_FAILED` | 401 | No / invalid bearer token. |

Files in the 30-day recycle-bin grace window are still downloadable so
uploaders can export or restore before permanent purge.

---

## `DELETE /api/v1/media/{id}` — enter the recycle bin

Sets `recycle_bin_at = NOW()`. The S3 object is **not** deleted yet; the
nightly sweep in the privacy-erasure module (deferred to a follow-up Celery
task) permanently purges objects after 30 days.

Only the uploader can call this endpoint. An institution-admin mass-delete
capability is **deferred to Stage 4b (ACL)** — see `§Deferred` below.

### 200 OK

```json
{
  "data": {
    "id": "beeab234-86fd-4e0e-91fa-db657f685d46",
    "recycle_bin_at": "2026-04-18T04:51:00Z",
    "message": "Media file moved to recycle bin. It will be permanently deleted after 30 days."
  }
}
```

### Error codes

| Code | HTTP | When |
|------|------|------|
| `NOT_FOUND` | 404 | Unknown id or cross-tenant. |
| `PERMISSION_DENIED` | 403 | Caller is not the uploader. |
| `VALIDATION_ERROR` | 422 | `id` is not a UUID. |
| `AUTHENTICATION_FAILED` | 401 | No / invalid bearer token. |

Calling DELETE twice is idempotent — the second call returns the same 200
response with the original `recycle_bin_at` (no second audit row).

---

## Audit events

Every mutation writes one row to `audit_logs` inside the same transaction
as the data change. The `AuditEvent` enum in
[`src/shared/security/audit.py`](../../../shared/security/audit.py) is the
single source of truth.

| Event | When | Metadata |
|-------|------|----------|
| `media.uploaded` | `/upload/complete` succeeds | `{"file_type": "image/jpeg", "file_size_bytes": 245000}` |
| `media.recycle_bin_entered` | `DELETE /{id}` first call | `{"grace_days": 30}` |
| `media.deleted` | reserved for a direct hard-delete flow (not exposed in Stage 4e) | — |
| `media.permanently_deleted` | reserved for the nightly recycle-bin sweep Celery task | — |

Idempotency-replay responses do NOT emit an audit row — the service layer
is never reached. This is the structural Gap-A fix documented in
`docs/MANUAL_SMOKE.md`.

---

## Deferred

These pieces of the MODULE.md spec are intentionally NOT in Stage 4e and
will land in a later stage:

- **Admin mass-delete / restore.** Requires `institution.manage_media`
  permission that only the ACL module (Stage 4b) can check. Attempting
  to use `DELETE /{id}` as a non-uploader returns 403 today; there is no
  admin override endpoint.
- **Thumbnail generation.** Schema has `thumbnail_key`; no route sets it.
  The Celery worker that runs PIL / ffmpeg and updates the column will be
  added alongside the notifications module's Celery infrastructure.
- **Recycle-bin sweep.** Required table indexes exist
  (`idx_media_files_recycle_bin`) but the Celery beat task that sets
  `deleted_at` after 30 days is not registered. Track with the
  privacy-erasure SLA in `docs/design/privacy-erasure.md`.
- **Virus scanning.** Not in scope for Stage 4e. SLO budget from
  `docs/design/slo.md` (upload p95 < 3000 ms) would need re-sizing before
  adding ClamAV.
- **Listing by uploader.** `GET /media?uploader_id=...` is not exposed
  yet; the `idx_media_files_uploader` index supports it whenever it's
  added.

---

## Feature flag

The module ships with `ENABLE_MEDIA=false` (see `src/config.py`). Flag flip
is a separate decision after all Stage-4 modules land and the integration
smoke passes. Turning the flag off in production removes the routes
entirely — no dangling `404`s from half-wired behaviour.
