---
title: "SLIs, SLOs, and Error-Budget Policy"
status: draft
created: 2026-04-18
stage: 3
owners:
  - backend
  - ops
consumers:
  - all Stage-4 modules
  - websocket-scaling.md (message-delivery SLO drives backpressure design)
  - api-versioning.md (p95 budget applies per-version)
  - privacy-erasure.md (erasure SLA cross-linked)
---

# SLIs, SLOs, and Error-Budget Policy

## Context

DuttaMessenger is a private institutional messaging platform deployed to a
single school with 1 000–5 000 users. It is self-hosted on a single VPS or
small Kubernetes cluster, operated by the institution's IT lead with no
dedicated SRE team. All Stage-4 modules need a shared understanding of what
"working correctly" means before they ship, and the load-test suite in
Stage 6 needs specific numeric targets to verify.

This RFC establishes the measurable indicators (SLIs), the commitments
(SLOs), and the budget/alert policy that governs reliability work between
now and the school pilot launch.

Right-sizing note: this is a school's internal communication tool, not
public SaaS. Users tolerate 10–15 minutes of downtime once a month; they
will not tolerate 30 seconds of lost or silently-dropped messages. The SLOs
below reflect that priority inversion.

## Decision

Five SLIs — API availability, API p95 latency, message delivery latency,
WebSocket connection availability, and push delivery rate — are tracked via
existing Prometheus metrics. Monthly SLO targets are set conservatively
(99.95 % API availability ≈ 22 min/month budget) while message-delivery and
WebSocket targets are tighter because users feel those failures immediately.
Error budgets are managed with a half-month feature-freeze rule: when more
than 50 % of any monthly budget is consumed before the 15th of the month,
feature work on that area stops until the next window opens.

---

## SLI Definitions

All SLIs are computed over a **5-minute rolling window** unless stated
otherwise. Prometheus is the single source of truth; Grafana boards
visualise the windows and alert on burn rate.

### SLI 1 — API Request Success Rate

```
good_requests  = sum(rate(http_requests_total{status=~"2xx|3xx"}[5m]))
total_requests = sum(rate(http_requests_total[5m]))
SLI = good_requests / total_requests
```

Metric: `http_requests_total{handler, method, status}` (labelled by
prometheus-fastapi-instrumentator, confirmed live in `/metrics` output).

Exclusions: `/metrics`, `/health` (these are infrastructure probes, not
user traffic — they are already excluded by the instrumentator config in
`src/shared/observability/metrics.py`).

### SLI 2 — API p95 Latency per Route

```
SLI = histogram_quantile(0.95,
        sum by (handler, le) (
          rate(http_request_duration_seconds_bucket{handler!~"/metrics|/health"}[5m])
        ))
```

Metric: `http_request_duration_seconds` (histogram emitted by
prometheus-fastapi-instrumentator). Tracked per `handler` label so each
route has its own SLI.

### SLI 3 — Message Delivery Latency (Online Recipients)

```
SLI = histogram_quantile(0.95,
        rate(dutta_message_delivery_latency_seconds_bucket[5m]))
```

Metric: `dutta_message_delivery_latency_seconds` (histogram, declared in
`src/shared/observability/metrics.py`). Measured from the instant the
message row is committed to Postgres to the instant the WebSocket frame
lands at every currently-connected recipient. Offline recipients are
excluded (their delivery is governed by push SLI 5).

### SLI 4 — WebSocket Connection Availability

```
SLI = dutta_websocket_connections / expected_connections
```

Metric: `dutta_websocket_connections` (gauge,
`src/shared/observability/metrics.py`). `expected_connections` is computed
as the 30-minute peak of the same gauge, rolling, so a genuine low-traffic
period does not trigger alerts. Minimum floor: if fewer than 5 connections
are expected, this SLI is not evaluated (prevents false positives during
maintenance windows and off-hours).

### SLI 5 — Push Delivery Confirmation Rate

```
SLI = push_confirmed_total / push_sent_total   (60-second window)
```

Metrics: `dutta_push_confirmed_total` and `dutta_push_sent_total` — to be
added in Stage 4f (notifications module) following the same pattern as the
counters already in `metrics.py`. The counter labels will include
`{result: "delivered" | "bounced" | "expired"}` so the dashboard can split
confirmed vs. failed vs. stale registrations.

---

## SLO Targets

### Summary Table

| # | SLI | Target | Window | Budget (monthly) |
|---|-----|--------|--------|-----------------|
| 1 | API availability (success rate ≥ threshold) | 99.95 % | Rolling 30 days | ≈ 22 min downtime |
| 2a | API p95 latency — default routes | < 300 ms | 5-min rolling | burn alert only |
| 2b | API p95 latency — `/api/v1/auth/login` | < 1 000 ms | 5-min rolling | burn alert only |
| 2c | API p95 latency — `/api/v1/media/upload` | < 3 000 ms | 5-min rolling | burn alert only |
| 3 | Message delivery p95 (online recipients) | < 2 s | 5-min rolling | ≈ 43 min/month |
| 4 | WebSocket 5-min availability | ≥ 99.9 % | 5-min rolling | ≈ 44 min/month |
| 5 | Push delivery confirmation (60 s) | ≥ 99 % | 60-second window | — (per-event) |

### SLO 1 — API Availability: 99.95 % Monthly

**Target:** No more than 0.05 % of API requests return 5xx status codes
over a rolling 30-day window.

**Why 99.95 %:** This yields ≈ 22 minutes of error budget per month.
School users access the app during school hours (roughly 08:00–17:00 IST,
5 days/week). A 22-minute outage budget is generous enough to absorb a
single restart-and-drain cycle without panic, yet tight enough that a whole
class period of downtime consumes the full budget and forces corrective
action. Three-nines (99.9 %, 44 min) would be too permissive — students
lose an entire lesson. Four-nines (99.99 %, 4 min) would require a second
availability zone, which is out of scope for a single-VPS pilot.

**Scope:** All `/api/v1/` routes. Health and metrics endpoints excluded.

### SLO 2 — API p95 Latency

**Target (default routes):** p95 response time < 300 ms over a 5-minute
window.

**Why 300 ms:** Human perception research (Nielsen) puts 100 ms as
"instant" and 1 000 ms as the limit for flow. 300 ms is the standard
budget for a JSON read operation over a local network in an institution
where the server is on-premise or in a nearby data centre. This covers
database queries, Redis lookups, and serialisation without requiring
caching heroics.

**Carve-out — `/api/v1/auth/login` < 1 000 ms:** Login runs bcrypt with a
work factor of 12 (takes ≈ 200–400 ms on a modern CPU at low concurrency).
A 300 ms global budget would force reducing the work factor, which is a
security regression. 1 000 ms is the upper bound before users perceive the
login as "broken"; it also gives headroom for occasional GC pauses.

**Carve-out — `/api/v1/media/upload` < 3 000 ms:** File upload involves
multipart parsing, virus scanning (ClamAV or equivalent), and an S3/MinIO
PUT. Even a 1 MB attachment on a school's broadband can take 500–800 ms
for the network leg alone. 3 s p95 is achievable without streaming tricks
and aligns with what mobile clients accept before showing a "slow upload"
spinner.

**Budget note:** Latency SLOs do not have a traditional error budget in the
same sense as availability. Instead, the burn-rate alerts fire when the p95
crosses the threshold for a sustained window, at which point the issue
becomes a backlog ticket or a page (see Alert Routing section).

### SLO 3 — Message Delivery p95 (Online Recipients): < 2 s

**Target:** The 95th-percentile time from a message being committed to
Postgres to the WebSocket frame being received by all currently-connected
recipients is below 2 seconds.

**Why 2 s and why this matters most:** Users feel message delivery delay
directly. If Alice sends a message and Bob (online, same group) sees it
after 3–4 seconds, the conversation feels broken — they will retry,
creating duplicates. A 2-second p95 budget covers:
- Postgres commit + trigger: ≈ 10–50 ms
- Redis pub/sub fan-out: ≈ 20–100 ms (5 000-subscriber group)
- WebSocket frame serialisation and send: ≈ 10–50 ms
- Network RTT (on-premise): ≈ 5–20 ms

...with headroom for a loaded Redis or a momentary GC pause. The
`websocket-scaling.md` RFC uses this 2-second target as the backpressure
trigger: if a per-connection send queue exceeds a depth that would cause
this SLO to be missed, the connection is backpressured or shed.

**Monthly budget:** 99.5 % compliance over 30 days ≈ 3.6 hours. The tighter
5-min window alert (p95 > 5 s for 10 min) catches acute failures before
they drain the monthly budget.

### SLO 4 — WebSocket 5-Minute Availability: ≥ 99.9 %

**Target:** In any 5-minute window with ≥ 5 expected connections, the ratio
of active WebSocket connections to the rolling 30-minute peak is ≥ 99.9 %.

**Why 99.9 % over 5 minutes:** WebSocket drops are visible to users
instantly (the chat UI goes "connecting..."). A 0.1 % tolerance in a
5-minute window allows for ≈ 0.3 seconds of total reconnection time, which
is within a normal client reconnect cycle. Tighter than this would
alert on every rolling redeploy; looser would miss a partial crash that
only drops 10 % of connections.

**Monthly budget:** A 5-minute window fails if the ratio drops below 99.9 %.
288 windows per day × 30 days = 8 640 windows/month. 99.9 % compliance
means ≈ 9 windows (45 minutes) can fail.

**Implementation note:** The gauge `dutta_websocket_connections` is already
declared in `src/shared/observability/metrics.py`. The `websocket-scaling.md`
RFC defines how this gauge is incremented/decremented at the connection
lifecycle hooks.

### SLO 5 — Push Delivery: ≥ 99 % within 60 s

**Target:** Of all push notifications submitted to FCM, ≥ 99 % receive a
confirmed delivery acknowledgement from FCM within 60 seconds of dispatch.

**Why 99 % / 60 s:** FCM is an external service; we cannot guarantee its
uptime. 99 % already absorbs FCM's own SLA gaps and stale device tokens
(users who uninstalled the app). 60 seconds is generous for in-app
notifications (message alerts) but acceptably prompt — a student's phone
will vibrate within a minute of a teacher posting an announcement.

**Note on metrics:** The `dutta_push_confirmed_total` and
`dutta_push_sent_total` counters are not yet declared. They will be added
as part of Stage 4f (notifications module). The SLO is documented here so
the notifications module author has a target before writing the first line
of Celery task code.

---

## Error-Budget Policy

### Monthly Budget per SLO

| SLO | Monthly budget | Calculation |
|-----|---------------|-------------|
| API availability (99.95 %) | ≈ 21.9 min of error time | 43 200 min/month × 0.0005 |
| Message delivery p95 (99.5 % compliance) | ≈ 3.6 hr of non-compliant windows | 8 640 windows × 0.005 |
| WebSocket availability (99.9 %) | ≈ 9 failed 5-min windows | 8 640 windows × 0.001 |
| Push delivery (99 %) | 1 % of dispatched pushes may miss SLO | per-event, not time-based |

### Burn-Rate Alerts

Burn rate = how fast the monthly error budget is being consumed relative to
the expected constant rate. A burn rate of 1 means the budget is consumed
at exactly the monthly rate; a rate of 14.4 means the full monthly budget
will be gone within 2 days.

**Fast burn (page oncall immediately):**

| SLO | Condition | Rationale |
|-----|-----------|-----------|
| API availability | Burn rate > 14.4 (≈ 2 % budget/hour) for ≥ 2 min | Full budget gone in < 2 days |
| Message delivery | p95 > 5 s sustained for ≥ 10 min | 2.5× the SLO threshold — acute failure |
| WebSocket | Connection drop > 30 % of expected in 5 min | Catastrophic partial outage |

**Slow burn (create backlog ticket, not a page):**

| SLO | Condition | Rationale |
|-----|-----------|-----------|
| API availability | Burn rate > 1.0 sustained for ≥ 6 hr | Budget will run out before month end |
| API p95 latency | p95 > SLO threshold for ≥ 3 consecutive 5-min windows | Sustained degradation, not a spike |
| Message delivery p95 | p95 > 2 s sustained for ≥ 30 min | Chronic but not catastrophic |
| Push delivery | Rate < 99 % for ≥ 3 consecutive 60-s windows | Possible FCM or token issue |

### Half-Month Feature-Freeze Rule

**If more than 50 % of any SLO's monthly error budget is consumed before
the 15th of the month**, the following applies to the affected SLO:

1. **Feature work stops** for that SLO's owning module until the next
   calendar month begins.
2. All engineering time for that module is redirected to reliability work:
   root-cause analysis, preventive changes, improved tests.
3. The incident and remediation are documented in `docs/postmortems/`.
4. The IT lead is notified within 24 hours with a plain-language summary.

**Rationale:** Consuming half the budget in half the time implies a
structural problem, not a one-off spike. Continuing to ship features into a
degraded module risks burning the remaining budget and triggering a full
outage before the monthly reset.

### Planned Maintenance Exclusion

One **quarterly 30-minute maintenance window** per quarter is excluded from
the availability calculation. The window must be:
- Announced to the IT lead ≥ 48 hours in advance.
- Recorded in the maintenance log at `docs/maintenance-log.md`.
- Scheduled outside school hours (before 07:30 or after 18:00 IST on
  weekdays, or on weekends).

Unplanned downtime during this window still counts against the budget.
The exclusion applies only to the scheduled window duration.

---

## Measurement and Reporting

### Scrape Configuration

```
scrape_interval: 15s
evaluation_interval: 15s

scrape_configs:
  - job_name: dutta_messenger
    static_configs:
      - targets: ['app:8000']   # or each pod in k8s
    metrics_path: /metrics
```

The `/metrics` endpoint is exposed by `register_metrics()` in
`src/shared/observability/metrics.py` and confirmed live as of the
2026-04-17 smoke run (see `docs/MANUAL_SMOKE.md` step 11).

### Reporting Cadence

| Cadence | Audience | Content |
|---------|----------|---------|
| Weekly (every Monday) | Engineering team | SLO compliance %, burn rate trend, open reliability tickets |
| Monthly (first week of month) | Institution IT lead | Plain-language summary: was the service within SLO? Any incidents? Budget remaining. |
| Ad-hoc | IT lead | Any fast-burn alert that fired — notify within 4 hours with a brief explanation |

The monthly report to the IT lead should be a 1-page PDF or email. Avoid
Prometheus terminology; use plain language ("the chat was slow for 8 minutes
on Thursday" rather than "p95 latency SLO violated for 2 windows").

### Grafana Dashboard

One dashboard: **DuttaMessenger — Service Health**. Panels:

| Row | Panel | Metric / Query |
|-----|-------|----------------|
| Overview | API success rate (30-day rolling) | `sum(rate(http_requests_total{status=~"2xx|3xx"}[30d])) / sum(rate(http_requests_total[30d]))` |
| Overview | Error budget remaining (%) | `(1 - error_rate) / 0.0005 * 100` |
| Overview | WebSocket connections (live) | `dutta_websocket_connections` |
| Latency | p95 by route (heatmap) | `histogram_quantile(0.95, sum by (handler, le) (rate(http_request_duration_seconds_bucket[5m])))` |
| Latency | Login p95 vs 1000 ms threshold | filtered `handler="/api/v1/auth/login"` |
| Latency | Upload p95 vs 3000 ms threshold | filtered `handler="/api/v1/media/upload"` |
| Messages | Message delivery p95 (5-min) | `histogram_quantile(0.95, rate(dutta_message_delivery_latency_seconds_bucket[5m]))` |
| Messages | Messages sent per minute | `rate(dutta_messages_sent_total[1m])` |
| Push | Push delivery rate (%) | `rate(dutta_push_confirmed_total[60s]) / rate(dutta_push_sent_total[60s]) * 100` |
| Push | Celery task latency | `histogram_quantile(0.95, rate(dutta_celery_task_latency_seconds_bucket{task_name=~"push.*"}[5m]))` |
| Errors | Auth failures by reason | `rate(dutta_auth_failures_total[5m])` by `reason` label |
| Errors | Rate-limited requests | `rate(dutta_rate_limited_requests_total[5m])` |

Each SLO row has a horizontal reference line at the target value.

---

## Alert Routing

### Page Oncall (immediate, wakes someone up)

These conditions indicate the school is actively experiencing degraded or
broken service.

| Alert | Condition | Severity |
|-------|-----------|----------|
| `DuttaAPIBurnRateHigh` | API error burn rate > 14.4 for ≥ 2 min | P1 |
| `DuttaWSConnectionDrop` | WS connections drop > 30 % of 30-min peak in 5 min | P1 |
| `DuttaMessageDeliveryHigh` | Message delivery p95 > 5 s for ≥ 10 min | P1 |

For the school pilot, "oncall" is the IT lead's mobile number. There is no
dedicated SRE rotation. P1 alerts go to the IT lead's phone via PagerDuty
(or a simple SMS webhook if PagerDuty is not provisioned yet).

### Backlog Ticket (next business day)

These conditions indicate a slow degradation that will burn the monthly
budget if not addressed, but does not require waking anyone up.

| Alert | Condition |
|-------|-----------|
| `DuttaAPIBurnRateSustained` | API error burn rate > 1.0 for ≥ 6 hr |
| `DuttaLatencyDegraded` | Any route p95 > SLO threshold for ≥ 3 consecutive windows |
| `DuttaMessageDeliveryElevated` | Delivery p95 > 2 s for ≥ 30 min |
| `DuttaPushRateLow` | Push delivery rate < 99 % for ≥ 3 consecutive 60 s windows |
| `DuttaHalfBudgetConsumed` | Any SLO budget > 50 % consumed before the 15th |

Backlog tickets are auto-created via the Alertmanager webhook to the
project issue tracker. The ticket must be assigned within 24 hours and
resolved before the half-month feature-freeze rule triggers.

---

## Details

### Scope

- All SLIs and SLOs apply to the production deployment of `src/` as
  described in `reference-docs/DEPLOYMENT.md`.
- Staging and local development environments are monitored but excluded from
  SLO calculations.
- Stage 6 load-test runs will verify that the system meets each SLO under
  simulated peak load (5 000-user group fanout, 10 000 concurrent WS
  clients, login storm) before the school pilot launch.

### Non-Goals

- **End-to-end encryption latency** is not measured here. E2EE is deferred
  per the project plan.
- **CDN or edge caching** is not in scope for the pilot — file downloads
  go directly to MinIO.
- **Multi-region availability** is explicitly out of scope for a single-VPS
  deployment.
- **Flutter client-side performance** (app render time, image decode) is
  outside backend SLOs.
- **Data retention SLA (DPDP):** the right-to-erasure completion time
  target (30 days from request to confirmed deletion) is defined in
  `docs/design/privacy-erasure.md`. This RFC cross-links it for completeness
  but does not own the measurement.

### Implementation Sketch

1. **Metrics that already exist** (`src/shared/observability/metrics.py`):
   - `http_requests_total` — SLI 1 and 2 ✓
   - `http_request_duration_seconds` — SLI 2 ✓
   - `dutta_message_delivery_latency_seconds` — SLI 3 ✓
   - `dutta_websocket_connections` — SLI 4 ✓

2. **Metrics to add in Stage 4f** (notifications module):
   - `dutta_push_sent_total{result}` — Counter
   - `dutta_push_confirmed_total` — Counter
   - Add to `src/shared/observability/metrics.py` following the existing
     pattern (single import point for all modules).

3. **Prometheus recording rules** (to be added to `ops/prometheus/rules.yml`
   in Stage 6 when the ops/ directory is created):
   - Pre-compute 30-day availability ratio to avoid high-cardinality
     range queries in Grafana.
   - Pre-compute per-route p95 for the dashboard.

4. **Alertmanager config** (`ops/alertmanager/config.yml`):
   - P1 route → PagerDuty / SMS webhook.
   - Backlog route → issue tracker webhook.
   - Inhibit rules: P1 silences all backlog alerts for the same SLO (to
     avoid ticket spam during an active incident).

### Alternatives Considered

**99.9 % API availability (44 min/month):** Rejected. 44 minutes is more
than one full class period — acceptable to SaaS companies with millions of
users, not acceptable when 200 students need to communicate with their
teacher right now.

**99.99 % API availability (4 min/month):** Rejected. Requires active-active
redundancy (two app nodes, load balancer, zero-downtime deploys). The pilot
is a single VPS. This SLO would be violated by the very first restart.

**Client-side message-delivery confirmation instead of server-side metric:**
Rejected. Client-side metrics require a telemetry SDK in the Flutter app
and introduce a dependency on client software versions. Server-side fan-out
latency (time from DB commit to WS send) is simpler, already within the
server's control, and a good enough proxy for the user experience.

---

## Consequences

### Positive

- Every Stage-4 module author has a concrete performance target before
  writing the first line of service code.
- The load-test suite in Stage 6 has specific numeric assertions to verify.
- The IT lead receives a plain-language monthly summary — no black box.
- Burn-rate alerts catch degradation early, before the budget runs out.
- The half-month feature-freeze rule forces reliability to be treated as
  first-class, not perpetually deferred.

### Negative / Tradeoffs

- The `dutta_push_*` counters are not yet implemented. SLO 5 cannot be
  measured until Stage 4f lands. The SLO is documented now to set the
  target, not because it is currently enforced.
- The WebSocket availability SLI depends on `expected_connections` being
  computed from a rolling peak, which requires a Prometheus recording rule.
  Until Stage 6 sets up `ops/prometheus/rules.yml`, this SLO is monitored
  manually.
- Quarterly maintenance windows require coordination with the IT lead 48
  hours in advance. This is a process commitment, not a code commitment.

### Future Work

- When `messages` table exceeds 10M rows, revisit message-delivery latency
  SLO — the Postgres commit may slow down and the 2-second budget may need
  adjustment. See `docs/design/message-partitioning.md`.
- If the school expands to multi-campus (multiple institutions in one
  deployment), availability SLOs should be computed per-institution, not
  globally. See `docs/design/tenant-isolation.md`.
- Privacy-erasure completion time (30 days, per DPDP) is a separate SLA
  tracked in `docs/design/privacy-erasure.md`. When the erasure pipeline is
  built in Stage 4 or later, wire a `dutta_erasure_completion_seconds`
  histogram and add a Grafana panel to the dashboard.

---

## Cross-References

- **Consumed by:** `src/modules/chat/` (message delivery latency),
  `src/modules/notifications/` (push delivery SLO), all modules (API SLOs).
- **Drives:** `docs/design/websocket-scaling.md` — the 2-second delivery
  SLO is the backpressure design trigger.
- **Applies per version:** `docs/design/api-versioning.md` — p95 budgets
  apply independently to `/v1/` and future `/v2/` routes.
- **Cross-linked SLA:** `docs/design/privacy-erasure.md` — DPDP erasure
  completion within 30 days is a separate commitment, tracked separately.
- **Reference doc:** `reference-docs/ARCHITECTURE.md` — system topology
  that determines which metrics are collectible at the server boundary.
- **Live metrics confirmation:** `docs/MANUAL_SMOKE.md` step 11 — `/metrics`
  endpoint was confirmed returning `dutta_*` series and
  `http_requests_total` on 2026-04-17.
- **Metrics source:** `src/shared/observability/metrics.py` — all
  application-level counters, gauges, and histograms referenced by SLIs 1–4.
