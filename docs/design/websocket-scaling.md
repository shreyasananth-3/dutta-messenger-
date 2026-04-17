---
title: "WebSocket Scaling — Chat Fanout for 1k–5k Users"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - src/modules/chat/
  - src/shared/middleware/
---

# WebSocket Scaling — Chat Fanout for 1k–5k Users

## Context

The `chat` module (Stage 4, Chain A) is the largest module in DuttaMessenger. Its WebSocket
layer must deliver messages in real-time to all online members of a conversation — which, for
a school-wide announcement topic, can be up to ~5 000 users simultaneously.

Without a deliberate design for the fanout path, naive approaches (e.g., iterating over all
connected sockets in-process) collapse immediately in a multi-process deployment. This RFC
decides the transport, fanout mechanism, heartbeat, reconnect semantics, backpressure
strategy, connection limits, and auth-token lifecycle so that Stage-4 module authors can
implement without ambiguity.

The target deployment is **1–5 000 users per instance**, running on **one or two app
processes** behind a load balancer. This scale is well within what plain Redis pub/sub can
handle — no NATS, no Redis Streams, no Kafka. (Explicitly ruled out in
`now-go-through-the-twinkly-wombat.md`.)

If a future deployment grows beyond ~20 000 concurrent connections, or requires message replay
guarantees at the broker level, this decision should be revisited. Until that threshold, the
simpler design wins.

## Decision

Use plain FastAPI WebSocket (`@app.websocket("/ws/chat")`), authenticate via JWT sent as the
first text frame after the TCP handshake, fan out to conversation members via one Redis pub/sub
channel per conversation (`chat:conv:{conversation_id}`), heartbeat using WebSocket protocol
ping/pong frames (not JSON), replay missed messages from PostgreSQL on reconnect (bounded to
500 messages, older history via HTTP), enforce a per-connection async send queue with a 1 000
message hard cap, and cap each user at 5 concurrent connections with a per-institution ceiling
of `user_count × 10`.

## Details

### Scope

- WebSocket endpoint: `wss://{host}/ws/chat`
- Authentication at the WS layer
- Fanout architecture (Redis pub/sub)
- Heartbeat / liveness
- Reconnect and missed-message replay
- Backpressure and slow-client handling
- Connection limits and rate enforcement
- Auth token lifecycle for long-lived connections
- Observability (metrics that already exist)

### Non-goals

- HTTP REST endpoints (see `reference-docs/modules/chat/MODULE.md` and the API.md that will
  be created in Stage 4)
- Celery push-notification dispatch for offline users (see `notifications` module)
- Media upload/download (see `media` module)
- Message partitioning / archival (see `docs/design/message-partitioning.md`)
- Multi-region or cross-datacenter replication
- End-to-end encryption (explicitly deferred for this scale)

### Fanout Architecture

```
Client A (device 1)         App Process 1                    Redis
    │                            │                              │
    │  WS frame: message.send    │                              │
    ├──────────────────────────► │  1. persist to Postgres      │
    │                            │  2. PUBLISH chat:conv:{id}  ─┼──► channel
    │  message.sent (ack)  ◄─────┤                              │
    │                            │                              │
                                                                │
                         App Process 2                          │
                              │    SUBSCRIBE chat:conv:{id}  ◄──┤
                              │                                 │
                    ┌─────────┴──────────┐
                    │ connection manager │
                    │  user_B → socket  │
                    │  user_C → socket  │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
            Client B (device 1)    Client C (device 1)
             message.new            message.new
```

Each app process maintains an in-memory mapping:

```
conversation_id → set[WebSocket]
user_id         → set[WebSocket]
```

When process 1 publishes to `chat:conv:{id}`, all processes that have at least one connected
member subscribed to that channel receive the event and forward it to their local sockets.

**Why this is correct at 1–5k users:**

A 5 000-member school with 50% online at peak gives ~2 500 concurrent connections. At one
message per second in a busy group, Redis pub/sub handles this with sub-millisecond internal
latency. The extra hop from sender's process → Redis → other processes adds ~1 ms. That is
negligible against the `message delivery p95 < 2 s` SLO defined in `docs/design/slo.md`.
Sticky sessions are not required because every process can serve every user — the Redis
channel is the rendezvous point.

**Channel naming:**

| Channel | Purpose |
|---------|---------|
| `chat:conv:{conversation_id}` | All events for one conversation |
| `presence:{institution_id}` | Online/offline presence broadcasts |
| `typing:{conversation_id}` | Typing indicators (TTL-based, ephemeral) |

Processes subscribe to conversation channels on demand: when the first user in a conversation
connects, the process subscribes. When the last user disconnects, it unsubscribes. This keeps
the subscription list small.

### Transport and Authentication

**Endpoint:** `wss://{host}/ws/chat`

**Auth method: first-message JWT** (not `?token=` query parameter).

Justification: Query parameters are logged by every reverse proxy, CDN, and load balancer by
default. A long-lived JWT in a query string will appear in access logs, exposing tokens to
anyone with log access. The WebSocket upgrade request is HTTPS-protected, but the URL
(including query string) is visible in server logs after TLS termination. Sending the token as
the first JSON frame keeps it inside the encrypted WebSocket payload and never in a log line.

Handshake sequence:

```
1. Client opens WS connection (no token in URL).
2. Server accepts the TCP/WS handshake — no auth yet.
3. Server waits up to 10 s for the first frame.
4. Client sends:
   {
     "type": "auth",
     "payload": { "token": "<access_token>" },
     "request_id": "<uuid>"
   }
5. Server verifies JWT (RS256, same key as HTTP middleware).
6a. Valid  → server sends connection.established; connection is live.
6b. Invalid → server closes with code 4001 "Invalid token".
6c. Expired → server closes with code 4002 "Token expired".
7. All subsequent frames are processed; unauthenticated frames before step 4 ack are rejected.
```

If no auth frame arrives within 10 s, the server closes with `4001`.

### Auth Token Lifecycle

WS connections use the **same short-lived access token** issued by `POST /auth/login` or
`POST /auth/refresh`. No separate WS-ticket endpoint is introduced.

Rationale: A dedicated `POST /auth/ws-ticket` would be a parallel token issuance path that
the auth module (Gap C in `MANUAL_SMOKE.md`) would have to maintain — additional surface area
with no security benefit at this scale, since the access token is already short-lived (15 min)
and the WS connection re-authenticates on reconnect.

**Token expiry during an active connection:**

The server does not forcibly close the socket when the embedded JWT expires. Instead:
- The server records `token_expires_at` when the connection is established.
- 60 s before expiry, the server emits:
  ```json
  { "type": "token.expiring", "payload": { "expires_in_seconds": 60 } }
  ```
- The client must call `POST /auth/refresh` and send a new `auth` frame over the same socket:
  ```json
  { "type": "auth.refresh", "payload": { "token": "<new_access_token>" }, "request_id": "<uuid>" }
  ```
- Server validates and replies `auth.refreshed`. The socket remains open — no reconnect storm.
- If the token expires and no refresh arrives within the grace window (60 s past expiry),
  the server closes with `4002`.

This approach coordinates with refresh-token rotation (Gap C) without introducing a new
endpoint: the refresh path is the same `POST /auth/refresh` endpoint already built.

### Heartbeat

Server sends a WebSocket protocol-level **ping frame** every 30 s. The client's WebSocket
library (Flutter's `web_socket_channel`) automatically replies with a **pong frame** — no
application-level code required on the client.

Why protocol-level ping/pong rather than JSON heartbeat:

1. RFC 6455 defines ping/pong at the framing layer. Compliant clients (including all Flutter
   WebSocket implementations) handle it transparently.
2. A JSON heartbeat wastes one round-trip of application parsing on every tick.
3. Some load balancers (nginx, AWS ALB) detect protocol-level ping/pong and reset their idle
   connection timeout, which prevents spurious disconnects at the infrastructure layer.
4. Reduces code: no `heartbeat_task` event type, no client-side pong handler to maintain.

If no pong is received within 10 s of a ping, the server closes the socket
(`WebSocket.close(1001, "Heartbeat timeout")`). The client must then reconnect with
exponential backoff (1 s → 2 s → 4 s → 8 s, capped at 30 s) and the reconnect flow
described below.

### Reconnect and Missed-Message Replay

On reconnect, the client sends its auth frame (step 4 above) with an additional field:

```json
{
  "type": "auth",
  "payload": {
    "token": "<access_token>",
    "resume": {
      "last_seen_message_id": "<uuid | null>"
    }
  },
  "request_id": "<uuid>"
}
```

If `last_seen_message_id` is present, the server replays missed messages for every
conversation the user is a member of, bounded to the **500 most recent** messages per
conversation since that cursor.

**DB query for replay:**

```sql
SELECT *
FROM   messages
WHERE  conversation_id = ANY(:conversation_ids)
  AND  created_at > (SELECT created_at FROM messages WHERE id = :last_seen_id)
  AND  deleted_at IS NULL
ORDER  BY conversation_id, created_at ASC
LIMIT  500;
```

The index `idx_messages_conversation_created ON messages (conversation_id, created_at DESC)`
(defined in `SCHEMA.sql`) makes this a fast index scan for each conversation. For 5 000-member
conversations, replaying 500 messages is a single range scan over an already-hot B-tree page.

**Boundary condition:**

If the gap is larger than 500 messages, the server sends:
```json
{ "type": "replay.truncated", "payload": { "conversation_id": "<uuid>", "oldest_replayed_at": "<ISO>" } }
```
The client must then fetch older history via:
```
GET /api/v1/chat/conversations/{id}/messages?after_cursor=<cursor>
```
This keeps the WS path simple and the HTTP path the canonical source for deep history.
For very stale clients (last seen > 30 days), the `message-partitioning.md` RFC governs
archival; those messages may not be in the hot table at all.

### Backpressure

Each WebSocket connection has an **async send queue** (Python `asyncio.Queue`). A dedicated
`_sender` coroutine drains this queue and writes to the socket. The producer (Redis subscriber
callback) enqueues without blocking.

```python
MAX_QUEUE_SIZE = 1_000  # messages per connection

async def _enqueue(ws_state: ConnectionState, event: dict) -> None:
    if ws_state.send_queue.qsize() >= MAX_QUEUE_SIZE:
        # Drop the oldest item to make room
        try:
            ws_state.send_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        await ws_state.send_queue.put({"type": "backpressure_truncated", "payload": {}})
    await ws_state.send_queue.put(event)
```

The `backpressure_truncated` event signals the client to fetch missed events via HTTP. This
prevents a single slow client (bad network, paused device) from blocking the fanout pipeline
or consuming unbounded memory on the server.

The `_sender` coroutine applies a 5 s write timeout per frame. If the socket write stalls for
longer than 5 s, the connection is considered dead and closed cleanly.

### Connection Limits

**Per-user cap: 5 concurrent connections.**

Tracked in Redis: `ws:connections:user:{user_id}` → sorted set of `{connection_id}` with
score = Unix timestamp of connect time. On connect, ZCARD is checked; if ≥ 5, the oldest
connection is closed with `4009 "Duplicate connection"` and the new one proceeds. This allows
multi-device use (phone, tablet, web, two desktops) while preventing runaway reconnect loops
from consuming resources.

**Per-institution ceiling: `user_count × 10`.**

Tracked in Redis: `ws:connections:institution:{institution_id}` → integer counter
(INCR on connect, DECR on disconnect with a TTL-based floor to prevent stuck counters).

If the ceiling is breached, new connections are rejected with HTTP 429 before the WS upgrade
completes, with header `Retry-After: 60`.

```python
# In the WS route handler, before accepting the upgrade:
count = await redis.incr(f"ws:connections:institution:{institution_id}")
await redis.expire(f"ws:connections:institution:{institution_id}", 86400)
if count > settings.WS_INSTITUTION_MAX_CONNECTIONS:
    await redis.decr(f"ws:connections:institution:{institution_id}")
    raise HTTPException(status_code=429, headers={"Retry-After": "60"})
```

`WS_INSTITUTION_MAX_CONNECTIONS` defaults to `user_count × 10`, configurable via
`src/config.py`. For a 1 000-user school this is 10 000 — effectively unlimited at that
scale; the cap becomes meaningful at 5 000 users (50 000 max, still well within a single
Redis instance).

### Observability

Two existing metrics in `src/shared/observability/metrics.py` cover this RFC:

| Metric | Type | Labels | What it measures |
|--------|------|--------|-----------------|
| `dutta_websocket_connections` | Gauge | `institution_id` | Current live connections |
| `dutta_message_delivery_latency_seconds` | Histogram | `conversation_type` | Time from persist → all sends dispatched |

No new metrics are introduced. The gauge is incremented on connect and decremented on
disconnect (inside a `try/finally` block to guarantee cleanup on crash). The histogram is
observed after the Redis PUBLISH returns, measuring the local fanout half; the
cross-process half (Redis → subscriber → socket write) is covered by the heartbeat
round-trip latency visible in the histogram's tail.

Structured log fields added to every WS log line:

```python
logger.info(
    "ws_event_dispatched",
    user_id=str(user_id),
    institution_id=str(institution_id),
    conversation_id=str(conversation_id),
    event_type=event_type,
    correlation_id=correlation_id,
    queue_depth=ws_state.send_queue.qsize(),
)
```

### Implementation Sketch

**Files touched:**

```
src/modules/chat/websocket/
    handler.py        ← WS route, auth handshake, heartbeat loop, reconnect replay
    events.py         ← Event type constants, Pydantic models for each frame type
    connection_manager.py  ← In-memory registry + Redis sync helpers
    fanout.py         ← Redis subscriber coroutine, enqueue helpers

src/shared/redis.py   ← Already exists; expose get_pubsub() helper
src/config.py         ← WS_INSTITUTION_MAX_CONNECTIONS, WS_MAX_QUEUE_SIZE, WS_HEARTBEAT_INTERVAL_S
```

**Key data structures:**

```python
@dataclass
class ConnectionState:
    user_id: uuid.UUID
    institution_id: uuid.UUID
    websocket: WebSocket
    send_queue: asyncio.Queue          # bounded, drained by _sender coroutine
    token_expires_at: datetime
    connection_id: uuid.UUID = field(default_factory=uuid.uuid4)

# In-memory per-process registry
_connections: dict[uuid.UUID, set[ConnectionState]]  # conversation_id → states
_user_connections: dict[uuid.UUID, set[ConnectionState]]  # user_id → states
```

**Redis key layout:**

```
ws:connections:user:{user_id}           ZSET  score=epoch  member=connection_id
ws:connections:institution:{iid}        INT   INCR/DECR    active connection count
chat:conv:{conversation_id}             PubSub channel
presence:{institution_id}              PubSub channel
typing:{conversation_id}               PubSub channel  (messages auto-expire in Redis)
```

**Non-obvious pseudocode — cross-process fan-out subscriber:**

```python
async def run_redis_subscriber(pubsub: aioredis.client.PubSub) -> None:
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        channel: str = message["channel"].decode()
        data: dict = json.loads(message["data"])
        conversation_id = uuid.UUID(channel.removeprefix("chat:conv:"))
        for conn_state in _connections.get(conversation_id, set()).copy():
            await _enqueue(conn_state, data)
```

This coroutine runs as a single background task per process. It never blocks the event loop
because `_enqueue` is async and the queue is bounded.

### Alternatives Considered

**Redis Streams instead of pub/sub:** Streams give a persistent log with consumer groups and
replay guarantees at the broker level. Rejected because: (a) ruled out explicitly for ≤5k
users in `now-go-through-the-twinkly-wombat.md`; (b) replay is already handled by PostgreSQL,
which is the authoritative store; (c) Streams add consumer-group management complexity with no
benefit at this scale.

**NATS:** Rejected for the same reason — explicitly ruled out. NATS is the right answer at
100k+ users across multiple datacenters. Here it adds an extra dependency and ops burden.

**Sticky sessions / consistent hashing:** Rejected because they make rolling deployments
painful (draining sessions on every deploy). With Redis pub/sub, any process can serve any
user; a restart causes a reconnect that the client handles with exponential backoff.

**`?token=` query parameter auth:** Rejected because query strings appear in reverse-proxy
access logs. First-message auth keeps the token inside the encrypted payload.

**JSON heartbeat ping/pong:** Rejected in favor of protocol-level frames. No application-level
parsing needed; load balancers recognize the frames natively; Flutter's `web_socket_channel`
handles pong automatically.

**Separate `POST /auth/ws-ticket`:** Rejected because it adds a parallel token issuance path
without a security benefit. The access token is already short-lived; the `token.expiring`
flow keeps connections alive without a storm of reconnects.

## Consequences

### Positive

- No sticky sessions → rolling deploys are zero-downtime for clients (reconnect + replay).
- Plain Redis pub/sub is already in the stack; no new infrastructure dependency.
- Per-connection async queue isolates slow clients completely — one stalled device cannot
  delay delivery to others.
- Protocol-level heartbeat works with all compliant WS clients and infrastructure (nginx,
  ALB) without any Flutter-side code.
- Token-refresh-in-place (`auth.refresh` frame) eliminates reconnect storms at 15-minute
  token expiry boundaries; keeps the SLO's p95 delivery latency clean.
- Resume-from-cursor bounded at 500 messages keeps replay fast and predictable; HTTP handles
  deep history without complicating the WS path.

### Negative / Tradeoffs

- **Plain pub/sub has no replay at the broker level.** If the Redis process restarts between a
  message being published and a subscriber receiving it, that delivery is lost. Mitigation:
  PostgreSQL is the authoritative store; replay on reconnect uses the DB, not Redis. The WS
  layer is best-effort real-time delivery, not a durable queue.
- **In-memory connection registry is per-process.** A process crash drops all its connections.
  Clients reconnect and replay from cursor. This is acceptable at 1–2 processes; if the
  deployment grows to 10+ processes, a distributed registry (e.g., Redis HSET) becomes
  necessary.
- **Token expiry handling adds a `token.expiring` server→client event type.** Flutter client
  must implement the `auth.refresh` frame. This is new compared to the baseline WEBSOCKET.md
  spec and must be documented in `docs/ui-contract/`.
- **Connection limit enforcement via Redis INCR/DECR can drift** if a process crashes without
  decrementing. Mitigation: a background sweeper reconciles the Redis counter against actual
  live connections every 60 s, and the TTL-based floor prevents permanent stuck counters.

### Future Work

- When `messages` table exceeds 10M rows, revisit `message-partitioning.md` for how replay
  queries behave against partitioned tables (index must span the partition used by the cursor).
- If deployment grows beyond 5 processes, move the connection registry from in-memory dicts to
  a Redis HSET so any process can route a targeted event (e.g., admin broadcast) to a specific
  user's socket without publishing to every process.
- Consider adding a `ws:last_seen:{user_id}` Redis key (SETEX) updated on every received
  frame to power presence without a separate heartbeat counter — deferred because the current
  heartbeat already covers liveness.
- Load-test target (Stage 6): 2 500 concurrent WS clients, 1 message/s to a 500-member group,
  verify p95 fanout latency < 200 ms and `dutta_message_delivery_latency_seconds` p95 < 2 s
  (the SLO in `docs/design/slo.md`).

## Cross-references

- Consumed by: `src/modules/chat/websocket/` — primary implementation target
- Related RFC: `docs/design/idempotency.md` — `client_message_id` deduplication applies to
  WS message.send as well as REST POST
- Related RFC: `docs/design/tenant-isolation.md` — WS connections are institution-scoped;
  cross-tenant fan-out must be impossible by construction (channel names include
  `conversation_id`, which is institution-scoped)
- Related RFC: `docs/design/message-partitioning.md` — replay query must remain valid after
  the messages table is partitioned
- Related RFC: `docs/design/slo.md` — `message delivery p95 < 2 s` SLO is the acceptance
  criterion for the fanout design
- Related RFC: `docs/design/api-versioning.md` — WS error frame envelope follows the same
  `{code, message, details}` structure as HTTP error envelope
- Reference doc: `reference-docs/modules/chat/WEBSOCKET.md` — this RFC extends the handshake
  section (adds first-message auth, `token.expiring` event, `auth.refresh` frame); the
  canonical WEBSOCKET.md must be updated in Stage 4 before the module ships
- Reference doc: `reference-docs/modules/chat/SCHEMA.sql` — replay query uses
  `idx_messages_conversation_created`; no new index required
