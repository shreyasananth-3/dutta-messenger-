---
title: "Idempotency — Idempotency-Key header handling for mutating REST endpoints"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - src/modules/auth/
  - src/modules/chat/
  - src/modules/groups/
  - src/modules/media/
---

# Idempotency — Idempotency-Key Header Handling

## Context

DuttaMessenger's clients (Flutter mobile, desktop) run over lossy mobile
networks. A user tapping "Send" may receive a TCP timeout before confirming
whether the server wrote the message; a naïve retry creates duplicates. The
same applies to group creation, media upload initiation, and invitation sending.

Before Stage 4 authors write these endpoints they need a single, consistent
answer to four questions: how do I store and look up a key, what do I return
on replay, what happens on collision, and when does audit logging fire?

Without this RFC, every module author will answer differently and the Flutter
client will face six incompatible retry contracts.

## Decision

Use a Redis-backed `Idempotency-Key` header (scoped per user per endpoint)
with a 24-hour TTL. On replay, return the stored response body byte-for-byte.
On collision (same key, different payload), return 409 with the canonical
error envelope. If Redis is unavailable, fail open (allow the request without
deduplication) and emit a structured warning. Audit events fire only on the
first write; replayed responses bypass audit entirely.

## Details

### Scope

This RFC governs idempotency handling for the following endpoints:

| Module | Endpoint | Required? |
|--------|----------|-----------|
| auth | `POST /api/v1/auth/invite` | Required |
| groups | `POST /api/v1/groups` | Required |
| chat | `POST /api/v1/chat/conversations/{id}/messages` | Required |
| chat | `POST /api/v1/chat/conversations` (DM create) | Required |
| media | `POST /api/v1/media/upload` (initiate) | Required |

Endpoints explicitly excluded (they are NOT idempotent by design):

| Endpoint | Reason |
|----------|--------|
| `POST /api/v1/auth/login` | Issues a new token on every call by design; repeating login is intentional. |
| `POST /api/v1/auth/refresh` | Token rotation deliberately invalidates the previous token; replay would yield a revoked token. |
| `GET *` | Safe, no state mutation. |
| `PATCH *`, `DELETE *` | Already effectively idempotent on the resource level; no additional key needed. |

### Non-goals

- **Audit taxonomy** — which `AuditEvent` values exist, the `audit_logs`
  schema, and cross-tenant fuzz testing strategy are deferred to
  `docs/design/tenant-isolation.md`.
- **Error envelope shape** — the exact JSON structure of error responses is
  deferred to `docs/design/api-versioning.md`. This RFC says "return 409 with
  the canonical error envelope" without defining that envelope.
- **WebSocket message deduplication** — WebSocket sends include a
  `client_message_id` UUID handled inside the chat module directly
  (see `reference-docs/modules/chat/MODULE.md` §Business Rules #7). This RFC
  only covers REST.
- **Client key generation** — clients are responsible for generating a valid
  UUID4. The server rejects non-UUID strings with 400.

### Implementation sketch

#### Key format (Redis)

```
idempotency:{institution_id}:{user_id}:{endpoint_fingerprint}:{client_key}
```

- `institution_id`: UUID from the verified JWT. Ensures keys cannot collide
  across institutions even if a client reuses the same UUID.
- `user_id`: UUID from the verified JWT. Scope is per-user, not per-session.
- `endpoint_fingerprint`: a short stable string identifying the endpoint —
  e.g. `chat.messages`, `groups.create`, `media.upload.init`, `auth.invite`.
  This prevents a client accidentally replaying a message-send key against a
  group-create endpoint.
- `client_key`: the raw value of the `Idempotency-Key` header (must be UUID4,
  validated on arrival).

Example:
```
idempotency:f47ac10b-58cc-4372-a567-0e02b2c3d479:3f6d8a12-...:chat.messages:9b1deb4d-...
```

TTL: **24 hours** from first write. Rationale: mobile clients retry within
seconds to minutes. 24 hours covers the longest plausible offline period
(overnight, poor connectivity) without filling Redis with stale entries. At
5k users × 100 msgs/day × ~1 KB per cached response the worst-case memory
footprint is ~500 MB — well within a 1 GB Redis instance.

#### Stored value schema (JSON, serialised as a Redis string)

```json
{
  "status": 201,
  "headers": {"Content-Type": "application/json"},
  "body": "<base64-encoded response body bytes>",
  "payload_hash": "<sha256 hex of the original request body>",
  "created_at": "<ISO-8601>"
}
```

`payload_hash` is used for collision detection (same key + different body →
409). The body is stored base64-encoded so arbitrary bytes are safe in Redis.

#### Middleware / shared helper

Add `src/shared/middleware/idempotency.py` exposing:

```python
async def check_idempotency(
    redis: Redis,
    key: str,
    payload_hash: str,
) -> IdempotencyResult:
    """Returns HIT (with stored response), MISS, or COLLISION."""

async def store_idempotency(
    redis: Redis,
    key: str,
    payload_hash: str,
    status: int,
    response_body: bytes,
    ttl_seconds: int = 86400,
) -> None:
    """Persist a completed response so replay returns it byte-for-byte."""
```

`IdempotencyResult` is a dataclass:

```python
@dataclass
class IdempotencyResult:
    outcome: Literal["hit", "miss", "collision", "redis_down"]
    stored: StoredIdempotencyEntry | None = None
```

Route handlers (or a FastAPI dependency) call `check_idempotency` before
delegating to the service. On HIT they return immediately from the stored
response. On MISS they proceed normally, then call `store_idempotency` with
the final response bytes before returning. On COLLISION they raise 409. On
`redis_down` they log and proceed as MISS (fail-open).

#### Audit log interaction — the Gap A fix

`write_audit(...)` (in `src/shared/security/audit.py`) is called inside the
service layer, not in middleware. This is intentional: the service is the only
place that knows whether the business operation actually succeeded.

On **replay** (HIT), the route handler returns the cached response before the
service is called. Therefore `write_audit` is never reached on replay. This is
the correct behaviour — the original write already created the audit row.

Rule: **audit writes happen exactly once per real write, zero times per
replay.** No special flag or guard is needed because the replay short-circuits
above the service layer.

#### Pseudocode — a route handler using the dependency

```python
# src/modules/chat/routes/messages.py
@router.post("/conversations/{conversation_id}/messages", status_code=201)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    idempotency: IdempotencyCheck = Depends(require_idempotency("chat.messages")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if idempotency.is_hit:
        return idempotency.replay()          # returns stored bytes, no service call
    result = await message_service.send(db, conversation_id, current_user.id, body)
    await idempotency.store(result)          # persists bytes for future replay
    return result
```

The `require_idempotency(endpoint_fingerprint)` FastAPI dependency:
1. Reads the `Idempotency-Key` header. Returns 400 if absent or not a UUID4.
2. Builds the full Redis key using JWT claims.
3. Calls `check_idempotency`. On COLLISION raises 409.
4. Attaches the result to the dependency so the handler can branch.

#### Files touched / created

| Path | Action |
|------|--------|
| `src/shared/middleware/idempotency.py` | Create — core helper |
| `src/shared/middleware/__init__.py` | Update exports |
| `tests/shared/test_idempotency.py` | Create — unit tests |
| Per-module route files | Add `Depends(require_idempotency(...))` |

### Alternatives considered

1. **Database-backed idempotency table instead of Redis.**
   More durable across Redis restarts, but adds a DB write on every request
   and a DB read on the hot path. At 5k users this is gratuitous overhead.
   Redis at this scale is plenty reliable with AOF persistence.

2. **Response re-fetch instead of byte-for-byte replay.**
   On replay, re-fetch the created resource from the DB and return a fresh
   serialisation. Simpler storage (just save the created resource ID), but it
   changes the response body if the resource was mutated between the original
   write and the replay. Stripe stores and replays the original bytes; we
   follow the same model for predictability.

3. **Scope the key per-institution only (not per-user).**
   Cheaper key, but two users in the same institution could collide if they
   both independently generate the same UUID4 (astronomically unlikely but
   non-zero). Per-user scope is strictly safer.

4. **Fail-closed when Redis is unavailable.**
   Rejecting requests when the idempotency store is down converts a
   non-critical service dependency into a hard availability dependency. For a
   1–5k user deployment where Redis downtime means the entire pub/sub layer is
   also down, fail-open is the lesser evil. An operator alert on the
   `idempotency_store_miss_redis_down` metric is the mitigation.

## Consequences

### Positive

- Flutter client can safely retry any mutating request on network failure
  without asking "did that go through?"
- Duplicate messages, groups, invitations, and uploads become impossible
  under normal retry patterns.
- Audit rows are guaranteed to be written exactly once per real mutation —
  Gap A (audit_logs empty) is fixed structurally for all future modules, not
  by patching each auth endpoint individually.
- Shared middleware means every Stage-4 module author adds two lines to a
  route handler rather than reinventing the pattern.

### Negative / tradeoffs

- Redis is now on the critical path for idempotency. Fail-open means a Redis
  outage opens a duplicate-write window. This is accepted given the scale.
- Storing response bodies in Redis costs memory (estimated ~500 MB worst-case,
  see TTL section). Operators must size Redis accordingly.
- Route handlers must explicitly opt in via the `require_idempotency`
  dependency — forgetting to add it on a new POST endpoint is a human error
  that the test checklist (POST endpoints must have an idempotency test case)
  should catch.
- 24-hour TTL means Redis holds completed response bodies for up to a day.
  For media upload initiation this may include presigned URL payloads that
  expire before the TTL; the client will need to initiate a fresh upload
  regardless. This is fine — the idempotent replay returns the original
  response; if the presigned URL has expired the client simply retries with a
  new key.

### Future work

- If Redis usage approaches capacity, reduce TTL to 4 hours (covers all
  realistic retry windows) or switch to a persistent idempotency table.
- If a module needs server-generated idempotency keys (e.g., background
  Celery tasks), extend `store_idempotency` to accept a server-generated
  UUID and return it in a response header.
- Revisit the fail-open decision if the deployment moves to a Redis cluster
  with guaranteed replica failover — at that point fail-closed becomes safer.

## Cross-references

- Related RFC: [tenant-isolation.md](tenant-isolation.md) — owns the
  `audit_logs` schema and `AuditEvent` taxonomy; this RFC calls
  `write_audit(...)` abstractly.
- Related RFC: [api-versioning.md](api-versioning.md) — owns the canonical
  error envelope used for 400, 409, and Redis-down warning responses.
- Consumed by:
  - `src/modules/auth/` (invite endpoint)
  - `src/modules/chat/` (message send, DM create)
  - `src/modules/groups/` (group create)
  - `src/modules/media/` (upload initiation)
- Reference doc: `reference-docs/modules/chat/MODULE.md` §Business Rules #7
  — the `client_message_id` field on WebSocket is the WS-layer analogue of
  this RFC; they are complementary, not duplicates.
- Gap addressed: `docs/MANUAL_SMOKE.md` Gap A — audit_logs empty after
  mutations. This RFC's replay short-circuit above the service layer
  structurally prevents double-write; the fix for zero rows is wiring
  `write_audit(...)` inside each service method (one or two lines per
  mutation), as noted in the smoke doc.
