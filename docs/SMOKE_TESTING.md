# Smoke Testing

Smoke tests prove "a deploy works end-to-end through public APIs from a real client's point of view." They catch integration bugs that unit and integration tests miss.

If you're writing one, or running one against a new environment, start here.

---

## The scripts we have

| Script | Purpose | Run against |
|--------|---------|-------------|
| `scripts/smoke_live.py` | Full multi-user chat + media (no direct DB). The one you should run after every deploy. | Any host — pass `--base <url>`. Defaults to the AWS URL. |
| `scripts/smoke_multi_user_chat.py` | 3 users, REST send + WS receive. Uses direct DB seeding to bypass the invite flow. | Local uvicorn on `:8765` only. |
| `scripts/smoke_chat_emulation.py` | 3 users with realistic typing patterns + emojis. | Local `:8765`. |
| `scripts/smoke_chat_scenarios.py` | Topic isolation + 5-parallel-pair chats. | Local `:8765`. |
| `scripts/fake_peer.py` | Single user sending messages on an interval. Used for Flutter real-time dev. | Any host via `--base`. |

**What to run after a production deploy:** `smoke_live.py`. Takes ~30 seconds, covers auth / groups / chat REST / chat WS / REST-to-WS fanout / tenant isolation / history.

---

## The four anti-patterns we've actually hit

Each of these let a real bug through. If your smoke has any of these shapes, fix it before trusting the green tick.

### 1. Happy path split across phases hides integration bugs

**How we got bitten:** `POST /messages` persisted to DB but didn't broadcast over WebSocket. My smoke did `POST /messages ×5` in one phase, then opened WS listeners in the next phase, then did `ws.send ×5` and asserted receivers got 5 frames. "5 received" was the 5 WS-native sends — the 5 REST sends never triggered a broadcast but were already in DB, so the GET-history check passed too. Bug invisible.

**The fix:** write the smoke so the **listener is attached before the trigger** and both are in the same phase:

```python
# attach listener first
listener_task = asyncio.create_task(collect(ws_receiver, received))

# now fire the trigger you want to validate
await http_post("/messages", body={"content": "probe"})

# assert the listener saw the effect within a reasonable window
await asyncio.sleep(1.5)
assert "probe" in [m["content"] for m in received]
```

If you're testing "REST send triggers WS broadcast," the REST send and the WS listener **must be alive at the same time**, and the assertion must specifically check for the REST-triggered frame (not a count that a different code path could satisfy).

### 2. Counts lie; identity doesn't

`len(received) == expected_count` is the weakest possible assertion. Any code path that produces the expected count satisfies it.

**Weak:**
```python
assert len(received) == 5
```

**Strong:**
```python
sent_ids = {m["id"] for m in sent_messages}
received_ids = {m["id"] for m in received}
assert sent_ids == received_ids, f"missing: {sent_ids - received_ids}"
```

Or cheaper: give each probe a unique content string and assert that *specific* string appeared on the receiver. That's what `smoke_live.py` now does with `REST-post #1 @ {stamp}` — each message identifies itself.

### 3. Sequential sends mask concurrent bugs

Sending 5 messages one at a time, waiting for each echo, tests one worker's happy path. It does NOT catch:
- Race conditions in Postgres fsync queue
- Lost updates from concurrent writes to the same row
- WebSocket frames arriving out of order
- Rate limiting that only triggers at burst

**Add a concurrent burst** to any smoke that sends more than a handful of messages:

```python
await asyncio.gather(*(sender.send(...) for _ in range(30)))
```

Measure real round-trip latency (send timestamp → server echo timestamp), not `time.time() - before_await_send` (which is just local buffer push).

### 4. Latency measurement bugs

```python
# WRONG — measures time to write to TCP buffer, not round-trip
t0 = time.time()
await ws.send(...)
latency = time.time() - t0        # ~0.5 ms, meaningless
```

```python
# RIGHT — measures server-processing round-trip
t_send[content] = time.time()
await ws.send(...)                # fire
frame = await ws.recv()           # wait for echo
t_echo = time.time()
latency = (t_echo - t_send[frame["message"]["content"]]) * 1000
```

If you see sub-millisecond p50 across a network, you've measured the wrong thing.

---

## Writing a new smoke — checklist

Before you commit, your script must:

- [ ] Use **only public APIs** (no `SessionLocal`, no direct Postgres — smoke is "from the client's shoes")
- [ ] Accept a `--base <url>` argument (don't hardcode dev/prod URLs)
- [ ] Use unique content strings per probe so assertions check identity, not just count
- [ ] Attach listeners **before** firing triggers; assert within a bounded timeout (≤5s)
- [ ] Include at least one **concurrent** burst if the code path has any shared state
- [ ] Measure latency as server-processing round-trip, not local-buffer-push
- [ ] Clean up (close WS connections, cancel tasks) — no hanging sockets on the server
- [ ] Print pass/fail at the end with enough detail to diagnose without re-running
- [ ] Return exit code 0 on pass, non-zero on fail (so CI can gate on it)

If you can't tick all those, the smoke will eventually pass while something real is broken. We've seen this happen — be paranoid.

---

## Running smoke_live.py

```bash
# Against AWS (the default)
.venv/bin/python scripts/smoke_live.py

# Against local uvicorn
.venv/bin/python scripts/smoke_live.py --base http://localhost:8000

# Against your own ngrok / staging
.venv/bin/python scripts/smoke_live.py --base https://your-host.example.com
```

**Expected output on success:**
```
++ SMOKE PASSED  REST p50=435ms  WS p50=123ms
```

**If any line starts with `!`**, don't ignore it. Either the backend is broken or the smoke needs updating — both cases need fixing before the deploy is "done."

---

## When to run which

| Change | Run |
|--------|-----|
| Code push to `main` | Full `smoke_live.py` against AWS |
| Backend restart | `curl /health` first, then `smoke_live.py` |
| Migration | `smoke_live.py` + check `docker compose logs app` for schema errors |
| Flutter integration testing | `fake_peer.py` in one terminal while Flutter runs |
| Pre-merge verification | Local uvicorn + `smoke_multi_user_chat.py` + Flutter dev app |

---

## Known regressions that smokes caught

Kept as a breadcrumb trail for what "good smokes catch real things" looks like:

| Date | Bug | Surfaced by |
|------|-----|-------------|
| 2026-04-20 | `POST /messages` didn't broadcast over WS | Flutter team's integration smoke (my own smoke missed it — see anti-pattern 1) |
| 2026-04-19 | boto3 defaulted to generic S3 endpoint → 307 on regional bucket | First media upload test (manual) |
| 2026-04-19 | `--workers 2` split WS connection registry → fanout broken | Two-user WS round-trip test post-deploy |
| 2026-04-19 | `migrations/001_init_schema.sql` was stale; alembic had 7 migrations | Seed script `actor_id` column missing |

Every row is a case where a deploy would have landed broken if we trusted unit/integration tests alone.
