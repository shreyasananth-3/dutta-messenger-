"""End-to-end smoke against a running DuttaMessenger instance.

Public API only — same path a Flutter client takes. No direct DB.

Default target is the AWS prod URL (override via --base). Verifies:
  1. Admin login → invite 3 users → each registers with own password + logs in
  2. Admin creates group, adds all 3 users as members
  3. Every user calls /chat/conversations/open-group (idempotent — returns same
     conversation_id, adds caller as conversation member)
  4. 5 REST sends: measure p50/max latency
  5. 5 WebSocket sends: measure p50/max round-trip
  6. Bob + Carol each receive all 5 WS messages pushed by Alice
  7. GET history contains all 10 messages (5 REST + 5 WS)
  8. Isolation: unauth = 401, non-member = 404/403

Usage:
    .venv/bin/python scripts/smoke_live.py
    .venv/bin/python scripts/smoke_live.py --base https://other.example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

import websockets

DEFAULT_BASE = "https://dattamessenger.duckdns.org"


def call(base: str, method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    """Make a JSON HTTP request. Returns (status_code, parsed_body)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.getcode(), json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {}


async def run(base: str) -> int:
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/v1/ws/chat"
    stamp = str(int(time.time()))
    print(f"=== smoke_live against {base} ({stamp}) ===\n")

    _, r = call(base, "POST", "/api/v1/auth/login", body={"email": "admin@demo.school", "password": "DemoPass123!"})
    admin_tok = r["data"]["access_token"]

    users = [("Alice", "AlicePass123!"), ("Bob", "BobPass123!"), ("Carol", "CarolPass123!")]
    tokens: dict[str, tuple[str, str]] = {}
    for name, pw in users:
        email = f"smoke.{name.lower()}.{stamp}@example.com"
        _, r = call(base, "POST", "/api/v1/auth/invite", admin_tok, {"email": email})
        invite_tok = r["data"]["invitation"]["token"]
        _, r = call(base, "POST", "/api/v1/auth/register", body={"invitation_token": invite_tok, "email": email, "password": pw, "full_name": name})
        uid = r["data"]["user"]["id"]
        _, r = call(base, "POST", "/api/v1/auth/login", body={"email": email, "password": pw})
        tokens[name] = (r["data"]["access_token"], uid)
    print("  + 3 users registered and logged in")

    _, r = call(base, "POST", "/api/v1/groups", admin_tok, {"name": f"SmokeGroup-{stamp}", "description": "", "mode": "simple"})
    gid = r["data"]["id"]
    for name in ("Alice", "Bob", "Carol"):
        call(base, "POST", f"/api/v1/groups/{gid}/members", admin_tok, {"user_id": tokens[name][1], "role": "member"})
    print(f"  + all 3 added to group {gid[:8]}")

    cids: set[str] = set()
    for name in ("Alice", "Bob", "Carol"):
        code, r = call(base, "POST", "/api/v1/chat/conversations/open-group", tokens[name][0], {"group_id": gid})
        if code != 200:
            print(f"{name} open-group: {code} {r}")
            return 1
        cids.add(r["data"]["id"])
    if len(cids) != 1:
        print(f"  ! open-group not idempotent: {cids}")
        return 1
    cid = cids.pop()
    print(f"  + all 3 called open-group (idempotent, cid {cid[:8]})")

    rest_lat: list[float] = []
    for i in range(5):
        t0 = time.time()
        code, r = call(base, "POST", f"/api/v1/chat/conversations/{cid}/messages", tokens["Alice"][0], {"content": f"REST #{i+1} @ {stamp}"})
        rest_lat.append((time.time() - t0) * 1000)
        if code >= 300:
            print(f"  ! REST send failed: {code} {r}")
            return 1
    print(f"  + 5 REST messages  p50={statistics.median(rest_lat):.0f}ms  max={max(rest_lat):.0f}ms")

    async def ws_open(tok: str) -> websockets.WebSocketClientProtocol:
        w = await websockets.connect(ws_url, open_timeout=10)
        await w.send(json.dumps({"type": "auth", "token": tok}))
        est = json.loads(await w.recv())
        if est.get("type") != "connection.established":
            raise RuntimeError(f"bad WS handshake: {est}")
        await w.send(json.dumps({"type": "subscribe", "conversation_id": cid}))
        return w

    wa, wb, wc = await asyncio.gather(ws_open(tokens["Alice"][0]), ws_open(tokens["Bob"][0]), ws_open(tokens["Carol"][0]))
    await asyncio.sleep(0.3)
    for w in (wa, wb, wc):
        try:
            while True:
                await asyncio.wait_for(w.recv(), timeout=0.1)
        except Exception:
            pass
    print("  + 3 WS connections authed + subscribed")

    recv_b: list[str] = []
    recv_c: list[str] = []

    async def collect(w: websockets.WebSocketClientProtocol, target: list[str]) -> None:
        try:
            while True:
                raw = await asyncio.wait_for(w.recv(), timeout=3.0)
                frame = json.loads(raw)
                if frame.get("type") == "message.new":
                    target.append(frame["message"]["content"])
        except asyncio.TimeoutError:
            pass

    t_b = asyncio.create_task(collect(wb, recv_b))
    t_c = asyncio.create_task(collect(wc, recv_c))

    ws_lat: list[float] = []
    for i in range(5):
        t0 = time.time()
        await wa.send(json.dumps({"type": "message.send", "conversation_id": cid, "content": f"WS #{i+1} @ {stamp}"}))
        await asyncio.wait_for(wa.recv(), timeout=3.0)
        ws_lat.append((time.time() - t0) * 1000)

    await asyncio.sleep(1.5)
    t_b.cancel()
    t_c.cancel()
    for t in (t_b, t_c):
        try:
            await t
        except Exception:
            pass
    for w in (wa, wb, wc):
        await w.close()

    print(f"  + 5 WS messages    p50={statistics.median(ws_lat):.0f}ms  max={max(ws_lat):.0f}ms")
    print(f"  {'+' if len(recv_b)==5 else '!'} Bob received {len(recv_b)}/5 WS messages")
    print(f"  {'+' if len(recv_c)==5 else '!'} Carol received {len(recv_c)}/5 WS messages")

    code, r = call(base, "GET", f"/api/v1/chat/conversations/{cid}/messages?limit=50", tokens["Bob"][0])
    msgs = r.get("data", [])
    print(f"  {'+' if len(msgs)==10 else '!'} Bob GET history: {len(msgs)}/10 messages")

    code, _ = call(base, "GET", f"/api/v1/chat/conversations/{cid}/messages")
    print(f"  {'+' if code==401 else '!'} no-auth GET blocked ({code})")

    code, _ = call(base, "GET", f"/api/v1/chat/conversations/{cid}/messages", admin_tok)
    print(f"  {'+' if code in (403,404) else '!'} non-member blocked ({code})")

    passed = len(recv_b) == 5 and len(recv_c) == 5 and len(msgs) == 10 and code in (403, 404)
    if passed:
        print(f"\n++ SMOKE PASSED  REST p50={statistics.median(rest_lat):.0f}ms  WS p50={statistics.median(ws_lat):.0f}ms")
        return 0
    print("\n!! SMOKE FAILED")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"Backend base URL (default: {DEFAULT_BASE})")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    return asyncio.run(run(base))


if __name__ == "__main__":
    sys.exit(main())
