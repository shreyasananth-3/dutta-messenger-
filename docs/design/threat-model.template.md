# Threat Model — {MODULE NAME}

> Fill one of these in for every module before it ships. Keep it to one page.
> It's a working doc, not a dissertation. Update when the module changes.

## 1. Scope

- What the module does (one sentence).
- Data it owns (rows, files, events).
- External interfaces (HTTP routes, WebSocket events, Celery tasks, webhooks).

## 2. Trust boundaries

- Who can reach this module? (unauthenticated / any user / admin / internal only)
- What is the tenant boundary? Which column enforces it?
- Does this module call out to any external service? Which credentials?

## 3. STRIDE analysis

| Threat | Applies? | Mitigation |
|---|---|---|
| **S**poofing — can an attacker impersonate a user? | y/n | JWT verified on every request; rotating signing keys |
| **T**ampering — can data be modified in transit or at rest? | y/n | TLS on wire; parameterised queries; audit log on mutations |
| **R**epudiation — can a user deny they did something? | y/n | `audit_logs` row written on every mutation |
| **I**nformation disclosure — can one tenant see another's data? | y/n | `tenant_scoped_query`; RLS policy; cross-tenant fuzz test |
| **D**enial of service — can the module be flooded? | y/n | per-user + per-IP rate limits; backpressure on fan-out |
| **E**levation of privilege — can a user do something they shouldn't? | y/n | ACL decorator checks permission on every route |

## 4. Abuse cases (module-specific)

List the 3–5 ways a malicious insider or compromised account could misuse
this module. Name the mitigation for each.

- Example: *An admin of institution A enumerates usernames of institution B by
  guessing IDs.* → 404 (not 403) on cross-tenant access; `assert_same_institution`
  on every lookup; rate limit enumeration-friendly endpoints.

## 5. Data handling

- PII touched: (email, phone, message content, media bytes, …)
- Retention: (see `docs/design/privacy-erasure.md`)
- Encryption at rest: (DB default; KMS on S3/MinIO)
- Right-to-erasure path: (tombstone user; redact message body; delete media)

## 6. Logging & monitoring

- Key events emitted (structlog `event` keys).
- Prometheus counters/gauges published.
- Alerts wired (SLO breach, error-rate spike).

## 7. Open risks

- What could go wrong that we're NOT mitigating yet, and why we're accepting
  the risk for now. Include a ticket or follow-up RFC link.
