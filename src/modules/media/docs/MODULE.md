# Module: Media

> **This module handles all file uploads and downloads.** Images, videos, audio, and documents.

---

## What This Module Does

- Upload files (images, videos, audio, documents)
- Generate thumbnails for images and videos
- Serve file downloads with signed URLs
- Track file metadata (size, type, dimensions, duration)
- Enforce file size and type limits

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | Only authenticated users can upload/download |
| `acl` | Permission check: `media.upload`, `media.download` |

---

## Upload Flow

```
1. Flutter app picks a file (camera, gallery, file picker)

2. Flutter requests an upload URL:
   POST /api/v1/media/upload/init
   { "file_name": "photo.jpg", "file_size": 245000, "mime_type": "image/jpeg" }

   Server validates:
   - File size within limits
   - MIME type is allowed
   - User has upload permission

   Server responds with:
   {
     "upload_id": "uuid",
     "upload_url": "https://s3.../presigned-upload-url",
     "expires_in": 3600
   }

3. Flutter uploads file directly to S3/MinIO using the presigned URL
   (This keeps large files off the API server)

4. Flutter confirms the upload:
   POST /api/v1/media/upload/complete
   { "upload_id": "uuid" }

   Server:
   - Verifies the file exists in S3
   - Records metadata in PostgreSQL
   - Queues thumbnail generation (Celery task)
   - Returns the media file object

5. Flutter includes media_file_id in the message send request
```

**Why presigned URLs?** Large files (videos up to 100MB) should not pass through the FastAPI server. They go directly from the client to S3/MinIO. The server only handles metadata.

---

## File Limits

| Type | Max Size | Allowed MIME Types |
|------|---------|-------------------|
| Image | 10 MB | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Video | 100 MB | `video/mp4`, `video/quicktime`, `video/webm` |
| Audio | 20 MB | `audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/aac` |
| Document | 50 MB | `application/pdf`, `application/msword`, `application/vnd.openxmlformats-officedocument.*` |

---

## Storage Structure (S3/MinIO)

```
bucket: infinitybox-messenger-media
├── {institution_id}/
│   ├── originals/
│   │   └── {year}/{month}/{media_file_id}.{ext}
│   └── thumbnails/
│       └── {media_file_id}_thumb.jpg
```

---

## Thumbnail Generation

| Source Type | Thumbnail |
|------------|-----------|
| Image | Resize to max 400x400, preserve aspect ratio, JPEG quality 80 |
| Video | Extract frame at 1 second, resize to 400x400 |
| Audio | No thumbnail (use generic audio icon in UI) |
| Document | No thumbnail (use generic doc icon in UI) |

Thumbnails are generated asynchronously by a Celery worker after upload completion.

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/media/upload/init` | Initialize upload, get presigned URL |
| `POST` | `/api/v1/media/upload/complete` | Confirm upload, trigger processing |
| `GET` | `/api/v1/media/{id}` | Get media file metadata |
| `GET` | `/api/v1/media/{id}/download` | Get signed download URL (expires in 1 hour) |
| `DELETE` | `/api/v1/media/{id}` | Delete a media file (uploader or admin only) |

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `media_files` | File metadata: name, size, type, S3 key, thumbnail key |

---

## Security

1. **Download URLs are signed and expire** — no permanent public URLs.
2. **Upload URLs are presigned and expire** — each URL works for exactly one upload.
3. **MIME type validation** happens both on init (declared type) and on complete (actual file inspection).
4. **File size is checked** on init (declared) and enforced by S3 policy.
5. **No executable files** — `.exe`, `.sh`, `.bat`, etc. are always rejected.
