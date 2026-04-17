# Architecture Overview

> **Read this first.** This document describes the complete system architecture of DuttaMessenger — a private messaging platform built for institutional use.

---

## System Context

DuttaMessenger is a private, institution-scoped messaging application. It is NOT a public social network. Key implications:

- **Closed user base**: Users are added by administrators, not via public sign-up.
- **Institution-level ACL**: The institution admin controls who can create groups, who can message whom, and what roles exist.
- **No voice/video calling**: Text, file sharing, and media only.
- **Data sovereignty**: All data stays on infrastructure we control.
- **Dual group modes**: Groups can operate as simple single-chat groups (like WhatsApp) or as topic-enabled groups with subchannels (like Telegram Topics).

---

## Why Not Firebase

> Full rationale: [docs/adr/001-why-not-firebase.md](docs/adr/001-why-not-firebase.md)

Firebase was used in the initial prototype. We are migrating away for these reasons:

1. **Vendor lock-in**: Firebase's proprietary data model makes migration painful later.
2. **Cost unpredictability**: Firestore reads/writes pricing scales poorly with chat volume.
3. **Limited query capability**: Complex queries (search messages, ACL checks, analytics) are weak in Firestore.
4. **No relational integrity**: Chat applications have deeply relational data (users → groups → topics → messages → replies → read receipts). A relational database with foreign keys and joins is the correct tool.
5. **Testability**: Firebase emulators are fragile. A standard PostgreSQL + Python stack is trivially testable.

---

## Tech Stack

### Backend

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Language** | Python 3.12+ | Team familiarity, rich ecosystem, excellent for rapid API development |
| **Framework** | FastAPI | Async-first, automatic OpenAPI docs, Pydantic validation, WebSocket support built-in |
| **Database** | PostgreSQL 16 | Relational integrity for chat data, JSONB for flexible metadata, full-text search, proven at scale |
| **ORM** | SQLAlchemy 2.0 (async) | Type-safe queries, migration support via Alembic, async session management |
| **Migrations** | Alembic | Version-controlled schema changes |
| **Cache** | Redis 7 | Session store, online status tracking, pub/sub for WebSocket scaling, rate limiting |
| **File Storage** | MinIO (dev) / S3 (prod) | S3-compatible object storage for media files |
| **WebSocket** | FastAPI WebSocket + Redis Pub/Sub | Real-time message delivery, typing indicators, online status |
| **Task Queue** | Celery + Redis | Async jobs: push notifications, media processing, cleanup tasks |
| **Push Notifications** | Firebase Cloud Messaging (FCM) | Industry standard for mobile push — we use ONLY the push notification service, not the database |

### Frontend

| Layer | Technology |
|-------|-----------|
| **Framework** | Flutter |
| **State Management** | (Flutter team decides — document in flutter-architecture.md) |
| **WebSocket Client** | web_socket_channel package |
| **Local Storage** | SQLite via drift/sqflite for offline message cache |

### Infrastructure

| Component | Technology |
|-----------|-----------|
| **Containerization** | Docker + Docker Compose |
| **CI/CD** | GitHub Actions |
| **Reverse Proxy** | Nginx (WebSocket upgrade support) |
| **Monitoring** | Prometheus + Grafana (future) |
| **Logging** | Structured JSON logs → stdout → log aggregator |

---

## Dual Group Mode — Core Concept

Groups support two modes. The admin chooses the mode at creation time.

```
┌─────────────────────────────────────────────────────────┐
│                    SIMPLE MODE                          │
│            (like WhatsApp groups)                       │
│                                                         │
│  Group ──── has one ──── Conversation ──── Messages     │
│                                                         │
│  - All members chat in one shared stream                │
│  - No topics, no subchannels                            │
│  - Simplest mental model                                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                 TOPIC-ENABLED MODE                      │
│          (like Telegram Topics / Discord)               │
│                                                         │
│  Group ──── has many ──── Topics                        │
│                             │                           │
│                        each has one                     │
│                             │                           │
│                        Conversation ──── Messages       │
│                                                         │
│  - "General" topic auto-created (cannot be deleted)     │
│  - Admin creates additional topics                      │
│  - Each topic can be read-write or read-only            │
│  - Per-topic access control                             │
│  - Unread counts are per-topic                          │
│  - Pinned messages are per-topic                        │
└─────────────────────────────────────────────────────────┘
```

**Mode conversion**: A simple group can be upgraded to topic-enabled (existing conversation becomes the "General" topic). Topic-enabled cannot be downgraded to simple (destructive — would lose topic separation).

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FLUTTER APP                              │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │  REST     │  │  WebSocket   │  │  Local SQLite Cache       │  │
│  │  Client   │  │  Client      │  │  (offline messages)       │  │
│  └────┬─────┘  └──────┬───────┘  └───────────────────────────┘  │
└───────┼───────────────┼─────────────────────────────────────────┘
        │               │
        │ HTTPS          │ WSS
        ▼               ▼
┌───────────────────────────────────────┐
│            NGINX (Reverse Proxy)      │
│   - TLS termination                   │
│   - WebSocket upgrade (/ws/*)         │
│   - Rate limiting (basic)             │
│   - Static file serving               │
└───────────┬───────────┬──────────────┘
            │           │
    REST    │           │  WebSocket
            ▼           ▼
┌───────────────────────────────────────┐
│          FASTAPI APPLICATION          │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │         API Router              │  │
│  │  /api/v1/auth/*                 │  │
│  │  /api/v1/users/*                │  │
│  │  /api/v1/chat/*                 │  │
│  │  /api/v1/groups/*               │  │
│  │  /api/v1/groups/{id}/topics/*   │  │
│  │  /api/v1/media/*                │  │
│  │  /api/v1/notifications/*        │  │
│  └─────────────────────────────────┘  │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │     WebSocket Manager           │  │
│  │  /ws/chat                       │  │
│  │  - Connection registry          │  │
│  │  - Room-based routing           │  │
│  │  - Heartbeat/ping-pong          │  │
│  └─────────────────────────────────┘  │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │     Middleware Stack            │  │
│  │  1. Request ID                  │  │
│  │  2. Structured Logger           │  │
│  │  3. Auth (JWT verification)     │  │
│  │  4. Rate Limiter                │  │
│  │  5. ACL Checker                 │  │
│  └─────────────────────────────────┘  │
└───────┬───────────┬──────────┬───────┘
        │           │          │
        ▼           ▼          ▼
   ┌─────────┐ ┌────────┐ ┌────────────┐
   │PostgreSQL│ │ Redis  │ │ MinIO / S3 │
   │          │ │        │ │            │
   │ - Users  │ │- Sessions│ │- Images   │
   │ - Groups │ │- Online │ │- Videos    │
   │ - Topics │ │  status│ │- Documents │
   │ - Messages│ │- Pub/Sub│ │- Audio     │
   │ - ACL    │ │- Rate   │ │            │
   │ - Media  │ │  limits │ │            │
   │   refs   │ │         │ │            │
   └─────────┘ └────┬───┘ └────────────┘
                     │
                     ▼
              ┌──────────────┐
              │ Celery Worker│
              │              │
              │ - Push notif │
              │ - Media proc │
              │ - Cleanup    │
              └──────────────┘
```

---

## Data Flow: Sending a Message

This is the most critical flow in the system. Every developer should understand it. This flow is identical for DMs, simple groups, and topic-enabled groups — the message always goes into a `conversation`, regardless of how that conversation was created.

```
1. User A types message in Flutter app
2. Flutter sends WebSocket frame:
   {
     "type": "message.send",
     "conversation_id": "conv_xxx",          ← could be DM, simple group, or topic
     "content": "Hello",
     "reply_to_message_id": "msg_yyy" | null,
     "client_message_id": "uuid-from-client"  ← for deduplication
   }

3. FastAPI WebSocket handler receives frame
4. Handler validates:
   - User is authenticated (JWT in WebSocket handshake)
   - User is a member of conversation_id (DB check, cached in Redis)
   - If topic conversation: check topic is not read-only (or user is admin)
   - Message content passes validation (length, content type)
   - If reply_to_message_id is set, verify it exists in this conversation

5. Handler persists message to PostgreSQL:
   INSERT INTO messages (id, conversation_id, sender_id, content,
                         reply_to_message_id, client_message_id, ...)

6. Handler publishes to Redis Pub/Sub channel:
   PUBLISH conversation:{conv_id} {message_json}

7. All FastAPI instances subscribed to that channel receive the event

8. Each instance checks its local WebSocket connections:
   - Find all users connected who are members of conv_id
   - Send WebSocket frame to each:
     {
       "type": "message.new",
       "message": { ... full message object ... }
     }

9. For users NOT currently connected (offline):
   - Celery task fires push notification via FCM

10. Flutter app receives WebSocket frame → updates UI
    Flutter app also caches message in local SQLite
```

---

## Security Model

### Authentication
- **Registration**: Admin-invited only (admin creates user → user receives invite link → sets password)
- **Login**: Email + password → returns JWT access token (15 min) + refresh token (7 days)
- **JWT**: Signed with RS256, contains `user_id`, `institution_id`, `roles[]`
- **WebSocket Auth**: JWT passed as query parameter during WebSocket handshake, verified once on connection

### Authorization (ACL)
- **Institution-level roles**: `super_admin`, `admin`, `member`
- **Group-level roles**: `owner`, `admin`, `member`
- **Topic-level permissions**: `read_write` (members can post), `read_only` (only admins can post)
- **Permission checks**: Every API endpoint and WebSocket event checks permissions via ACL middleware
- **Principle of least privilege**: Default role is `member` with minimal permissions

### Data Security
- **TLS everywhere**: All HTTP and WebSocket traffic over TLS
- **Password hashing**: bcrypt with cost factor 12
- **Input validation**: Pydantic models validate every request body
- **SQL injection prevention**: SQLAlchemy parameterized queries only — no raw SQL string formatting
- **File upload validation**: MIME type checking, file size limits, virus scanning (future)
- **Rate limiting**: Per-user, per-endpoint limits stored in Redis

---

## Scaling Considerations

For an institutional app (likely < 10,000 users), this architecture is more than sufficient. But it is designed to scale horizontally if needed:

- **Multiple FastAPI instances**: Stateless app servers behind Nginx load balancer
- **Redis Pub/Sub**: Ensures WebSocket messages reach users regardless of which instance they're connected to
- **PostgreSQL read replicas**: For read-heavy queries (message history, search)
- **S3/MinIO**: Object storage scales independently
- **Celery workers**: Scale horizontally for notification/media processing load

---

## Module Dependency Graph

```
auth ──────────────────────────────────────┐
  │                                         │
  ▼                                         │
users ──► acl ◄── groups (+ topics)         │
  │         │        │                      │
  │         ▼        ▼                      │
  └──────► chat ◄────┘                      │
              │                             │
              ▼                             │
           media                            │
              │                             │
              ▼                             │
        notifications ◄────────────────────┘
```

**Dependency rules:**
- `auth` depends on nothing — it is the foundation.
- `users` depends on `auth` (user must be authenticated).
- `acl` depends on `auth` and `users` (permissions are assigned to users).
- `groups` depends on `auth`, `users`, and `acl`. Groups module also manages topics.
- `chat` depends on `auth`, `users`, `groups`, and `acl`. Chat operates on conversations — it does not care whether a conversation belongs to a DM, simple group, or topic.
- `media` depends on `auth` (file upload requires authentication).
- `notifications` depends on `auth` and `users` (push tokens belong to users).

**Build order**: auth → users → acl → groups (with topics) → chat → media → notifications

---

## Schema Overview

```
┌──────────────────┐     ┌──────────────────────┐
│     users         │     │    institutions       │
│─────────────────  │     │──────────────────────│
│ id (PK)           │◄────│ id (PK)              │
│ institution_id(FK)│     │ name                  │
│ email             │     │ slug                  │
│ display_name      │     │ settings (JSONB)      │
│ ...               │     └──────────────────────┘
└──────┬───────────┘
       │
       ▼
┌──────────────────────┐
│  groups              │
│──────────────────────│
│ id (PK)              │
│ institution_id (FK)  │
│ name                 │
│ mode (simple|topics) │    ← THE KEY FIELD
│ conversation_id (FK) │    ← set for simple mode, NULL for topic mode
│ created_by (FK)      │
│ settings (JSONB)     │
└──────┬───────────────┘
       │
       │ (only in topic-enabled mode)
       ▼
┌──────────────────────┐
│  topics              │
│──────────────────────│
│ id (PK)              │
│ group_id (FK)        │
│ conversation_id (FK) │    ← each topic has its own conversation
│ name                 │
│ icon_emoji           │
│ access_mode          │    ← 'read_write' or 'read_only'
│ is_default           │    ← TRUE for "General" topic
│ sort_order           │
└──────────────────────┘
       │
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│  conversations       │     │  conversation_members │
│──────────────────────│     │──────────────────────│
│ id (PK)              │     │ conversation_id (FK) │
│ type (dm|group|topic)│     │ user_id (FK)         │
│ ...                  │     │ role                 │
└──────┬───────────────┘     └──────────────────────┘
       │
       ▼
┌──────────────────────┐
│  messages            │
│──────────────────────│
│ id (PK)              │
│ conversation_id (FK) │     ← messages don't know about groups or topics
│ sender_id (FK)       │       they only know their conversation
│ content              │
│ reply_to_id (FK)     │
│ pinned_at            │     ← if set, message is pinned
│ pinned_by (FK)       │
│ ...                  │
└──────────────────────┘
```

**The elegance of this design:** The `messages` table doesn't know whether it belongs to a DM, a simple group, or a topic. It only knows its `conversation_id`. All the complexity of "which group mode is this?" lives in the groups and topics layer above. The chat module is completely agnostic.
