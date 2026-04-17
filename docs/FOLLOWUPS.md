# Follow-ups — running list of deferred work

> Append-only running list. Each module's `POST-FLIGHT` report adds a row
> here for anything that was *intentionally* skipped, so it doesn't silently
> disappear. Update `docs/NEXT_SESSION.md` with a one-line pointer whenever
> a row lands here.
>
> **Rules of the road:**
>
> - One row = one concrete follow-up with a name and a scope.
> - Group rows by the module they belong to.
> - When a row ships, DELETE it (the commit/PR is the record).
> - "Blocker" says what is stopping the work today. "Owner" is a stage
>   or module name, not a person.

---

## Media module (Stage 4e, PR #5)

Everything below is a **real-work test / hardening gap** acknowledged at
ship time. The module was merged under `ENABLE_MEDIA=false`; each
follow-up is cheap on its own but none blocked the merge.

### Test gaps — in-scope for a Stage-4e follow-up PR

| # | Gap | Where it bites | Fix size | Blocker |
|---|-----|----------------|----------|---------|
| M1 | **Audit row SELECT assertion** — tests assert the service method is called; they don't `SELECT * FROM audit_logs WHERE resource_id=...` to prove the row committed. `write_audit` swallows errors by design, so a bad JSONB insert would be silent. | All three mutating tests (complete, recycle-bin). Catches silent-audit regressions forever. | ~30 LoC total | None |
| M2 | **`extra="forbid"` boundary** — request models set `ConfigDict(extra="forbid")` but no test sends a spurious field to prove it bites. | `POST /upload/init`, `POST /upload/complete` | ~10 LoC | None |
| M3 | **Max-length filename** — Pydantic `max_length=255` on `file_name`; no test sends 256 chars. CLAUDE.md POST-FLIGHT §C explicitly lists this. | `POST /upload/init` | ~5 LoC | None |
| M4 | **Full-chain migration from an empty DB** — only the `up → down → up` round-trip was exercised. A clean-slate `alembic upgrade head` and a `\d media_files` diff vs `SCHEMA.sql` is stronger. | Migration `0005_media_module_schema.py` | small bash script | None |
| M5 | **Live MinIO smoke recipe** — parallel to `docs/MANUAL_SMOKE.md` for auth: init → actual PUT bytes to MinIO → complete → download URL → DELETE. Proves the boto3 + MinIO path end-to-end; catches signing-algorithm bugs that mocks can't see. | All five endpoints | ~1 page recipe doc + 5 min to run | `docker compose up minio` or Homebrew MinIO; `.env` already wired |

### Hardening — RFC compliance

| # | Gap | RFC reference | Fix size | Blocker |
|---|-----|---------------|----------|---------|
| M6 | **Postgres RLS on `media_files`** — `docs/design/tenant-isolation.md` §1.2 lists every `institution_id`-bearing table and mandates an RLS policy as defence-in-depth. The users migration applied it; mine didn't. App-layer `tenant_scoped_query` works (proven by cross-tenant tests); a raw SQL bypass would **not** be blocked today. | tenant-isolation.md §1.3 | ~6 SQL lines in a new Alembic migration | None |
| M7 | **MIME inspection at `complete`** — MODULE.md §Security #3 wants "MIME type validation on init (declared type) and on complete (actual file inspection)". My `complete` only does S3 HEAD, which relies on the presigned URL's locked `Content-Type`. A client with a leaked presigned URL could still upload content whose first bytes don't match the declared MIME. Mitigated by allow-list + extension block, not eliminated. | MODULE.md §Security #3 | magic-byte sniff on a small `GetObject` range, ~20 LoC | Small; needs a `get_object_range(key, 0..1024)` helper in `src/shared/storage.py` |

### Cross-cutting — owned by shared, not media

| # | Gap | Where it bites | Blocker |
|---|-----|----------------|---------|
| M8 | **Redis race on duplicate `Idempotency-Key`** — `src/shared/middleware/idempotency.py` uses plain `SET` (no `NX`) in `store_idempotency`. Two concurrent requests with the same key can both MISS and both write. The 409 collision path fires on *sequential* replay, not concurrent. | All idempotent POST routes (media + chat + groups + auth.invite) | **Out of scope for media.** Needs `SET key value NX EX ttl` + lock-then-compare in the shared helper. Flag to lead session when chat lands. |
| M9 | **`moto` in media tests** — `src/shared/storage.py`'s own tests use `moto`; the media tests reach straight for monkeypatch stubs instead. Real `moto` would exercise actual signature construction. | Any signing-algorithm or region mismatch | Media tests could be ported to `moto` (requires the `moto[s3]` extra, which is already in `pyproject.toml` dev deps) |

### Deferred by design (already in `src/modules/media/docs/API.md` §Deferred)

Not gaps — planned follow-ups in later stages:

- **Admin mass-delete** → Stage 4b (ACL).
- **Thumbnail Celery task** → after chat module lands (shared Celery workers).
- **Recycle-bin nightly sweep** → Celery beat task; 30-day SLA per
  `docs/design/privacy-erasure.md`.
- **Virus scanning** → SLO budget (upload p95 < 3000 ms per
  `docs/design/slo.md`) needs re-sizing first.
- **`GET /media?uploader_id=...` listing** → index exists, endpoint deferred.

---

## Other modules

*(empty — add rows as each stage POST-FLIGHTs.)*
