---
title: "API Versioning Policy and Canonical Error Envelope"
status: accepted
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - all Stage-4 modules (users, acl, groups, chat, media, notifications)
  - Flutter UI team
  - third-party admin tools (future)
---

# API Versioning Policy and Canonical Error Envelope

## Context

DuttaMessenger is a self-hosted institutional platform targeting 1–5k users per
deployment. The Flutter client is the primary consumer today; third-party admin
tools are expected later. Two problems need solving before Stage-4 modules ship:

1. **No versioning policy.** There is no documented rule for when `/api/v1` must
   become `/api/v2`, how long v1 will be supported after v2 ships, or what changes
   are considered breaking. Without this decision, module authors have no guidance
   and the Flutter team cannot predict contract stability.

2. **Inconsistent error envelope (Gap B).** The smoke run documented in
   `docs/MANUAL_SMOKE.md` found that `AppException`-derived errors produce the
   correct `{"error": {"code", "message", "details"}}` shape, but bare
   `HTTPException(detail=...)` calls — seven of them in
   `src/modules/auth/routes/auth_routes.py` (verified by the Stage-3
   implementability check, not the original drafter's count of four), plus
   FastAPI's own 401 for missing
   auth — produce `{"detail": "..."}`. The Flutter team is already parsing both
   shapes. This RFC fixes the shape once.

## Decision

**Part 1 — Versioning:** URL versioning at `/api/v{N}`. Start at `/api/v1`,
increment to `/api/v2` only when a breaking change must ship. When v(N+1) ships,
v(N) lives for exactly 90 days behind `Sunset` + `Link` deprecation headers, then
is removed. Only the current and previous major (N, N-1) are ever supported
simultaneously; v(N+2) removes v(N) on its release day.

**Part 2 — Error envelope:** Every non-2xx response across every module and every
version uses exactly the shape below. This RFC is the single source of truth.
The fix mechanism is **middleware normalisation** (option A): a FastAPI exception
handler registered in `src/main.py` rewrites any `HTTPException` whose `detail`
is a plain string into the canonical envelope, so existing `AppException` paths
are unaffected and module authors never need to remember to use a specific class.

## Details

### Scope

- All REST endpoints under `/api/v{N}/`.
- All WebSocket error frames (the `websocket-scaling.md` RFC references this
  document for the frame shape).
- The OpenAPI `info.version` field and its bump rules.
- The deprecation runbook for v1 → v2 transitions.
- The canonical error envelope and the normalisation mechanism that enforces it.

### Non-goals

- GraphQL, gRPC, or `Accept-Version` header versioning — out of scope at this
  scale. URL versioning is sufficient and simpler.
- Multi-region or multi-tenant routing — see `tenant-isolation.md`.
- Idempotency collision responses — the envelope shape is defined here; the
  collision logic lives in `idempotency.md`.
- GDPR erasure endpoint error shapes — use this envelope; see `privacy-erasure.md`
  for the endpoint spec.
- Audit-log gap (Gap A) — separate fix, tracked in `docs/MANUAL_SMOKE.md`.
- Refresh token rotation (Gap C) — separate fix, tracked in `docs/MANUAL_SMOKE.md`.

---

## Part 1 — Versioning Policy

### URL structure

```
/api/v1/{resource}          ← current
/api/v2/{resource}          ← future, only when a breaking change ships
```

No `/api/latest/`, no `Accept-Version` header, no beta namespaces. A client
pins to a major version and knows exactly what contract it has.

### Breaking vs non-breaking changes

**Breaking** (requires a new major version and a new URL prefix):

| Change | Example |
|--------|---------|
| Remove an endpoint | DELETE `/api/v1/auth/invite` removed |
| Rename a response field | `access_token` → `token` |
| Change a field's type | `expires_in_seconds: int` → `expires_at: ISO8601 string` |
| Add a required request field | new mandatory `device_id` on `/auth/login` |
| Change the HTTP status code for a given outcome | login failure changes from 401 to 403 |
| Remove an enum value from a field clients must write | remove a `MessageType` value |
| Change pagination from cursor-based to offset | fundamental contract change |

**Non-breaking** (allowed on the same URL version, bumps OpenAPI minor):

| Change | Example |
|--------|---------|
| Add a new endpoint | new `GET /api/v1/users/search` |
| Add a new optional request field | optional `locale` on `/auth/login` |
| Add a new response field | add `avatar_url` to `UserResponse` |
| Add a new error code | new `INVITATION_QUOTA_EXCEEDED` code |
| Add a new enum value (with caveat — see below) | new `MessageType.POLL` |

**Enum caveat:** adding a new enum value is non-breaking *only if* clients treat
unknown enum values gracefully (e.g., render a fallback). The Flutter client MUST
implement this defensive parsing. Document this requirement in
`docs/ui-contract/README.md` and add a test fixture that sends an unknown enum
value to the client parser.

### OpenAPI `info.version` — semver on the spec

The published `docs/ui-contract/openapi.json` carries a semver version in its
`info.version` field:

```
MAJOR.MINOR.PATCH

MAJOR  — incremented when the URL prefix advances (v1 → v2). MAJOR == URL major.
MINOR  — incremented for every non-breaking, additive change.
PATCH  — incremented for doc-only corrections (no schema change).
```

The CI contract-snapshot test (`make contract-snapshot`) regenerates
`openapi.json` from the live app and diffs it against the committed copy. If the
diff is non-empty and CI is not running in the "intentional-change" mode, the
build fails. This catches accidental field renames.

### Deprecation and sunset runbook

When a breaking change requires `/api/v2`:

**Step 1 — Announce (before code ships)**
- Open a GitHub issue labelled `deprecation` stating: what changes, when v2
  ships, when v1 will be removed (v1 removal date = v2 ship date + 90 days).
- Notify Flutter team and any third-party consumers via the issue.
- Record the dates in `docs/NEXT_SESSION.md` under a "Deprecation tracker" heading.

**Step 2 — Ship v2**
- Register the new router prefix in `src/main.py`:
  ```python
  app.include_router(v2_router, prefix="/api/v2")
  # v1 router remains registered
  ```
- The two routers may share service-layer code; the version boundary lives in
  the route handlers and response models only.

**Step 3 — Emit sunset headers on v1 (same PR as Step 2)**
- Add a middleware or per-router dependency that injects two headers on every
  v1 response:
  ```
  Sunset: <RFC3339 datetime of removal, e.g. 2026-07-18T00:00:00Z>
  Link: <https://your-host/docs/deprecation>; rel="deprecation"
  ```
- Implementation: a FastAPI `Middleware` that checks `request.url.path.startswith("/api/v1")`
  and appends headers. Zero business-logic change to v1 routes.

**Step 4 — Remove v1 on the sunset date**
- Unregister the v1 router from `src/main.py`.
- Delete `src/modules/*/routes/v1/` files.
- Remove the sunset middleware (now dead code).
- Run the full test suite + smoke test.
- Tag the release.
- Close the deprecation issue.

### N-1 support rule

At any moment, at most two major versions are live: the current (vN) and the
previous (v(N-1)). When vN+1 ships, v(N-1) is removed **on the same day**. There
is no grace period for skipping two majors.

Example:
```
Today:    v1 supported
v2 ships: v1 gets 90-day sunset, v2 is current
v3 ships: v2 is current, v1 is removed same day (even if its 90-day window
          hasn't elapsed), v2 gets a fresh 90-day sunset window
```

This keeps the codebase clean and prevents accumulation of legacy shims.

---

## Part 2 — Canonical Error Envelope

### Shape

Every non-2xx HTTP response body from every endpoint, in every API version:

```json
{
  "error": {
    "code": "SNAKE_CASE_ERROR_CODE",
    "message": "Human-readable message, English, no PII.",
    "details": { "any": "contextual data" }
  }
}
```

- `code` — `UPPER_SNAKE_CASE`. Taxonomy: `<DOMAIN>_<REASON>` (see catalog below).
  Clients should switch on `code`, never on `message`.
- `message` — Stable enough for a developer to read in logs. MUST NOT contain
  PII (no emails, names, UUIDs of other users, or raw SQL).
- `details` — Optional object. May be empty `{}` but is always present (never
  `null`). Contains machine-readable context the client can use without parsing
  `message` (e.g., which field failed validation, which resource was not found).

### WebSocket error frames

WebSocket error events use the same envelope embedded in the event payload:

```json
{
  "event": "error",
  "payload": {
    "error": {
      "code": "WS_MESSAGE_TOO_LARGE",
      "message": "Message content exceeds 4096 characters.",
      "details": { "max_length": 4096, "received_length": 5120 }
    }
  }
}
```

See `docs/design/websocket-scaling.md` for the full WebSocket event contract.

### Fixing Gap B — chosen mechanism: middleware normalisation

**Decision: use a FastAPI exception handler (option A).**

Rationale: mandating `AppException` everywhere (option B) relies on every
current and future developer remembering the rule. It has already failed — seven
call sites in `auth_routes.py` use bare `HTTPException`, and FastAPI itself
emits `{"detail": "Not authenticated"}` for missing auth tokens. A middleware
fix is applied once, in one place (`src/main.py`), and normalises every
deviation automatically regardless of who wrote the route.

**Implementation:**

Add an exception handler for `HTTPException` in `src/main.py`:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Normalise all HTTPException responses to the canonical error envelope.

    If exc.detail is already the canonical dict shape (has top-level "error" key),
    pass it through unchanged. Otherwise, wrap plain string detail into the envelope.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        # Already canonical (e.g. from AppException.to_http_exception())
        body = detail
    else:
        # FastAPI default or bare HTTPException(detail="...") — normalise
        body = {
            "error": {
                "code": _status_to_code(exc.status_code),
                "message": str(detail) if detail else _status_to_message(exc.status_code),
                "details": {},
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body)


def _status_to_code(status_code: int) -> str:
    """Map HTTP status code to a default error code when no specific code is given."""
    _MAP = {
        400: "BAD_REQUEST",
        401: "AUTH_UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    return _MAP.get(status_code, f"HTTP_{status_code}")


app.add_exception_handler(HTTPException, http_exception_handler)
```

Also add a handler for Pydantic `RequestValidationError` (FastAPI's 422 default
also uses `{"detail": [...]}` shape):

```python
from fastapi.exceptions import RequestValidationError

async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": {"errors": exc.errors()},
            }
        },
    )

app.add_exception_handler(RequestValidationError, validation_exception_handler)
```

**Specific call sites in `auth_routes.py` that produce non-canonical responses:**

| Location | Line | Current (broken) | Fix |
|----------|------|-------------------|-----|
| `register()` | ~122 | `HTTPException(400, detail="Direct registration not allowed.")` | Middleware normalises automatically. Optionally replace with `raise ValidationError("Direct registration not allowed.")` for better code clarity. |
| `create_institution()` | ~80 | `HTTPException(500, detail="Failed to create institution")` | Middleware normalises. |
| `invite_user()` | ~277 | `HTTPException(500, detail="Failed to send invitation")` | Middleware normalises. |
| `change_password()` | ~321 | `HTTPException(500, detail="Failed to change password")` | Middleware normalises. |
| `get_current_user` middleware | (auth.py) | FastAPI 401 `{"detail": "Not authenticated"}` | Middleware normalises. |

**Does this RFC cover the cleanup?** The middleware fix in `src/main.py` is in
scope for Stage 3 and should be implemented before Stage-4 modules begin. The
optional `AppException` refactor of the seven auth route call sites is a
**follow-up** (low priority — the middleware makes the responses correct either
way). Track it as a tech-debt note in `docs/NEXT_SESSION.md`.

### Error code taxonomy

Pattern: `<DOMAIN>_<REASON>` where `DOMAIN` is an uppercase module or
cross-cutting area and `REASON` is a concise, specific verb-phrase.

**Currently defined codes** (from `src/shared/exceptions.py`):

| Code | HTTP | AppException class | Meaning |
|------|------|--------------------|---------|
| `NOT_FOUND` | 404 | `NotFoundError` | Resource does not exist |
| `PERMISSION_DENIED` | 403 | `PermissionDeniedError` | Authenticated but not authorised |
| `AUTHENTICATION_FAILED` | 401 | `AuthenticationError` | Token invalid, expired, or absent |
| `VALIDATION_ERROR` | 422 | `ValidationError` | Request field failed validation |
| `CONFLICT` | 409 | `ConflictError` | Duplicate or conflicting resource |
| `RATE_LIMIT_EXCEEDED` | 429 | `RateLimitError` | Too many requests |
| `INTERNAL_SERVER_ERROR` | 500 | `InternalServerError` | Unhandled server fault |

**Reserved codes for cross-cutting concerns** (used by multiple RFCs):

| Code | HTTP | Defined by RFC | Meaning |
|------|------|---------------|---------|
| `AUTH_UNAUTHENTICATED` | 401 | this RFC | No/malformed auth token (middleware default) |
| `TENANT_FORBIDDEN` | 403 | `tenant-isolation.md` | Cross-institution access attempt |
| `IDEMPOTENCY_COLLISION` | 409 | `idempotency.md` | Duplicate `Idempotency-Key` with different payload |
| (none — use `RATE_LIMIT_EXCEEDED` from the existing `AppException` catalog above) | 429 | — | Per-endpoint/per-IP rate limit. Do NOT introduce a second alias such as `RATE_LIMITED`; keep one code across the codebase. |

**Module-level codes (to be added in Stage 4)** — illustrative, not exhaustive:

| Code | Module | Meaning |
|------|--------|---------|
| `AUTH_INVITE_EXPIRED` | auth | Invitation token past its TTL |
| `AUTH_INVITE_ALREADY_USED` | auth | Invitation token already accepted |
| `AUTH_DIRECT_REGISTRATION_DISABLED` | auth | Institution requires invitation |
| `USER_NOT_FOUND` | users | Specific user-domain variant of `NOT_FOUND` |
| `GROUP_MEMBER_ALREADY_EXISTS` | groups | User already in group |
| `CHAT_MESSAGE_TOO_LARGE` | chat | Content > 4096 chars |
| `MEDIA_UNSUPPORTED_MIME_TYPE` | media | File type not permitted |
| `NOTIFICATION_TOKEN_INVALID` | notifications | FCM device token rejected |

### Module-level documentation contract

Every module's `docs/API.md` MUST include an **Error Codes** section that lists:
- The `code` string
- The HTTP status
- The trigger condition
- An example `details` object (copy from a real test fixture)

This is mandated by CLAUDE.md Post-Flight section F. Any module PR that adds a
new error code without updating `docs/API.md` fails code review.

Example format:

```markdown
## Error Codes

| Code | HTTP | When | Example `details` |
|------|------|------|-------------------|
| `AUTH_INVITE_EXPIRED` | 401 | Invitation token TTL has passed | `{"expired_at": "2026-03-01T12:00:00Z"}` |
| `VALIDATION_ERROR` | 422 | Missing required field | `{"field": "email", "errors": [...]}` |
```

---

## Implementation Sketch

### Files touched

| File | Change |
|------|--------|
| `src/main.py` | Register `http_exception_handler` and `validation_exception_handler` |
| `src/shared/exceptions.py` | Add `TenantForbiddenError` and `IdempotencyCollisionError` stubs (filled in by their respective RFCs) |
| `src/shared/middleware/auth.py` | Ensure `get_current_user` raises `AuthenticationError` (already an `AppException`) rather than bare `HTTPException` where possible |
| `docs/ui-contract/openapi.json` | Regenerate after middleware wires up; error response schemas will now be consistent |

### No new DB columns or tables required for this RFC.

### Required Stage-0/1 primitives

- `src/shared/exceptions.py` — already exists; stubs needed for new codes.
- `src/shared/responses.py` — `error_response()` already exists; handlers above
  use `JSONResponse` directly to avoid circular import risk.

---

## Alternatives Considered

**Option B (AppException-only, no middleware):** Require every module to raise
`AppException` subclasses. Rejected because it requires every developer to
remember, and FastAPI's own 401 for missing tokens cannot be controlled without
a handler anyway. The middleware fix is lower-risk and more complete.

**Accept-Version header versioning:** Some APIs use `Accept: application/vnd.app+json;version=2`.
Rejected for this scale — adds client complexity and forces header inspection on
every request. URL versioning is explicit, debuggable, and cacheable.

**Semver on the URL itself (`/api/v1.2/`):** Rejected — semver in the URL is
unusual and confusing. Version the URL by major only; use `info.version` in the
OpenAPI spec for the full semver.

**Keep `{"detail": "..."}` for FastAPI internals:** Rejected — the Flutter client
cannot have two code paths for error parsing. One shape, always.

---

## Consequences

### Positive

- Flutter client has exactly one error-parsing code path.
- Module authors get a clear rule: breaking change = new URL prefix. No ambiguity.
- Middleware fix is a one-time change that covers all current and future bare
  `HTTPException` calls, including third-party FastAPI middleware.
- Error code catalog in each `docs/API.md` means the UI team can build error
  handling without asking backend.
- 90-day sunset window is long enough for a 1–5k user school to update their
  Flutter app before v1 disappears.

### Negative / Tradeoffs

- Middleware adds one function call per error response. At this scale (5k users),
  this overhead is unmeasurable.
- The `_status_to_code` fallback produces generic codes like `HTTP_418` for
  unregistered status codes. Acceptable — these should never appear in production.
- Two simultaneous URL versions (v1 + v2 during the 90-day window) means
  maintaining two sets of route handlers. Mitigated by sharing the service layer.

### Future Work

- **Tech debt:** Optionally refactor the seven `HTTPException(detail=...)` call
  sites in `auth_routes.py` to use `AppException` subclasses. Track in
  `docs/NEXT_SESSION.md`.
- **Revisit if:** a third-party integration requires header-based versioning.
  Current constraint (Flutter + admin tools) does not need it.
- **Revisit N-1 rule if:** a school's IT department cannot update the Flutter app
  within 90 days — extend the sunset window in that case, but document it as a
  one-time exception.

---

## Cross-References

- Related RFC: [idempotency.md](idempotency.md) — uses `IDEMPOTENCY_COLLISION`
  error code defined here
- Related RFC: [tenant-isolation.md](tenant-isolation.md) — uses
  `TENANT_FORBIDDEN` error code defined here
- Related RFC: [websocket-scaling.md](websocket-scaling.md) — WS error frames
  use the envelope shape defined here
- Related RFC: [privacy-erasure.md](privacy-erasure.md) — erasure endpoint error
  responses use this envelope
- Consumed by: all `src/modules/*/routes/` — every route handler
- Reference doc: `docs/MANUAL_SMOKE.md` — Gap B that this RFC resolves
- Reference doc: `CLAUDE.md` — Post-Flight section F (module API.md contract)
- Reference doc: `reference-docs/API_STANDARDS.md` — REST API rules this RFC
  extends (does not contradict)
