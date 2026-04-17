# Local Testing Guide — Multi-User, Multi-Instance, Audit Review

**Audience:** anyone who needs to prove the backend holds up under realistic usage — multiple users signing in at once, many clients chatting, long-running sessions — and inspect exactly what happened after the fact.

**Prerequisite:** [`LOCAL_SETUP.md`](LOCAL_SETUP.md) done. DB is live, `uvicorn` boots, auth endpoints return 200.

---

## 1. Where evidence of every action is recorded

Three independent layers — you need all three to understand "what happened".

| Layer | What it captures | Where it lives | How to query |
|-------|-----------------|----------------|--------------|
| **Audit table** | Every mutating business action (login, message sent/deleted, role granted, etc.) with actor, institution, resource, metadata, timestamp | PostgreSQL table `audit_logs` (persisted) | `psql ... -c "SELECT ..."` |
| **Structured logs** | Every request, every service call, every error — with correlation ID tying it to one request | `stdout` of the `uvicorn` process (human pretty in dev, JSON in prod) | `tail -f logs/app.log` (if piped) or terminal |
| **Metrics** | Request rate, p50/p95/p99 latency, active WebSocket connections, rate-limit rejections, message delivery latency | In-memory on the running process | `curl http://localhost:8000/metrics` |

**Correlation ID ties them together.** Every request gets an `X-Request-ID`. It appears:
- In the response header
- In every structlog line produced during the request
- (Soon, Stage 4d) attached to audit rows for that request too

### 1a. Audit — where exactly?

**Audit is in the database, not local files.** The table is `audit_logs` (created by `0001_baseline_schema`). Schema:

| Column | Meaning |
|--------|---------|
| `id` | UUID, primary key |
| `actor_id` | the user who did it |
| `institution_id` | tenant scope |
| `action` | canonical event name (e.g. `user.login.success`, `message.deleted`) |
| `resource_type` | `user`, `message`, `group`, `role`, `media`, `fcm_token`, … |
| `resource_id` | UUID of the thing acted upon |
| `metadata` | JSONB — context (conversation_id, old/new values) |
| `created_at` | UTC timestamp |

Canonical action names are declared in `src/shared/security/audit.py` (the `AuditEvent` enum). Adding a new auditable action = adding an enum entry; ad-hoc strings are not allowed.

Inspect live:
```bash
psql -h localhost -U "$USER" -d dutta_messenger -c \
  "SELECT created_at, action, actor_id, resource_type, resource_id
     FROM audit_logs
     ORDER BY created_at DESC
     LIMIT 50;"
```

Filter by a specific user during a test run:
```bash
psql ... -c "SELECT * FROM audit_logs WHERE actor_id = '<uuid>' ORDER BY created_at;"
```

### 1b. Local log file — opt in

By default, logs go to stdout. To keep them after a test run:

```bash
mkdir -p logs
.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8000 \
  2>&1 | tee "logs/app-$(date +%Y-%m-%d_%H%M%S).log"
```

In prod, structlog outputs JSON — ship those lines to Loki / ELK / Grafana Cloud and query by `correlation_id`.

### 1c. Before you read metrics, know what's counted

Defined in `src/shared/observability/metrics.py`. Key ones for chat load tests:

- `dutta_messages_sent_total{conversation_type="..."}` — total messages persisted
- `dutta_message_delivery_latency_seconds` — histogram (persist → all online recipients)
- `dutta_websocket_connections` — current gauge
- `dutta_auth_failures_total{reason="..."}`
- `dutta_rate_limited_requests_total{rule="..."}`
- `http_request_duration_seconds` — per endpoint (from the FastAPI instrumentator)

Scrape once:
```bash
curl -s http://localhost:8000/metrics | grep -E "^dutta_" | head -20
```

For a live dashboard, point a local Prometheus at the `/metrics` endpoint:
```yaml
# prometheus.yml (minimal)
scrape_configs:
  - job_name: dutta
    scrape_interval: 5s
    static_configs:
      - targets: ["localhost:8000"]
```

---

## 2. Multi-user scenarios — how to run them locally

Three levels of complexity. Pick the simplest one that answers your question.

### 2a. Manual — two or three humans

Open 2–3 browser tabs of `http://localhost:8000/docs` (Swagger UI). Log in as different users in each. Perform actions. Check audit + logs afterward.

**Good for:** smoke-checking new endpoints, verifying tenant isolation manually.

### 2b. Scripted — tens of users, deterministic

A Python script that hits the API as N users in parallel. Template (we'll ship a filled-in version with Stage 6):

```python
# scripts/simulate_users.py
import asyncio, httpx, random, time, uuid

BASE = "http://localhost:8000/api/v1"

async def one_user(i: int, client: httpx.AsyncClient) -> None:
    email = f"user{i}@test.school.edu"
    # login
    r = await client.post(f"{BASE}/auth/login", json={
        "email": email, "password": "test-password",
        "institution_id": "<seeded-institution-id>",
    })
    token = r.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # do stuff
    for _ in range(10):
        await client.post(f"{BASE}/messages", headers=headers, json={
            "conversation_id": "<seed>", "content": f"hi from {i}",
        })
        await asyncio.sleep(random.uniform(0.1, 1.0))

async def main(n: int = 50) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await asyncio.gather(*[one_user(i, client) for i in range(n)])

if __name__ == "__main__":
    t0 = time.monotonic()
    asyncio.run(main(50))
    print(f"done in {time.monotonic() - t0:.1f}s")
```

Seed the test users once (via `scripts/seed.py` — scaffold today, complete with Stage 4a).

### 2c. Load — hundreds to thousands of users, realistic patterns

[`k6`](https://k6.io/) is the tool of choice. Install:
```bash
brew install k6
```

Template script (Stage 6 will ship filled versions in `tests/load/`):

```javascript
// tests/load/login_storm.js
import http from "k6/http";
import { sleep, check } from "k6";

export const options = {
  stages: [
    { duration: "30s", target: 200 },   // ramp to 200 VUs
    { duration: "2m",  target: 200 },   // hold
    { duration: "30s", target: 0 },     // ramp down
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],        // <1% errors
    http_req_duration: ["p(95)<500"],      // 95th percentile < 500ms
  },
};

export default function () {
  const res = http.post("http://localhost:8000/api/v1/auth/login",
    JSON.stringify({
      email: `user${__VU}@test.school.edu`,
      password: "test-password",
      institution_id: __ENV.INSTITUTION_ID,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  check(res, { "status is 200": (r) => r.status === 200 });
  sleep(1);
}
```

Run:
```bash
INSTITUTION_ID=<seed-uuid> k6 run tests/load/login_storm.js
```

k6 prints p50/p95/p99 latency, request rate, error rate. Simultaneously tail `/metrics` or watch Prometheus to cross-reference.

---

## 3. Chat + reaction time measurement (when Stage 4d lands)

The chat module is not live yet (`ENABLE_CHAT=false`). When it ships, this is how to measure end-to-end latency:

1. Start the server with `ENABLE_CHAT=true`.
2. Spin up N WebSocket clients in parallel (k6 supports `ws`, or use `websockets` python lib).
3. Each client joins a conversation and either publishes or listens.
4. Measure two things per message:
   - **Publish → persist latency:** from client send to server 200 OK on `POST /messages` — visible in `http_request_duration_seconds{handler="/api/v1/messages",method="POST"}`.
   - **Persist → deliver latency:** from message row `created_at` to recipient's WebSocket receive — recorded in `dutta_message_delivery_latency_seconds`.
5. After the run, inspect:
   - `SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (delivered_at - created_at))) FROM message_reads;` (Stage 4d will populate `delivered_at`)
   - `audit_logs WHERE action='message.reacted' AND created_at BETWEEN ...` for reaction timeline.
   - `dutta_websocket_connections` gauge history to confirm the sockets stayed connected.

**Target SLO for school-scale (1k–5k):** p95 delivery < 2 seconds, zero dropped WebSocket messages during a 10-minute soak test. Final numbers will be ratified in `docs/design/slo.md` (Stage 3).

---

## 4. After a test run — the 4-step audit review

Every time you run a local test (manual, scripted, or k6), do this to understand what happened:

### Step 1 — pick a correlation ID
From the test output or response headers, grab one `X-Request-ID` from a request that failed or felt slow.

### Step 2 — find every log line
```bash
grep "<correlation-id>" logs/app-*.log
```
You'll see the request enter, DB queries, service branches, the response. The full story.

### Step 3 — read the audit trail
```sql
SELECT created_at, action, resource_type, metadata
  FROM audit_logs
  WHERE created_at BETWEEN '<test-start>' AND '<test-end>'
  ORDER BY created_at;
```
Every mutation should be here. If an action fired but no audit row exists, the audit writer silently failed (logged as `audit_write_failed`) — investigate.

### Step 4 — cross-check metrics
```bash
curl -s http://localhost:8000/metrics > metrics-after.txt
diff metrics-before.txt metrics-after.txt | head -50
```
Compare `dutta_messages_sent_total`, `dutta_auth_failures_total`, error counters. Should match what the audit + logs say.

---

## 5. Test scenarios to actually run (before declaring the backend "ready")

> Stages 4+6 ship the scripts for these. The list is frozen now so you know what "ready" means.

### 5a. Multi-user concurrency (single institution)
50 users in one institution. Each logs in, sends 20 messages in a shared group, reacts to 5 messages, reads 10 messages. Pass criteria:
- All logins succeed (no 429 under 300/min default limit).
- All messages visible to all members within p95 < 2s.
- `audit_logs` has 50 × 36 = 1800 rows for this run (login + 20 send + 5 react + 10 read).
- No `TenantScopeViolation` entries in logs.

### 5b. Multi-institution isolation fuzz
Create institutions A and B with 5 users each. Run a script where A's users attempt to read, update, and delete B's messages / groups / roles. Every attempt **must** return 404 (never 200, never 403). Verify in audit: no cross-tenant rows.

### 5c. Rate-limit behaviour
Single user spams login 1000 times in 60s. Expect:
- First ~300 succeed (or 401 on bad creds).
- Rest return 429 with `Retry-After` header.
- `dutta_rate_limited_requests_total{rule="..."}` increments correctly.

### 5d. Token lifecycle
User logs in → receives tokens. Access token expires (can fast-forward with `freezegun` in tests or set `ACCESS_TOKEN_EXPIRE_MINUTES=1` in env). A protected request returns 401 with `UNAUTHORIZED`. Refresh returns new pair. Retry succeeds. Change password → refresh token revoked → new refresh attempt returns 401.

### 5e. WebSocket reconnect + resume (Stage 4d)
Client connects, receives 10 messages, closes socket mid-stream. Reconnects with last-seen `message_id`. Server replays messages it missed. No duplicates, no gaps.

### 5f. Long-running soak
10 users, each sending 1 message every 5 seconds, for 30 minutes. Memory stable (no leak), p95 latency stable, no rogue error counters, WebSocket connections do not drop.

### 5g. Media upload burst (Stage 4e)
20 users simultaneously upload 10 MB files. All succeed, presigned URLs work, mime-type validation enforced, no file slips past the 100 MB cap.

### 5h. Push-notification fan-out (Stage 4f)
A message in a 1000-member group triggers exactly 999 push notifications (not self), batched via Celery. Each batch completes within p95 < 5s. Failed FCM tokens get marked inactive.

---

## 6. CI equivalents

Every scenario above will also run in CI against a fresh Postgres (nightly for load tests, per-PR for functional scenarios). Local = debug; CI = gate.

---

## 7. Summary cheat-sheet

```
╭──────────────────────────────────────────────────────────────╮
│ Before running a test:                                       │
│   curl -s /metrics > metrics-before.txt                      │
│   date +%s  > test-start.txt                                 │
│   tail -F logs/app.log &                                     │
│                                                              │
│ Run your test.                                               │
│                                                              │
│ After:                                                       │
│   curl -s /metrics > metrics-after.txt                       │
│   psql ... "SELECT ... FROM audit_logs WHERE created_at ..." │
│   grep <correlation-id> logs/app.log                         │
│   diff metrics-before.txt metrics-after.txt                  │
╰──────────────────────────────────────────────────────────────╯
```
