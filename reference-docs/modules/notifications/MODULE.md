# Module: Notifications

> **This module handles push notifications and in-app notification feed.** It uses Firebase Cloud Messaging (FCM) for push delivery — the ONLY Firebase service we use.

---

## What This Module Does

- Register device push tokens (FCM tokens from Flutter)
- Send push notifications when users are offline
- Manage notification preferences (per-user, per-conversation mute)
- Provide an in-app notification feed (unread counts, notification list)

## Dependencies

| Depends On | Why |
|-----------|-----|
| `auth` | Must know the user to send notifications |
| `users` | Notification preferences stored per user |
| `chat` | Triggers notifications on new messages |

---

## Push Notification Flow

```
1. Flutter app obtains FCM token on startup
2. Flutter registers token with backend:
   POST /api/v1/notifications/tokens
   { "token": "fcm_token_string", "device_id": "unique_device_id", "platform": "android|ios" }

3. When a message is sent (chat module):
   - WebSocket delivers to online users immediately
   - For offline users: Celery task is queued

4. Celery worker processes notification:
   a. Check if recipient has notifications enabled (user_settings)
   b. Check if conversation is muted (conversation_members.muted_until)
   c. If allowed, fetch recipient's FCM tokens (they may have multiple devices)
   d. Build notification payload
   e. Send via FCM
   f. Log the notification in notification_log table

5. Flutter receives FCM notification → shows system notification
   User taps notification → app opens to the relevant conversation
```

---

## Notification Payload

```json
{
  "notification": {
    "title": "Rajesh",
    "body": "Hello! How are you doing?"
  },
  "data": {
    "type": "new_message",
    "conversation_id": "uuid",
    "message_id": "uuid",
    "sender_id": "uuid",
    "sender_name": "Rajesh"
  }
}
```

For group messages:
```json
{
  "notification": {
    "title": "Engineering Team",
    "body": "Rajesh: Hello! How are you doing?"
  },
  "data": {
    "type": "new_message",
    "conversation_id": "uuid",
    "message_id": "uuid",
    "sender_id": "uuid",
    "sender_name": "Rajesh",
    "group_name": "Engineering Team"
  }
}
```

---

## Notification Batching

To avoid notification spam in active group chats:

- If a user receives 3+ notifications from the same conversation within 30 seconds, batch them into one:
  `"Engineering Team: 5 new messages"`
- Use Redis to track recent notification timestamps per user per conversation.

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/notifications/tokens` | Register FCM device token |
| `DELETE` | `/api/v1/notifications/tokens/{device_id}` | Unregister device token (on logout) |
| `GET` | `/api/v1/notifications/unread-count` | Get total unread count across conversations |
| `POST` | `/api/v1/notifications/mark-read` | Mark notifications as read |

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `notification_tokens` | FCM tokens per user per device |
| `notification_log` | Audit log of sent notifications (for debugging) |

```sql
CREATE TABLE notification_tokens (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       TEXT NOT NULL,
    device_id   VARCHAR(255) NOT NULL,
    platform    VARCHAR(10) NOT NULL CHECK (platform IN ('android', 'ios')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, device_id)
);

CREATE TABLE notification_log (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    recipient_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id     UUID REFERENCES conversations(id) ON DELETE SET NULL,
    notification_type   VARCHAR(30) NOT NULL,
    payload             JSONB NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'sent'
                        CHECK (status IN ('sent', 'delivered', 'failed')),
    fcm_response        JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports: Querying notification history for a user (debugging)
CREATE INDEX idx_notification_log_recipient
    ON notification_log (recipient_id, created_at DESC);
```

---

## Token Lifecycle

1. **Registration**: On app startup, Flutter gets FCM token → sends to backend.
2. **Refresh**: FCM tokens can change. Flutter listens for `onTokenRefresh` → sends updated token.
3. **Logout**: On logout, Flutter calls `DELETE /tokens/{device_id}` to stop push for that device.
4. **Stale tokens**: If FCM returns `UNREGISTERED` error, backend deletes the token automatically.
5. **Multi-device**: A user can have tokens for multiple devices. All devices receive the push.
