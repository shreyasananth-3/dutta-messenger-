# Media Module — API Contract

**Status:** Live on `main`.
**Base path:** `/api/v1`
**Auth required:** Bearer on every endpoint.

---

## Read this before anything else — the upload flow is not what you think

Most APIs have "upload a file" as one call: you POST a multipart form with the bytes, server saves it, returns the file id. **This is NOT that.**

Here, the Flutter client uploads the file **directly to S3**, not to our server. Our server only gives the client a **presigned URL** (a temporary URL that S3 trusts) and tracks the file's metadata.

### Why?

- Our server never sees the file bytes → no memory blow-ups, no bandwidth on our EC2
- Client can upload a 100 MB video without worrying about request timeouts on our API
- S3 handles retries, multi-part uploads, etc.

### The three-step flow

```
1. INIT        Client tells API "I want to upload {name, size, mime_type}"
               API returns: upload_id + upload_url (presigned PUT to S3)
                                    + storage_key + expires_in

2. PUT TO S3   Client uploads bytes directly to upload_url (NOT through our API)
               PUT https://<bucket>.s3.amazonaws.com/<storage_key>?{signature}
               Body: raw file bytes
               Header: Content-Type must match the mime_type from step 1

3. COMPLETE    Client tells API "done, upload_id=XYZ"
               API HEADs the S3 object to verify it arrived, then returns
               the final MediaFileResponse. Use the returned media_id as
               the attachment in messages, avatars, etc.
```

**Why 3 steps instead of 2?** Because between steps 1 and 2, the client might crash / user kills the app / network dies. Step 3 is how the server confirms "yes the file actually made it" and flips the DB row from `upload_status=pending` → `completed`. If you forget step 3, the file is orphaned — it'll sit in S3 but won't be retrievable via `/download`.

---

## Endpoints at a glance

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/media/upload/init` | Step 1: get a presigned PUT URL | Bearer |
| POST | `/media/upload/complete` | Step 3: finalize after PUT succeeds | Bearer |
| GET | `/media/{id}` | Get file metadata | Bearer |
| GET | `/media/{id}/download` | Get a presigned GET URL | Bearer |
| DELETE | `/media/{id}` | Move to recycle bin (30-day grace) | Bearer |

---

## File limits — know these before calling init

| Kind | Max size | Allowed MIME types |
|------|---------|-------------------|
| Image | 10 MB | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Video | 100 MB | `video/mp4`, `video/quicktime`, `video/webm` |
| Audio | 20 MB | `audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/aac` |
| Document | 50 MB | `application/pdf`, `application/msword`, `application/vnd.openxmlformats-officedocument.*` (docx / xlsx / pptx) |

**Hard ceiling:** 100 MB overall. `/upload/init` will 422 if you exceed it.

**Blocked file extensions (always rejected):** `.exe`, `.sh`, `.bat`, `.cmd`, `.com`, `.scr`, `.ps1`, `.msi`, `.dll`, `.jar`, `.app`, `.pkg`.

Do client-side validation on the file picker screen too — don't just rely on the 422 from the server. Use the `mime` package to sniff the real MIME type (don't trust filename extensions).

---

## 1. `POST /api/v1/media/upload/init`

### Request

**Header:** `Idempotency-Key: <unique-string>` — required. See the idempotency note below.

```json
{
  "file_name": "summer-field-trip.jpg",
  "file_size": 2457600,
  "mime_type": "image/jpeg"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file_name` | string | yes | 1–255 chars, original filename |
| `file_size` | integer | yes | Bytes. Must be > 0 and ≤ 100 MB. Must match the real size (the server verifies later) |
| `mime_type` | string | yes | 1–100 chars, must be one of the allowed MIME types above |

### 201 Created
```json
{
  "data": {
    "upload_id": "f4a3e81c-5e91-48dc-832f-bbb2de1a7c11",
    "upload_url": "https://duttamessenger-media.s3.ap-south-1.amazonaws.com/...signed...",
    "storage_key": "institutions/301842.../2026/04/19/f4a3e81c....jpg",
    "expires_in": 3600
  }
}
```

**Keep `upload_id` around** — you'll need it for step 3.
**`upload_url` is valid for `expires_in` seconds** (currently 3600 = 1 hour). If upload takes longer, call init again.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `AUTHENTICATION_FAILED` | Missing/invalid bearer token |
| 422 | `VALIDATION_ERROR` | Size > 100 MB, disallowed MIME type, or blocked extension |

### Idempotency note

If your upload crashes between init and complete, retrying `init` with the **same `Idempotency-Key`** gets you back the same `upload_id` + `upload_url` instead of creating a duplicate. Use a stable per-attempt UUID (e.g. `const Uuid().v4()` once per user action, saved in local storage until complete succeeds).

---

## 2. PUT the bytes directly to S3 — **not through our API**

With the `upload_url` from step 1:

```dart
// Flutter — dio
await dio.put(
  uploadUrl,                           // the presigned URL, NOT api base
  data: file.openRead(),               // stream the bytes
  options: Options(
    headers: {
      'Content-Type': mimeType,        // MUST match what you sent in init
      'Content-Length': fileSize,
    },
  ),
);
```

### What can go wrong here

- `403 Forbidden` — the URL expired (>1 hour since init) → call init again
- `403 SignatureDoesNotMatch` — `Content-Type` header doesn't match the `mime_type` you sent in init → send the exact same string
- `400 EntityTooLarge` — you uploaded more bytes than `file_size` in init → pick up the real size and redo init
- Network error mid-upload — safe to retry the PUT (S3 will overwrite)

### What NOT to do

- **Do not** send `Authorization: Bearer ...` to S3. The URL is already signed.
- **Do not** send the file through `/api/v1/...`. There is no such endpoint.
- **Do not** URL-encode the presigned URL. Use it verbatim.

---

## 3. `POST /api/v1/media/upload/complete`

### Request
```json
{
  "upload_id": "f4a3e81c-5e91-48dc-832f-bbb2de1a7c11"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `upload_id` | UUID string | yes | From step 1 |

### 200 OK
```json
{
  "data": {
    "id": "9c2b1ef4-71aa-4ba8-91ea-6c9b8dc5f221",
    "institution_id": "301842b2-3a73-46d5-b4d5-6ab8e13c3829",
    "uploader_id": "8585217f-04ab-43b8-8edd-9bd7ba000a93",
    "file_name": "summer-field-trip.jpg",
    "file_size": 2457600,
    "mime_type": "image/jpeg",
    "storage_key": "institutions/301842.../2026/04/19/f4a3e81c....jpg",
    "thumbnail_key": null,
    "metadata": { "verified_size_bytes": 2457600 },
    "upload_status": "completed",
    "recycle_bin_at": null,
    "deleted_at": null,
    "created_at": "2026-04-19T16:10:00Z",
    "updated_at": "2026-04-19T16:10:23Z"
  }
}
```

The `id` here is the **media id** — this is what you reference in messages, user avatars, etc.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Someone other than the uploader tried to complete the upload |
| 404 | `NOT_FOUND` | `upload_id` doesn't exist or belongs to a different tenant |
| 422 | `VALIDATION_ERROR` | S3 object not found (PUT failed / skipped) |

### Safe to retry

Calling `complete` again with the same `upload_id` after it already succeeded returns the same row unchanged (idempotent, no double-audit, no double-thumbnail-job).

---

## 4. `GET /api/v1/media/{id}` — Metadata

Returns the same shape as the `/upload/complete` response. Use it to check `upload_status`, thumbnail availability, etc.

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 404 | `NOT_FOUND` | Not found, or cross-tenant, or hard-deleted (`deleted_at` set) |

Files in the recycle bin (`recycle_bin_at` set but `deleted_at` null) are still visible.

---

## 5. `GET /api/v1/media/{id}/download`

Returns a **presigned GET URL** — use it to download or render the file.

### 200 OK
```json
{
  "data": {
    "download_url": "https://duttamessenger-media.s3.ap-south-1.amazonaws.com/...signed...",
    "expires_in": 3600
  }
}
```

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 404 | `NOT_FOUND` | Not found, cross-tenant, or hard-deleted |
| 422 | `VALIDATION_ERROR` | Upload never completed (`upload_status != "completed"`) |

### Flutter pattern

```dart
// 1. get a fresh URL (don't cache — they expire in 1 hour)
final resp = await api.get('/api/v1/media/$mediaId/download');
final url = resp.data['download_url'];

// 2. use it with any image loader
Image.network(url);

// or download to disk
await dio.download(url, localPath);
```

**Don't cache `download_url` for more than a minute.** If you need to show the same image in multiple places, cache the bytes (e.g. `CachedNetworkImage` with the `media_id` as cache key), not the URL.

---

## 6. `DELETE /api/v1/media/{id}` — Move to recycle bin

### 200 OK
```json
{
  "data": {
    "id": "9c2b1ef4-71aa-4ba8-91ea-6c9b8dc5f221",
    "recycle_bin_at": "2026-04-19T16:20:00Z",
    "message": "Media file moved to recycle bin. It will be permanently deleted after 30 days."
  }
}
```

### Errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 403 | `PERMISSION_DENIED` | Caller is not the uploader |
| 404 | `NOT_FOUND` | Not found or cross-tenant |

### Behavior

- **Soft-delete**: sets `recycle_bin_at`, does not hard-delete the S3 object.
- File stays visible via `GET /media/{id}` and `GET /media/{id}/download` for 30 days.
- After 30 days a background job permanently deletes the S3 object + DB row.
- **Idempotent**: re-deleting returns the same row, no second audit.

There is no "restore from recycle bin" endpoint yet. If you need one, file a ticket.

---

## Complete Flutter example — upload a photo, attach to a message

```dart
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:uuid/uuid.dart';
import 'package:mime/mime.dart';

class MediaUploader {
  final Dio api;      // pointed at https://your-host/api/v1 with Bearer token
  final Dio s3;       // plain Dio, no interceptors, no auth header

  MediaUploader(this.api) : s3 = Dio();

  /// Uploads [file] and returns the final media_id on success.
  Future<String> upload(File file) async {
    final stat = await file.stat();
    final name = file.path.split('/').last;
    final mime = lookupMimeType(file.path) ?? 'application/octet-stream';
    final idempotencyKey = const Uuid().v4();

    // Step 1: init
    final init = await api.post(
      '/media/upload/init',
      data: {
        'file_name': name,
        'file_size': stat.size,
        'mime_type': mime,
      },
      options: Options(headers: {'Idempotency-Key': idempotencyKey}),
    );
    final uploadId = init.data['data']['upload_id'] as String;
    final uploadUrl = init.data['data']['upload_url'] as String;

    // Step 2: PUT to S3 (no auth header, no API base — direct to the presigned URL)
    await s3.put(
      uploadUrl,
      data: file.openRead(),
      options: Options(
        headers: {
          'Content-Type': mime,
          'Content-Length': stat.size,
        },
      ),
    );

    // Step 3: complete
    final complete = await api.post(
      '/media/upload/complete',
      data: {'upload_id': uploadId},
    );
    return complete.data['data']['id'] as String;
  }
}

// Usage
final mediaId = await uploader.upload(pickedFile);
await chatApi.sendMessage(
  conversationId: convId,
  content: null,
  mediaId: mediaId,      // reference media_id in the message
);
```

**Show a progress bar** during step 2 using `dio`'s `onSendProgress` callback — that's the long part for videos.

---

## Minimum "attach file to message" implementation

- [ ] **File picker** — `image_picker` for photos, `file_picker` for documents.
- [ ] **Client-side size check** — reject files over the type's limit before init.
- [ ] **Client-side MIME sniff** — use the `mime` package; don't trust extensions.
- [ ] **Idempotency-Key** — generate one UUID per attempt, keep it across retries of the same upload.
- [ ] **Show a progress bar during the S3 PUT** — use `onSendProgress`. Init + complete are fast (<500 ms each).
- [ ] **Handle expired `upload_url`** — if S3 returns 403 after a long wait, call `init` again.
- [ ] **Save `media_id` on the message** — the chat send endpoint accepts a `media_id` field.
- [ ] **Lazy-load download URLs** — don't pre-fetch every message's download URL; fetch only when the user is about to view/download.

---

## Common pitfalls

| Pitfall | How to spot | Fix |
|---------|-------------|-----|
| Sending `Authorization` header to S3 | `403 SignatureDoesNotMatch` | Use a separate Dio instance for S3, without your auth interceptor |
| `Content-Type` mismatch | `403 SignatureDoesNotMatch` | Pass exactly the `mime_type` you sent in init — including casing |
| Calling `complete` from a different user | `403 PERMISSION_DENIED` | Uploader-only. Don't share `upload_id` across accounts. |
| Forgetting step 3 | File sits in S3 but `/download` returns 422 | Always call `complete` after the PUT succeeds. Retry it if the network dropped. |
| Trying to `DELETE` someone else's file | `403 PERMISSION_DENIED` | Only the uploader can delete. Group admins / owners cannot delete other users' uploads (by design). |
| Caching `download_url` for hours | 403 when you try to load the image later | Re-fetch via `/media/{id}/download`. URLs expire in 1 hour. |
