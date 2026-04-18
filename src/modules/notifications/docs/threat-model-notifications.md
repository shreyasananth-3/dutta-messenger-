# Threat Model — Notifications

## 1. Scope

- Registers Flutter FCM device tokens, fans out push notifications for new
  messages, and exposes the in-app notification feed.
- Data owned: `fcm_tokens`, `notifications`, `notification_batches`.
- External interfaces:
  - HTTP: `POST/DELETE /api/v1/notifications/tokens[/{id}]`,
    `GET /api/v1/notifications/unread-count`,
    `POST /api/v1/notifications/mark-read`.
  - Celery task `notifications.send_push_batch` → Firebase Cloud Messaging (FCM).
  - No WebSocket events (online delivery lives in chat).

## 2. Trust boundaries

- All HTTP routes require a valid JWT (`get_current_user`); no unauthenticated
  surface. Tokens and notifications are scoped by `user_id`, which the route
  reads from the verified JWT — clients never pass a `user_id` in the body.
- Tenant boundary: `fcm_tokens`, `notifications`, `notification_batches`
  carry no `institution_id`. Isolation is transitive through
  `users.institution_id` (see `docs/design/tenant-isolation.md` §1.2). Every
  service method fetches the target token/notification and asserts the owning
  user's `institution_id` matches the caller's.
- External call-out: FCM (HTTPS). Credentials held as service-account key
  material, loaded via `src/config.py` (never committed). CI uses a mock FCM
  client; production uses `firebase-admin`.

## 3. STRIDE analysis

| Threat | Applies? | Mitigation |
|---|---|---|
| **S**poofing — can an attacker impersonate another user's device? | yes | JWT verified on every request; token registration is scoped to `current_user.user_id`; a collision on the globally-unique `token` column rebinds the FCM token to the calling user only after we revoke the previous binding and write an audit row. |
| **T**ampering | yes | Parameterised SQLAlchemy only; FCM payloads are signed in transit via TLS; audit row accompanies every mutation in the same transaction. |
| **R**epudiation | yes | `audit_logs` row on `notification.token.registered`, `notification.token.revoked`, `notification.batch.sent`, `notification.batch.failed`. |
| **I**nformation disclosure — cross-tenant leak | yes | `assert_same_institution()` on every token/notification lookup; cross-tenant fuzz test asserts 404 for inter-institution access. |
| **D**enial of service | yes | Per-user rate limit on token register (`300/minute` default rule inherited from `RATE_LIMIT_DEFAULT`); Celery batching caps worst-case fan-out; FCM failures do not retry inline. |
| **E**levation of privilege | yes | No admin-only surface in this slice; every mutation is bound to the caller's identity. |

## 4. Abuse cases

- **Harvesting active device tokens of another user.** A compromised account
  cannot query tokens for another user — the `GET` equivalent does not exist.
  The unread-count and mark-read endpoints only read rows where
  `user_id = current_user.id`.
- **Spam fan-out to drain FCM quota.** The fanout service is only reachable
  from the chat module (via Celery enqueue), never from an HTTP route. A
  compromised chat path is the real risk — `docs/design/slo.md` caps delivery
  budgets and the metric `dutta_notifications_delivered_total{result="failure"}`
  is alerted when the rate drops below 99 %.
- **Cross-tenant token collision.** Two institutions cannot share the same
  FCM token row because the column is `UNIQUE`; registering the same string
  twice rebinds it to the latest caller after revocation + audit.
- **Stale-token storm to FCM.** `UNREGISTERED` responses from FCM cause the
  Celery task to flip `is_active = false` and emit
  `notification.token.revoked`, preventing further attempts.

## 5. Data handling

- PII touched: FCM registration tokens (device identifier), notification
  titles and bodies (may contain message previews).
- Retention: tokens belong to a user; when the user is tombstoned
  (`users.deleted_at IS NOT NULL`), tokens are eligible for purge 30 days
  later — implemented by the users-module erasure pipeline
  (`docs/design/privacy-erasure.md` §retention table). Our code supports
  this by leaving `fcm_tokens.user_id` as a plain FK so the users module
  can bulk-update `is_active = false` on tombstone and delete rows at the
  30-day mark. Audit rows survive for 7 years (per privacy-erasure.md).
- Encryption at rest: Postgres default; FCM traffic over TLS.
- Right-to-erasure: implemented by the users module, not here — we only own
  the mutation primitives (deactivate, purge) it calls.

## 6. Logging & monitoring

- structlog events:
  `notification_token_registered`, `notification_token_revoked`,
  `notification_batch_sent`, `notification_batch_failed`,
  `fcm_unregistered_token_deactivated`.
- Prometheus: `dutta_notifications_delivered_total{result}` Counter
  (new this module; feeds SLO 5 in `docs/design/slo.md`).
- Alerts: `DuttaPushRateLow` fires when the delivery rate over three
  consecutive 60 s windows drops below 99 % (backlog ticket, not a page).

## 7. Open risks

- **FCM quota exhaustion is not actively monitored** inside our service. We
  rely on FCM response codes. Follow-up: wire a `dutta_fcm_quota_exhausted`
  counter once Stage 6 load tests surface a realistic budget.
- **Institution offboarding does not cascade into FCM token deletion on the
  FCM side.** We deactivate locally; FCM's own server-side token lifecycle
  handles the rest. Accepted — no better fix available without per-token
  deletion calls and quota cost.
- **No client-side acknowledgement of push receipt.** SLO 5's "delivered"
  label reflects FCM's ack, not the user's device actually rendering the
  push. Acceptable for the 99 % / 60 s target; revisit if the product
  demands device-side receipts.
