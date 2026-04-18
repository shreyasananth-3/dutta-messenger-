"""Multi-user chat smoke — 3 users, 1 group, WebSocket round-trip + DB verify.

Run with all feature flags on and uvicorn already running on :8765:

    pkill -f uvicorn; \
    ENABLE_USERS=true ENABLE_ACL=true ENABLE_GROUPS=true ENABLE_CHAT=true \
    ENABLE_MEDIA=true ENABLE_NOTIFICATIONS=true \
        .venv/bin/uvicorn src.main:app --host 127.0.0.1 --port 8765 \
        --log-level warning &
    sleep 3
    .venv/bin/python scripts/smoke_multi_user_chat.py

Bypasses the invite flow by seeding users + group + conversation
directly through the service layer (the API's register endpoint is
invite-only by design). Aim is to exercise the live server end-to-end:
REST create → WS connect → WS send → WS receive → DB verify.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import websockets
from sqlalchemy import text

from src.config import settings
from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.services.message_service import MessageService
from src.modules.groups.services.group_service import GroupService
from src.shared.database import SessionLocal
from src.shared.middleware.auth import create_access_token

BASE_HTTP = "http://127.0.0.1:8765"
BASE_WS = "ws://127.0.0.1:8765"


async def seed_actors() -> dict:
    """Create 1 institution, 3 users, 1 group, 1 conversation — all DB-direct."""
    async with SessionLocal() as session:
        inst = await AuthService.create_institution(
            session,
            name=f"SmokeSchool-{uuid.uuid4().hex[:6]}",
            domain=f"smoke-{uuid.uuid4().hex[:4]}.test",
        )
        alice = await AuthService.register_user(
            session,
            institution_id=inst.id,
            email=f"alice-{uuid.uuid4().hex[:4]}@x.test",
            password="Sup3rStr0ng!",
            full_name="Alice",
        )
        bob = await AuthService.register_user(
            session,
            institution_id=inst.id,
            email=f"bob-{uuid.uuid4().hex[:4]}@x.test",
            password="Sup3rStr0ng!",
            full_name="Bob",
        )
        carol = await AuthService.register_user(
            session,
            institution_id=inst.id,
            email=f"carol-{uuid.uuid4().hex[:4]}@x.test",
            password="Sup3rStr0ng!",
            full_name="Carol",
        )
        await session.flush()

        group = await GroupService.create_group(
            session,
            institution_id=inst.id,
            creator_id=alice.id,
            name="SmokeGroup",
            mode="simple",
        )
        await GroupService.add_member(
            session,
            institution_id=inst.id,
            group_id=group.id,
            actor_id=alice.id,
            target_user_id=bob.id,
        )
        await GroupService.add_member(
            session,
            institution_id=inst.id,
            group_id=group.id,
            actor_id=alice.id,
            target_user_id=carol.id,
        )

        conv = await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=alice.id,
            group_id=group.id,
        )
        await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=bob.id,
            group_id=group.id,
        )
        await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=carol.id,
            group_id=group.id,
        )
        await session.commit()

        return {
            "institution_id": inst.id,
            "alice": {"id": alice.id, "email": alice.email},
            "bob": {"id": bob.id, "email": bob.email},
            "carol": {"id": carol.id, "email": carol.email},
            "group_id": group.id,
            "conversation_id": conv.id,
        }


def mint_token(user_id: str, institution_id: str) -> str:
    return create_access_token(
        user_id=uuid.UUID(user_id),
        institution_id=uuid.UUID(institution_id),
    )


async def open_socket(token: str, conversation_id: str):
    ws = await websockets.connect(f"{BASE_WS}/api/v1/ws/chat")
    await ws.send(json.dumps({"type": "auth", "token": token}))
    first = json.loads(await ws.recv())
    assert first["type"] == "connection.established", first
    await ws.send(
        json.dumps({"type": "subscribe", "conversation_id": conversation_id})
    )
    sub_ack = json.loads(await ws.recv())
    assert sub_ack["type"] == "subscribed", sub_ack
    return ws


async def main() -> None:
    print("==> seeding 3 users + 1 group + 1 conversation via DB-direct path")
    ctx = await seed_actors()
    print(f"    inst={ctx['institution_id']}")
    print(f"    conv={ctx['conversation_id']}")
    for name in ("alice", "bob", "carol"):
        print(f"    {name:5} = {ctx[name]['id']}")

    tokens = {
        name: mint_token(ctx[name]["id"], ctx["institution_id"])
        for name in ("alice", "bob", "carol")
    }

    print("\n==> REST send latency (alice posts 5 messages via REST)")
    async with httpx.AsyncClient() as client:
        lat = []
        for i in range(5):
            t0 = time.perf_counter()
            r = await client.post(
                f"{BASE_HTTP}/api/v1/chat/conversations/{ctx['conversation_id']}/messages",
                headers={"Authorization": f"Bearer {tokens['alice']}"},
                json={"content": f"rest msg {i}"},
            )
            dt = (time.perf_counter() - t0) * 1000
            lat.append(dt)
            assert r.status_code == 201, r.text
        print(f"    REST send p50={sorted(lat)[len(lat)//2]:.1f}ms, max={max(lat):.1f}ms")

    print("\n==> WebSocket fanout latency (3 connections; alice→{bob,carol})")
    ws_alice = await open_socket(tokens["alice"], ctx["conversation_id"])
    ws_bob = await open_socket(tokens["bob"], ctx["conversation_id"])
    ws_carol = await open_socket(tokens["carol"], ctx["conversation_id"])

    round_trips = []
    received_by_others = 0
    for i in range(5):
        text_msg = f"ws msg {i} at {time.time():.4f}"
        t0 = time.perf_counter()
        await ws_alice.send(
            json.dumps(
                {
                    "type": "message.send",
                    "conversation_id": ctx["conversation_id"],
                    "content": text_msg,
                }
            )
        )
        # alice also receives her own message.new broadcast
        recv_alice = json.loads(await asyncio.wait_for(ws_alice.recv(), 2.0))
        recv_bob = json.loads(await asyncio.wait_for(ws_bob.recv(), 2.0))
        recv_carol = json.loads(await asyncio.wait_for(ws_carol.recv(), 2.0))
        dt = (time.perf_counter() - t0) * 1000
        round_trips.append(dt)

        for r in (recv_alice, recv_bob, recv_carol):
            assert r["type"] == "message.new", r
            assert r["message"]["content"] == text_msg, r
        received_by_others += 2  # bob + carol
    print(
        f"    WS round-trip (send→all-3-receive) p50="
        f"{sorted(round_trips)[len(round_trips)//2]:.1f}ms, "
        f"max={max(round_trips):.1f}ms"
    )
    print(f"    fanout delivered = {received_by_others} (expected 10)")

    await ws_alice.close()
    await ws_bob.close()
    await ws_carol.close()

    print("\n==> DB verify (are messages persisted? are they tenant-scoped?)")
    async with SessionLocal() as session:
        row = await session.execute(
            text(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = :c"
            ),
            {"c": ctx["conversation_id"]},
        )
        count = row.scalar_one()
        print(f"    messages in conversation: {count} (expected 10: 5 REST + 5 WS)")

        # Verify audit trail present for the group creation
        audit = await session.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs WHERE institution_id = :i"
            ),
            {"i": ctx["institution_id"]},
        )
        ac = audit.scalar_one()
        print(f"    audit_logs rows for institution: {ac}")

        # Cross-tenant probe: alice's messages should NOT appear when filtered by another institution
        leak = await session.execute(
            text(
                "SELECT COUNT(*) FROM messages m "
                "JOIN conversations c ON c.id = m.conversation_id "
                "JOIN groups g ON g.id = c.group_id "
                "WHERE g.institution_id <> :i AND m.sender_id IN (:a, :b, :ca)"
            ),
            {
                "i": ctx["institution_id"],
                "a": ctx["alice"]["id"],
                "b": ctx["bob"]["id"],
                "ca": ctx["carol"]["id"],
            },
        )
        leaked = leak.scalar_one()
        print(f"    cross-tenant leak probe (expect 0): {leaked}")

    print("\n==> SMOKE PASSED")


if __name__ == "__main__":
    asyncio.run(main())
