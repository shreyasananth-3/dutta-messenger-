# DuttaMessenger — Flutter Integration Guide

**Audience:** Flutter / UI developers integrating with the DuttaMessenger backend.

**Status of this document:** Current as of commit `d92ce1c` (Stages 0–4 complete: auth, users, acl, groups, chat, media, notifications). Reflects **what is actually shipped and callable today**, not what is planned. Updated as each backend module lands.

---

## 1. What you can build against today

| Area | Status | Notes |
|------|--------|-------|
| ✅ **Auth** (register, login, refresh, invite, change password, create institution) | **Ready** | Endpoints listed in [auth.md](auth.md) |
| ✅ **Standard error envelope** | **Ready** | Every API returns the same shape (see §4) |
| ✅ **Correlation IDs** | **Ready** | `X-Request-ID` header echoed on every response |
| ✅ **Health probe** | **Ready** | `GET /health` — useful for smoke tests |
| ✅ Users (profiles, search, presence, settings) | **Ready** | `ENABLE_USERS=true`; 7 endpoints |
| ✅ ACL (roles, permissions, 3-level access) | **Ready** | `ENABLE_ACL=true`; 4 endpoints |
| ✅ Groups (incl. topics) | **Ready** | Endpoints listed in [groups.md](groups.md) — 11 endpoints |
| ✅ Chat (REST + WebSocket) | **Ready** | WebSocket integration in [websocket-integration.md](websocket-integration.md); 6 REST + `/api/v1/ws/chat` |
| ✅ Media upload | **Ready** | Endpoints listed in [media.md](media.md) — 5 endpoints (presigned-URL flow, not multipart) |
| ✅ Push notifications | **Ready** | `ENABLE_NOTIFICATIONS=true`; 4 endpoints + FCM fanout |

**Feature flags are environment variables** read at server startup. A module whose flag is `false` returns `404` for all its routes — you can safely write Flutter code against the planned contracts in `reference-docs/modules/*/MODULE.md` and flip flags on as each module ships.

---

**👉 On AWS right now:** `https://dattamessenger.duckdns.org` is the stable backend. If your Flutter app is still hitting `ngrok-free.dev` anywhere, read [environments.md](environments.md) — that's the one-setting fix.

---

## 2. Running the backend locally (for UI testing)

```bash
# 1. Clone the repo
git clone https://github.com/shreyasananth-3/dutta-messenger-.git
cd dutta-messenger-

# 2. Start Postgres, Redis, MinIO
docker compose up -d

# 3. Install Python deps (requires Python 3.12+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"

# 4. Apply database migrations
make migrate

# 5. Run the API
make run
```

The API is now at **`http://localhost:8000`** with:
- Interactive docs: `http://localhost:8000/docs` (Swagger UI)
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/health`
- Prometheus metrics: `http://localhost:8000/metrics`

To expose optional modules while testing, set the corresponding flag before `make run`:

```bash
ENABLE_USERS=true ENABLE_CHAT=true make run
```

---

## 3. Base URL + versioning

All business endpoints live under `/api/v1/`. Example:
```
http://localhost:8000/api/v1/auth/login
```

The version is part of the URL path — when we ship `/api/v2`, `/api/v1` will continue to work for at least **one release cycle (N-1)** with a `Sunset` response header giving the deprecation date. Flutter clients should log any `Sunset` header they see and prompt for an app update.

---

## 4. Standard response envelope

Every endpoint returns one of three shapes. Parse against these in your Dart models; you will never see anything else.

### 4.1 Single resource (success)
```json
{
  "data": {
    "id": "4bfb3d2c-5f28-4a2a-9e5d-3dc7e9b1f1c4",
    "email": "alice@school.edu",
    ...
  }
}
```

### 4.2 List with pagination (success)
```json
{
  "data": [ { ... }, { ... } ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wNC0xOFQxMjowMDowMFoifQ==",
    "limit": 50
  }
}
```

**Pagination is cursor-based, not page-based.** To fetch the next page, send `?cursor=<next_cursor>&limit=50`. Never do `?page=2` — that endpoint does not exist. When `has_more` is `false`, stop.

### 4.3 Error (any non-2xx status)
```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "Email or password is incorrect.",
    "details": { "email": "alice@school.edu" }
  }
}
```

**`error.code` is the machine-readable discriminator.** Switch on it in Dart, never parse `message`. The full catalog of codes per module is documented in each module's `docs/API.md`. Common codes include:

| Code | HTTP | Meaning |
|------|------|---------|
| `UNAUTHORIZED` | 401 | Token missing, expired, or invalid. Trigger refresh flow. |
| `FORBIDDEN` | 403 | User is authenticated but not allowed. Show a friendly deny screen. |
| `NOT_FOUND` | 404 | Resource does not exist **or** belongs to another institution (we return 404 in both cases to avoid leaking tenant existence). |
| `VALIDATION_ERROR` | 422 | Malformed payload. `details` contains the field-level errors. |
| `RATE_LIMITED` | 429 | Respect `Retry-After` header and back off. |
| `INTERNAL_SERVER_ERROR` | 500 | Server bug. Surface a generic "something went wrong" and log the `X-Request-ID`. |

---

## 5. Authentication flow

1. **Login** with email + password → you get `access_token` (30-minute lifetime) and `refresh_token` (7-day lifetime).
2. **Every protected request** sends `Authorization: Bearer <access_token>`.
3. **If a request returns 401** with `code: UNAUTHORIZED`, call `/api/v1/auth/refresh` with the `refresh_token` to get a new pair, then retry the original request **once**. If the retry also fails, log the user out.
4. **Change password** requires `access_token` + current password.

```
┌────────┐        ┌─────────┐
│Flutter │        │ Backend │
└───┬────┘        └────┬────┘
    │ POST /auth/login  │
    ├──────────────────>│
    │  200 OK           │
    │  {access, refresh}│
    │<──────────────────┤
    │                   │
    │ GET /secured      │ <— Authorization: Bearer <access>
    ├──────────────────>│
    │ 401 UNAUTHORIZED  │
    │<──────────────────┤
    │                   │
    │ POST /auth/refresh│ <— body: {refresh_token}
    ├──────────────────>│
    │ 200 {new pair}    │
    │<──────────────────┤
    │                   │
    │ GET /secured (x2) │
    ├──────────────────>│
    │ 200 OK            │
    │<──────────────────┤
```

**Never store tokens in `SharedPreferences`.** Use `flutter_secure_storage` (iOS Keychain / Android Keystore).

---

## 6. Correlation IDs — use them in bug reports

Every response includes an `X-Request-ID` header. When a user reports a bug, include that ID in the ticket — backend engineers can grep every log line for that ID and trace the request end-to-end.

Flutter: inspect `response.headers['x-request-id']` on failure paths and attach it to your error reports (Crashlytics, Sentry, etc.).

---

## 7. Rate limiting

Default limit: **300 requests per minute per user** (falls back to per-IP when unauthenticated). When you exceed it:

- HTTP 429
- Error code `RATE_LIMITED`
- `Retry-After` header in seconds

Build a retry helper with exponential backoff and respect `Retry-After`. Never retry more than 3 times before surfacing an error to the user.

---

## 8. Code generation (Dart from OpenAPI)

Once the backend is running locally, export the OpenAPI spec:

```bash
make openapi-export    # writes docs/ui-contract/openapi.json
```

Then in your Flutter project:

```bash
dart pub global activate openapi_generator_cli
openapi_generator_cli generate \
  -i path/to/dutta-messenger/docs/ui-contract/openapi.json \
  -g dart-dio \
  -o lib/src/api
```

This gives you typed Dart models and a pre-built client for every endpoint. Regenerate whenever the backend ships a new module.

---

## 8a. Using Claude Code inside the Flutter repo

If your Flutter team uses Claude Code, copy [`CLAUDE_FLUTTER.md`](CLAUDE_FLUTTER.md) into the **Flutter repo's** `CLAUDE.md` (merge with any existing file). It teaches Claude:
- The backend's URL, auth flow, envelope, pagination, rate limits.
- How to read the per-module contracts before writing Dart code.
- Project conventions (networking, state, storage, WebSocket reconnect).
- Starter prompts your team can use ("build a login screen against the auth contract…").

---

## 9. Per-module contracts

- [**auth.md**](auth.md) — 6 auth endpoints
- [**openapi.json**](openapi.json) — 33-path OpenAPI 3.1 snapshot for codegen (all modules)
- Module source-of-truth: every module ships its own contract under `src/modules/{name}/docs/`
  - [users MODULE.md](../../src/modules/users/docs/MODULE.md) + [API.md](../../src/modules/users/docs/API.md)
  - [acl MODULE.md](../../src/modules/acl/docs/MODULE.md)
  - [groups MODULE.md](../../src/modules/groups/docs/MODULE.md)
  - [chat MODULE.md](../../src/modules/chat/docs/MODULE.md) + [API.md](../../src/modules/chat/docs/API.md) + [WEBSOCKET.md](../../src/modules/chat/docs/WEBSOCKET.md)
  - [media MODULE.md](../../src/modules/media/docs/MODULE.md) + [API.md](../../src/modules/media/docs/API.md)
  - [notifications MODULE.md](../../src/modules/notifications/docs/MODULE.md) + [API.md](../../src/modules/notifications/docs/API.md)

## 9.1 WebSocket entry point

The chat WebSocket lives at **`ws://localhost:8000/api/v1/ws/chat`**.
Protocol per [WEBSOCKET.md](../../src/modules/chat/docs/WEBSOCKET.md) —
the server today implements the minimum viable path (auth → subscribe →
message.send → message.new broadcast + ping/pong). Advanced features
(backpressure queue, resume-from-cursor replay, token-expiry hints)
ship in Stage 6 hardening.

Each module page will include real request/response JSON copied from the test suite, so examples are guaranteed to match production behaviour.

---

## 10. Until then — what to build against

You can already start on:
1. **Auth flow screens** (login, token refresh, change password, accept invitation).
2. **Networking layer**: Dio client with `Authorization` header injection, 401-retry interceptor, error-envelope parser, `X-Request-ID` logging, exponential backoff on 429.
3. **Storage**: secure-storage wrapper for tokens, local DB (Isar / Drift) for messages (schema per `reference-docs/flutter-architecture.md`).
4. **WebSocket reconnect helper**: you can read `reference-docs/modules/chat/WEBSOCKET.md` now to design the resume-from-cursor logic; the server side ships in Stage 4d.

---

## 11. Questions / blocked on backend?

Open a GitHub issue with:
- The endpoint you're calling
- The request body (redact secrets)
- The full response (including `X-Request-ID`)
- What you expected vs what you got

Backend team will respond within one working day.
