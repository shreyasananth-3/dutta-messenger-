# Auth Module — API Contract

**Status:** Live as of commit `7197905`.
**Base path:** `/api/v1`
**Auth required:** Noted per endpoint.

All requests send/receive JSON (`Content-Type: application/json`). All responses follow the standard envelope (see [README.md §4](README.md#4-standard-response-envelope)).

---

## Endpoints at a glance

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/institutions` | None | Create a new institution (bootstrap-only — typically done via admin tooling, not the app) |
| POST | `/auth/register` | None | Create a user account via invitation token |
| POST | `/auth/login` | None | Exchange email + password for access + refresh tokens |
| POST | `/auth/refresh` | Bearer | Exchange a refresh token for a fresh access + refresh pair |
| POST | `/auth/invite` | Bearer | Invite a new user to your institution |
| POST | `/auth/change-password` | Bearer | Change the current user's password |

---

## 1. `POST /api/v1/auth/login`

**Auth:** None.
**Purpose:** Authenticate a user and receive tokens.

### Request
```json
{
  "email": "alice@school.edu",
  "password": "correct-horse-battery-staple",
  "institution_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `email` | string | yes | 1–255 chars |
| `password` | string | yes | 1–255 chars |
| `institution_id` | UUID string | optional | Only required if the server cannot infer the institution from the email domain |

### 200 OK
```json
{
  "data": {
    "user": {
      "id": "4bfb3d2c-5f28-4a2a-9e5d-3dc7e9b1f1c4",
      "institution_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
      "email": "alice@school.edu",
      "full_name": "Alice Example",
      "phone_number": null,
      "avatar_url": null,
      "bio": null,
      "status": "online",
      "is_active": true
    },
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expires_in_seconds": 1800
  }
}
```

### Error codes

| HTTP | `error.code` | When |
|------|--------------|------|
| 400 | `VALIDATION_ERROR` | Missing or malformed field |
| 401 | `INVALID_CREDENTIALS` | Email or password wrong |
| 403 | `ACCOUNT_DISABLED` | User's `is_active` is `false` |
| 429 | `RATE_LIMITED` | Too many login attempts; see `Retry-After` |

---

## 2. `POST /api/v1/auth/refresh`

**Auth:** Bearer (current access token; can be expired).
**Purpose:** Get a new pair of tokens without asking the user to log in again.

> **Flutter note:** Call this when any protected request returns `401 UNAUTHORIZED`. If refresh itself returns 401, log the user out and clear secure storage.

### Request
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 200 OK
```json
{
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "expires_in_seconds": 1800
  }
}
```

### Error codes
| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `UNAUTHORIZED` | Refresh token invalid, expired, or revoked |

---

## 3. `POST /api/v1/auth/register`

**Auth:** None.
**Purpose:** Create an account using an invitation token received via email.

### Request
```json
{
  "email": "bob@school.edu",
  "password": "a-long-strong-password",
  "full_name": "Bob Example",
  "phone_number": "+911234567890",
  "invitation_token": "inv_5f3a9c..."
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `email` | yes | Must match the email the invitation was sent to |
| `password` | yes | ≥ 8 chars |
| `full_name` | yes | 1–255 chars |
| `phone_number` | optional | up to 20 chars, E.164 format recommended |
| `invitation_token` | yes (for now) | From the invitation email link |

> Direct (non-invitation) registration is intentionally disabled — this is a closed institutional platform.

### 201 Created
```json
{
  "data": {
    "user": {
      "id": "…",
      "email": "bob@school.edu",
      "full_name": "Bob Example",
      "institution_id": "…",
      "is_active": true
    },
    "message": "Account created successfully from invitation"
  }
}
```

### Error codes
| HTTP | `error.code` | When |
|------|--------------|------|
| 400 | `INVITATION_TOKEN_REQUIRED` | No token supplied (direct registration disallowed) |
| 400 | `VALIDATION_ERROR` | Weak password, malformed email, missing field |
| 404 | `INVITATION_NOT_FOUND` | Token doesn't exist |
| 410 | `INVITATION_EXPIRED` | Token expired or already used |

---

## 4. `POST /api/v1/auth/invite`

**Auth:** Bearer (authenticated user with invite permission).
**Purpose:** Send an invitation email to a new user in your institution.

### Request
```json
{ "email": "charlie@school.edu" }
```

### 201 Created
```json
{
  "data": {
    "invitation": {
      "id": "…",
      "institution_id": "…",
      "email": "charlie@school.edu",
      "invited_by_user_id": "…",
      "expires_at": "2026-04-25T10:00:00Z",
      "status": "pending"
    },
    "message": "Invitation sent to charlie@school.edu"
  }
}
```

### Error codes
| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `UNAUTHORIZED` | Missing / invalid token |
| 403 | `FORBIDDEN` | User lacks invite permission |
| 409 | `USER_ALREADY_EXISTS` | A user with that email already exists in the institution |
| 409 | `INVITATION_ALREADY_PENDING` | There's already a pending invite for that email |

---

## 5. `POST /api/v1/auth/change-password`

**Auth:** Bearer.
**Purpose:** Change the logged-in user's own password.

### Request
```json
{
  "current_password": "old-password",
  "new_password": "a-stronger-new-password"
}
```

### 200 OK
```json
{
  "data": {
    "id": "…",
    "email": "alice@school.edu",
    "full_name": "Alice Example",
    "is_active": true
  }
}
```

### Error codes
| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `UNAUTHORIZED` | Access token invalid |
| 400 | `INVALID_CURRENT_PASSWORD` | `current_password` is wrong |
| 422 | `VALIDATION_ERROR` | New password < 8 chars |

> **Side effect:** all existing refresh tokens for this user are revoked. The app should force a fresh login (or re-issue tokens from this response if you add that flow later).

---

## 6. `POST /api/v1/institutions`

**Auth:** None (intentional — bootstrap only).
**Purpose:** Create a new institution. **This is typically called once per deployment** by an administrator, not from the Flutter app. Documented here for completeness.

### Request
```json
{
  "name": "Springfield Elementary",
  "description": "K-5 school",
  "domain": "springfield-school.edu",
  "logo_url": "https://cdn.example.com/logo.png",
  "subscription_tier": "free",
  "max_users": 5000,
  "max_groups": 500
}
```

### 201 Created
```json
{
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
    "name": "Springfield Elementary",
    "domain": "springfield-school.edu",
    "subscription_tier": "free",
    "max_users": 5000,
    "max_groups": 500,
    "created_at": "2026-04-18T14:32:10.123Z",
    "updated_at": "2026-04-18T14:32:10.123Z"
  }
}
```

---

## Flutter reference snippet

```dart
// Minimal Dio client with auth header and 401 refresh.
final dio = Dio(BaseOptions(
  baseUrl: 'http://localhost:8000/api/v1',
  headers: {'Content-Type': 'application/json'},
));

dio.interceptors.add(InterceptorsWrapper(
  onRequest: (options, handler) async {
    final token = await secureStorage.read(key: 'access_token');
    if (token != null) options.headers['Authorization'] = 'Bearer $token';
    handler.next(options);
  },
  onError: (err, handler) async {
    if (err.response?.statusCode == 401) {
      // Try refresh once, then retry original request.
      final refresh = await secureStorage.read(key: 'refresh_token');
      if (refresh != null) {
        final r = await dio.post('/auth/refresh',
            data: {'refresh_token': refresh});
        final data = r.data['data'];
        await secureStorage.write(key: 'access_token', value: data['access_token']);
        await secureStorage.write(key: 'refresh_token', value: data['refresh_token']);
        final retry = await dio.fetch(err.requestOptions);
        return handler.resolve(retry);
      }
    }
    handler.next(err);
  },
));
```

---

## Change log

| Commit | Change |
|--------|--------|
| `7197905` | Initial contract (auth module live) |
