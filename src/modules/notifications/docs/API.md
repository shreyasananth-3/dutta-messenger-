# Notifications — REST API

All endpoints are under `/api/v1/notifications/` and require a valid
access token (`Authorization: Bearer <jwt>`). Error responses follow the
canonical envelope defined in `docs/design/api-versioning.md`.

## `POST /api/v1/notifications/tokens` — Register a device token

Register (or reactivate) an FCM registration token. **Idempotent on
`(user_id, token)`** per `docs/design/idempotency.md`: re-registering a
token the caller already owns updates `last_used_at` and returns
`reused: true` rather than 409.

### Request

```json
{
  "token": "fOcm4c2z1Hj...Zq",
  "device_name": "iPhone 14",
  "device_type": "ios"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string (1–500) | yes | Opaque FCM registration token. |
| `device_name` | string (≤255) | no | Human-readable device label. |
| `device_type` | enum | no | One of `ios`, `android`, `web`. |

### Response — 200 OK

```json
{
  "data": {
    "token": {
      "id": "0cf3ef2e-...",
      "user_id": "3f6d8a12-...",
      "device_name": "iPhone 14",
      "device_type": "ios",
      "is_active": true,
      "last_used_at": "2026-04-18T10:00:00+00:00",
      "created_at": "2026-04-18T10:00:00+00:00",
      "updated_at": "2026-04-18T10:00:00+00:00"
    },
    "reused": false
  }
}
```

`reused` is `true` on the second identical call.

## `DELETE /api/v1/notifications/tokens/{token_id}` — Revoke a token

Soft-deactivates one of the caller's tokens (`is_active = false`).
Cross-owner, cross-tenant, and unknown IDs all return 404 — the server
never confirms existence across scopes.

### Response — 204 No Content

(no body)

### Error — 404 Not Found

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "fcm_token not found",
    "details": {
      "resource_type": "fcm_token",
      "resource_id": "b4c9…"
    }
  }
}
```

## `GET /api/v1/notifications/unread-count` — Unread count

Returns the caller's unread notification count.

### Response — 200 OK

```json
{ "data": { "unread": 3 } }
```

## `POST /api/v1/notifications/mark-read` — Mark as read

Marks notifications as read. Empty `notification_ids` marks every
unread row for the caller; a non-empty list limits the update to those
IDs — and only those actually owned by the caller (cross-user IDs are
silently ignored so this cannot be used to probe for existence).

### Request

```json
{ "notification_ids": [] }
```

### Response — 200 OK

```json
{ "data": { "marked": 3 } }
```

## Error Codes

| Code | HTTP | When | Example `details` |
|---|---|---|---|
| `NOT_FOUND` | 404 | Unknown `token_id`, or the caller doesn't own it, or it belongs to another institution. | `{ "resource_type": "fcm_token", "resource_id": "b4c9…" }` |
| `AUTHENTICATION_FAILED` | 401 | Missing, malformed, or expired JWT. | `{}` |
| `VALIDATION_ERROR` | 422 | `token` empty, `device_type` not in `{ios, android, web}`, `token` over 500 chars. | `{ "field": "token" }` |

## Audit Events Emitted

| Event | When |
|---|---|
| `notification.token.registered` | First successful token registration for the caller. |
| `notification.token.revoked` | Caller deleted the token, or FCM returned `UNREGISTERED`, or another user re-bound the same token string. |
| `notification.batch.sent` | A Celery push batch completed successfully. |
| `notification.batch.failed` | A Celery push batch errored out (FCM transport error, no active tokens, missing source notification row). |

## Prometheus Metric

The Celery push task increments
`dutta_notifications_delivered_total{result="success"|"failure"}` on
every completed FCM batch. SLO 5 in `docs/design/slo.md` reads from this
counter.

## Cross-Tenant Isolation

`fcm_tokens`, `notifications`, and `notification_batches` have **no
`institution_id` column** — isolation is transitive through
`users.institution_id`. Every service method either owns the target via
`user_id = current_user.user_id` or calls
`assert_same_institution(...)` before mutating. All cross-tenant probes
return 404 (never 403) per `docs/design/tenant-isolation.md` §1.1.
