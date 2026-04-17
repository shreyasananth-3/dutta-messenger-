# Module: Auth

> **This is the foundation module.** Every other module depends on auth. Build this first.

---

## What This Module Does

- User registration (admin-invited flow)
- Login (email + password → JWT tokens)
- Token refresh (refresh token → new access token)
- Logout (invalidate refresh token)
- Password reset flow
- JWT verification middleware (used by all other modules)

## Dependencies

| Depends On | Why |
|-----------|-----|
| Nothing | Auth is the foundation. It has no module dependencies. |

---

## Authentication Flow

```
1. Admin creates user invitation
   POST /api/v1/auth/invitations → generates invite token

2. Invited user receives link (email/WhatsApp)
   https://app.example.com/register?token={invite_token}

3. User registers with invite token
   POST /api/v1/auth/register
   { "invite_token": "...", "password": "...", "display_name": "..." }
   → Creates user, returns JWT tokens

4. Subsequent logins
   POST /api/v1/auth/login
   { "email": "...", "password": "..." }
   → Returns { access_token, refresh_token, expires_in }

5. Token refresh (before access token expires)
   POST /api/v1/auth/refresh
   { "refresh_token": "..." }
   → Returns new { access_token, expires_in }

6. Logout
   POST /api/v1/auth/logout
   { "refresh_token": "..." }
   → Invalidates the refresh token in Redis
```

---

## JWT Token Design

### Access Token (short-lived)
- **Algorithm**: RS256
- **Expiry**: 15 minutes
- **Payload**:
```json
{
  "sub": "user-uuid",
  "institution_id": "institution-uuid",
  "roles": ["member"],
  "type": "access",
  "iat": 1705312800,
  "exp": 1705313700
}
```

### Refresh Token (long-lived)
- **Algorithm**: RS256
- **Expiry**: 7 days
- **Stored**: Hashed in Redis with TTL matching expiry
- **Payload**:
```json
{
  "sub": "user-uuid",
  "type": "refresh",
  "jti": "unique-token-id",
  "iat": 1705312800,
  "exp": 1705917600
}
```

### Why RS256?
Asymmetric keys (RS256) allow any service to verify tokens using the public key without needing the private key. This is important if we ever add microservices.

---

## API Endpoints

| Method | Path | Auth Required | Purpose |
|--------|------|--------------|---------|
| `POST` | `/api/v1/auth/invitations` | Yes (admin) | Create user invitation |
| `POST` | `/api/v1/auth/register` | No (invite token) | Register with invitation |
| `POST` | `/api/v1/auth/login` | No | Login with credentials |
| `POST` | `/api/v1/auth/refresh` | No (refresh token in body) | Refresh access token |
| `POST` | `/api/v1/auth/logout` | Yes | Invalidate refresh token |
| `POST` | `/api/v1/auth/forgot-password` | No | Request password reset email |
| `POST` | `/api/v1/auth/reset-password` | No (reset token) | Reset password with token |

---

## Password Rules

- Minimum 8 characters
- At least one uppercase, one lowercase, one digit
- Hashed with bcrypt (cost factor 12)
- Never stored in plain text, never logged, never returned in API responses

---

## Database Tables

> Full SQL: [SCHEMA.sql](SCHEMA.sql)

| Table | Purpose |
|-------|---------|
| `users` | Core user record (email, password_hash, status) |
| `refresh_tokens` | Active refresh tokens (stored in Redis, not PostgreSQL) |
| `invitations` | Pending user invitations |
| `password_reset_tokens` | One-time password reset tokens |

---

## Security Considerations

1. **Rate limiting on login**: 10 attempts per minute per email. After 10 failures, lock for 15 minutes.
2. **Refresh token rotation**: Each refresh gives a new refresh token and invalidates the old one.
3. **Concurrent session limit**: Max 5 active refresh tokens per user. Oldest is revoked when limit is hit.
4. **Password reset tokens**: Expire after 1 hour. Single use.
5. **Invite tokens**: Expire after 7 days. Single use.
