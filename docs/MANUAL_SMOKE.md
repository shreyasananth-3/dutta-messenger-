# Manual Live-Server Smoke — Auth Slice

A ~5-minute drill that boots the real FastAPI server, exercises every auth
endpoint the UI will hit, and proves rows land in the database. Run this
**before declaring the auth slice ready for the UI team**, and again any
time `src/modules/auth/` or `src/shared/` changes.

Last executed: **2026-04-17 (commit after `48062c3`)**. All 6 paths returned
expected status codes; every expected row landed in Postgres.

---

## Prerequisites

- Local Postgres 17 (Homebrew) running with `dutta_messenger` database —
  see [LOCAL_SETUP.md](LOCAL_SETUP.md).
- Local Redis responding to `PING`.
- `.venv` installed with `pip install -e ".[dev,test]"`.
- `.env` at repo root (points at the dev DB + a `SECRET_KEY`).
- `make migrate` has been applied at least once.

Sanity check:

```bash
psql -h localhost -U $USER -d dutta_messenger -c "SELECT current_database();"
redis-cli -h localhost -p 6379 ping
```

Both should respond without error.

---

## Recipe

All steps are copy-pasteable. We use port `8765` so nothing collides with a
hypothetical production server on `8000`. All artifacts are written to
`/tmp/dm-smoke/` so re-runs are isolated.

### 1. Start the server (background)

```bash
mkdir -p /tmp/dm-smoke
.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8765 \
  > /tmp/dm-smoke/uvicorn.log 2>&1 &
echo $! > /tmp/dm-smoke/uvicorn.pid
sleep 2
tail -5 /tmp/dm-smoke/uvicorn.log       # expect: "Application startup complete."
```

### 2. Health probe

```bash
curl -sS http://127.0.0.1:8765/health
# -> {"status":"healthy"}
```

### 3. Create an institution (open endpoint)

```bash
STAMP=$(date +%s)
curl -sS -H "Content-Type: application/json" \
  -d "{\"name\":\"SmokeSchool-$STAMP\",\"domain\":\"smoke-$STAMP.test\"}" \
  http://127.0.0.1:8765/api/v1/institutions \
  > /tmp/dm-smoke/inst.json
INST_ID=$(python3 -c "import json; print(json.load(open('/tmp/dm-smoke/inst.json'))['data']['id'])")
echo "institution = $INST_ID"
```

### 4. Seed the first user

Direct registration is rejected by design — the auth service only accepts
users who arrive via an invitation. To bootstrap the first admin we call
the service layer from Python. This is the same entry point `scripts/seed.py`
will call once it's fleshed out in Stage 4.

```bash
cat > /tmp/dm-smoke/seed_user.py <<'PY'
import asyncio, os, sys
sys.path.insert(0, os.getcwd())
from src.shared.database import SessionLocal
from src.modules.auth.services.auth_service import AuthService

INST_ID, EMAIL, PASSWD = sys.argv[1], sys.argv[2], sys.argv[3]

async def main():
    async with SessionLocal() as db:
        user = await AuthService.register_user(
            db=db, institution_id=INST_ID,
            email=EMAIL, password=PASSWD, full_name="Smoke Admin",
        )
        await db.commit()
        print(f"user_id={user.id}")

asyncio.run(main())
PY

.venv/bin/python /tmp/dm-smoke/seed_user.py "$INST_ID" \
  "admin@smoke.test" "Sup3rStr0ng!"
```

### 5. Login (get JWT)

```bash
curl -sS -H "Content-Type: application/json" \
  -d '{"email":"admin@smoke.test","password":"Sup3rStr0ng!"}' \
  http://127.0.0.1:8765/api/v1/auth/login \
  > /tmp/dm-smoke/login.json

ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/dm-smoke/login.json'))['data']['access_token'])")
REFRESH=$(python3 -c "import json; print(json.load(open('/tmp/dm-smoke/login.json'))['data']['refresh_token'])")
echo "access len=${#ACCESS}, refresh len=${#REFRESH}"
```

### 6. Invite a second user (authed endpoint)

```bash
curl -sS -H "Authorization: Bearer $ACCESS" \
     -H "Content-Type: application/json" \
     -d '{"email":"invitee@smoke.test"}' \
     http://127.0.0.1:8765/api/v1/auth/invite
# -> 201, body: { "data": { "invitation": { ... }, "message": "Invitation sent to invitee@smoke.test" } }

# Negative: same call without the Authorization header
curl -sS -o /dev/null -w "%{http_code}\n" \
     -H "Content-Type: application/json" -d '{"email":"nope@smoke.test"}' \
     http://127.0.0.1:8765/api/v1/auth/invite
# -> 401
```

### 7. Register the invitee via the invitation link

The invitation token is not returned in the API response (it would be emailed
to the invitee in production). Pull it from the DB for the smoke run:

```bash
TOKEN=$(psql -h localhost -U $USER -d dutta_messenger -At \
  -c "SELECT token FROM user_invitations WHERE email='invitee@smoke.test' ORDER BY created_at DESC LIMIT 1;")

curl -sS -H "Content-Type: application/json" \
  -d "{\"email\":\"invitee@smoke.test\",\"password\":\"Inv1teeP@ss!\",\"full_name\":\"Invitee User\",\"invitation_token\":\"$TOKEN\"}" \
  http://127.0.0.1:8765/api/v1/auth/register
# -> 201, the new user, message "Account created successfully from invitation"
```

### 8. Refresh the access token

```bash
curl -sS -H "Authorization: Bearer $ACCESS" \
     -H "Content-Type: application/json" \
     -d "{\"refresh_token\":\"$REFRESH\"}" \
     http://127.0.0.1:8765/api/v1/auth/refresh
# -> 200, new access_token + refresh_token

# Negative: garbage refresh token
curl -sS -o /dev/null -w "%{http_code}\n" \
     -H "Authorization: Bearer NOT_A_JWT" \
     -H "Content-Type: application/json" -d '{"refresh_token":"garbage"}' \
     http://127.0.0.1:8765/api/v1/auth/refresh
# -> 401
```

### 9. Verify DB state

```bash
psql -h localhost -U $USER -d dutta_messenger <<'SQL'
SELECT 'institutions' tbl, count(*) FROM institutions
UNION ALL SELECT 'users',            count(*) FROM users
UNION ALL SELECT 'user_invitations', count(*) FROM user_invitations
UNION ALL SELECT 'refresh_tokens',   count(*) FROM refresh_tokens
UNION ALL SELECT 'audit_logs',       count(*) FROM audit_logs
ORDER BY tbl;
SQL
```

Expected after a fresh run:

| table | rows |
|---|---|
| institutions | ≥1 |
| users | ≥2 (admin + invitee) |
| user_invitations | ≥1 (`accepted_at` set) |
| refresh_tokens | ≥2 (one per `login` + `refresh`) |
| audit_logs | **0 — see gap A below** |

### 10. Stop the server

```bash
kill "$(cat /tmp/dm-smoke/uvicorn.pid)"
```

### 11. Infrastructure extras

- `GET /metrics` returns Prometheus output including custom `dutta_*`
  series and live `http_requests_total{handler="…",status="2xx"}` counters.
- `GET /openapi.json` currently publishes 6 paths (the auth slice) — the
  same set committed at [docs/ui-contract/openapi.json](ui-contract/openapi.json).

---

## Gaps surfaced by this smoke run

Three Stage-2 ragged edges that the automated tests didn't catch (because
each individually is a surface-level consistency issue, not a functional
bug). None block the auth slice shipping, but all three should be cleaned
up before/during Stage 4.

### Gap A — `audit_logs` is empty after every mutation

CLAUDE.md says "Every mutation writes to `audit_logs` via
`src/shared/security/audit.py`" but this smoke run made 5 mutations
(institution create, user register × 2, login, invite, refresh) and
`audit_logs` has 0 rows. The audit infrastructure exists (`audit.py` is
unit-tested at 100% coverage) but no route or service method calls it.

**Fix scope:** one or two lines per mutating endpoint, wiring an
`audit.log(...)` call inside the service layer. Best done alongside the
Stage 3 `tenant-isolation.md` RFC so the actor/institution/action taxonomy
is decided once.

### Gap B — Inconsistent error envelope

CLAUDE.md mandates `{"error": {"code", "message", "details"}}` for every
error. In practice:

- `AppException` → correct envelope (e.g. refresh with garbage JWT → 401
  `{"error":{"code":"AUTHENTICATION_FAILED", …}}`).
- Raw `HTTPException(detail="…")` → FastAPI default `{"detail":"…"}`
  (e.g. invite without auth → 401 `{"detail":"Not authenticated"}`; register
  without invitation → 400 `{"detail":"Direct registration not allowed."}`).

**Fix scope:** replace `HTTPException(detail=...)` with subclasses of
`AppException` in `src/modules/auth/routes/auth_routes.py` (≈4 call sites),
or add a response middleware that normalises the shape for any `detail`-style
HTTPException. The first is cleaner; the second is UI-team-friendly if other
modules will repeat the mistake.

### Gap C — Refresh tokens are not rotated on `/auth/refresh`

After `login` + `refresh` for the same user we end up with two refresh
tokens both `revoked=false, still_valid=true`. Industry practice for
session security is to revoke the old refresh token the moment a new one
is issued (single-use refresh), which also provides replay detection
when a stolen token is later presented.

**Fix scope:** in `AuthService.refresh_access_token`, set
`revoked_at = now()` on the consumed refresh token before inserting the
new one. Add a test that a replay of the old refresh token returns 401.

---

## When to re-run this smoke

- Before merging any PR that touches `src/modules/auth/` or `src/shared/`.
- Before tagging a release.
- On a fresh clone, as part of LOCAL_SETUP.md's final acceptance check.
- Anytime `make test` has been amended (to make sure the real server agrees
  with the ASGI-client tests).

If the automated suite (`make test`) is green but this smoke fails, something
in the server bootstrap, middleware wiring, or live-DB interaction is broken
that the in-process tests are hiding. Investigate before shipping.
