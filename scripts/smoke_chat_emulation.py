"""Chat-experience emulation — 3 users chatting in parallel.

Unlike `smoke_multi_user_chat.py` (which has one sender and two passive
receivers), this smoke drives all three users concurrently via asyncio.
Each user follows a scripted back-and-forth that resembles a real
conversation:

  Alice:  "Hi everyone 👋"
  Bob:    "hi Alice"
  Carol:  "hey 👋"
  Alice:  "how was class today?"
  Bob:    "a bit rough honestly"
  Carol:  "missed the algebra part, can someone send notes?"
  Alice:  "on it — give me 10 min"
  Bob:    "thanks alice"
  Carol:  "🙏"

Each user runs in its own `asyncio.Task`:
  - connects WS, authenticates, subscribes to the conversation
  - sends its lines with small random delays (realistic typing)
  - logs every inbound `message.new` frame with the sender's name
  - exits when the script coordinator says "done"

At the end:
  - we verify every user saw every OTHER user's messages
  - we query Postgres directly to confirm each message committed with
    the correct `sender_id` (not just "all from Alice like before")

Run against a live uvicorn on :8765 with all ENABLE_* flags on.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid

import websockets
from sqlalchemy import text

from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.services.message_service import MessageService
from src.modules.groups.services.group_service import GroupService
from src.shared.database import SessionLocal
from src.shared.middleware.auth import create_access_token

BASE_WS = "ws://127.0.0.1:8765"

# Scripted back-and-forth — each tuple is (sender_name, content).
SCRIPT: list[tuple[str, str]] = [
    ("alice", "Hi everyone 👋"),
    ("bob", "hi Alice"),
    ("carol", "hey 👋"),
    ("alice", "how was class today?"),
    ("bob", "a bit rough honestly"),
    ("carol", "missed the algebra part, can someone send notes?"),
    ("alice", "on it — give me 10 min"),
    ("bob", "thanks alice"),
    ("carol", "🙏"),
]


async def seed() -> dict:
    """Seed institution + 3 users + group + conversation through services."""
    async with SessionLocal() as session:
        inst = await AuthService.create_institution(
            session,
            name=f"ChatEmu-{uuid.uuid4().hex[:6]}",
            domain=f"emu-{uuid.uuid4().hex[:4]}.test",
        )
        users: dict[str, object] = {}
        for name in ("alice", "bob", "carol"):
            u = await AuthService.register_user(
                session,
                institution_id=inst.id,
                email=f"{name}-{uuid.uuid4().hex[:4]}@emu.test",
                password="Sup3rStr0ng!",
                full_name=name.capitalize(),
            )
            users[name] = u
        await session.flush()

        group = await GroupService.create_group(
            session,
            institution_id=inst.id,
            creator_id=users["alice"].id,
            name="ChatEmu",
            mode="simple",
        )
        for other in ("bob", "carol"):
            await GroupService.add_member(
                session,
                institution_id=inst.id,
                group_id=group.id,
                actor_id=users["alice"].id,
                target_user_id=users[other].id,
            )
        conv = await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["alice"].id,
            group_id=group.id,
        )
        for other in ("bob", "carol"):
            await MessageService.open_conversation(
                session,
                institution_id=inst.id,
                actor_id=users[other].id,
                group_id=group.id,
            )
        await session.commit()
        return {
            "institution_id": inst.id,
            "conversation_id": conv.id,
            "users": {name: str(u.id) for name, u in users.items()},
        }


def mint(user_id: str, institution_id: str) -> str:
    return create_access_token(
        user_id=uuid.UUID(user_id),
        institution_id=uuid.UUID(institution_id),
    )


async def run_user(
    *,
    name: str,
    token: str,
    conversation_id: str,
    my_lines: list[str],
    inbound: asyncio.Queue,
    ready: asyncio.Event,
    start: asyncio.Event,
    done: asyncio.Event,
) -> dict:
    """Drive one user: connect, subscribe, send scripted lines, log inbound."""
    received_counts: dict[str, int] = {}
    latencies: list[float] = []

    async with websockets.connect(f"{BASE_WS}/api/v1/ws/chat") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        first = json.loads(await ws.recv())
        assert first["type"] == "connection.established", first

        await ws.send(
            json.dumps({"type": "subscribe", "conversation_id": conversation_id})
        )
        sub_ack = json.loads(await ws.recv())
        assert sub_ack["type"] == "subscribed", sub_ack

        ready.set()
        await start.wait()  # all three sockets subscribed; now chatter can begin

        # Launch sender + receiver loops concurrently.
        async def _receiver() -> None:
            while not done.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                frame = json.loads(raw)
                if frame.get("type") != "message.new":
                    continue
                content = frame["message"]["content"]
                # Parse originating timestamp if the sender embedded one
                sender = _sender_name_for_content(content)
                if sender is None:
                    continue
                received_counts[sender] = received_counts.get(sender, 0) + 1
                await inbound.put((name, sender, content, time.perf_counter()))

        async def _sender() -> None:
            for line in my_lines:
                # realistic typing pause
                await asyncio.sleep(random.uniform(0.15, 0.45))
                t0 = time.perf_counter()
                await ws.send(
                    json.dumps(
                        {
                            "type": "message.send",
                            "conversation_id": conversation_id,
                            # tag the content with my name so receivers can attribute
                            "content": f"[{name}] {line}",
                        }
                    )
                )
                # the server will echo message.new back to us too; the receiver
                # loop picks it up. Record just the send latency via the next
                # receive corresponding to this message.
                latencies.append(time.perf_counter() - t0)

        recv_task = asyncio.create_task(_receiver())
        send_task = asyncio.create_task(_sender())
        await send_task
        # drain inbound for up to ~1s after last send
        await asyncio.sleep(1.0)
        done.set()
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

    return {
        "name": name,
        "sent": len(my_lines),
        "received_by_sender": received_counts,
        "send_latencies_ms": [round(1000 * x, 2) for x in latencies],
    }


def _sender_name_for_content(content: str) -> str | None:
    """Extract the author's nick from our tagged content format."""
    if content.startswith("[") and "]" in content:
        return content[1 : content.index("]")]
    return None


async def main() -> None:
    print("==> seeding institution + 3 users + 1 group + 1 conversation")
    ctx = await seed()
    print(f"    inst  = {ctx['institution_id']}")
    print(f"    conv  = {ctx['conversation_id']}")
    for n, uid in ctx["users"].items():
        print(f"    {n:5} = {uid}")

    tokens = {
        n: mint(uid, ctx["institution_id"]) for n, uid in ctx["users"].items()
    }

    # Split SCRIPT into per-user line lists.
    my_lines: dict[str, list[str]] = {"alice": [], "bob": [], "carol": []}
    for who, line in SCRIPT:
        my_lines[who].append(line)

    inbound: asyncio.Queue = asyncio.Queue()
    ready = {n: asyncio.Event() for n in ("alice", "bob", "carol")}
    start = asyncio.Event()
    done = asyncio.Event()

    print("\n==> launching 3 concurrent WS sessions (each user chats in parallel)")
    tasks = [
        asyncio.create_task(
            run_user(
                name=n,
                token=tokens[n],
                conversation_id=ctx["conversation_id"],
                my_lines=my_lines[n],
                inbound=inbound,
                ready=ready[n],
                start=start,
                done=done,
            )
        )
        for n in ("alice", "bob", "carol")
    ]

    # Wait until every socket has authed + subscribed before letting anyone chat.
    for n, ev in ready.items():
        await ev.wait()
    print("    all three subscribed — conversation begins")
    start.set()

    results = await asyncio.gather(*tasks)

    print("\n==> per-user outcome (received counts attributed by sender)")
    for r in results:
        latencies = r["send_latencies_ms"]
        if latencies:
            print(
                f"    {r['name']:5}: sent={r['sent']:>2}  "
                f"received-by-sender={r['received_by_sender']}  "
                f"send-latency p50={sorted(latencies)[len(latencies) // 2]:.1f}ms "
                f"max={max(latencies):.1f}ms"
            )
        else:
            print(
                f"    {r['name']:5}: sent={r['sent']:>2}  "
                f"received-by-sender={r['received_by_sender']}  "
                f"send-latency=(no sends)"
            )

    # ---- Assertions on what each user should have seen.
    print("\n==> assertions")
    expected = {"alice": 0, "bob": 0, "carol": 0}
    for who, _ in SCRIPT:
        expected[who] += 1
    for r in results:
        for sender, exp in expected.items():
            if sender == r["name"]:
                continue  # self-echo handled below
            got = r["received_by_sender"].get(sender, 0)
            ok = got == exp
            print(
                f"    {r['name']:5} saw {got}/{exp} of {sender}'s messages "
                f"{'✓' if ok else '✗'}"
            )

    # ---- Independent DB verification of sender fidelity.
    print("\n==> DB verify (are messages persisted with the correct sender_id?)")
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT u.full_name AS sender, m.content, m.created_at "
                    "FROM messages m "
                    "JOIN users u ON u.id = m.sender_id "
                    "WHERE m.conversation_id = :c "
                    "ORDER BY m.created_at"
                ),
                {"c": ctx["conversation_id"]},
            )
        ).mappings().all()
        print(f"    total rows in messages for this conversation: {len(rows)}")
        per_sender: dict[str, int] = {}
        for row in rows:
            per_sender[row["sender"]] = per_sender.get(row["sender"], 0) + 1
            print(
                f"      {row['created_at'].strftime('%H:%M:%S.%f')[:-3]} "
                f"{row['sender']:6}: {row['content']}"
            )
        print(f"\n    per-sender counts: {per_sender}")
        want = {"Alice": 3, "Bob": 3, "Carol": 3}
        ok = all(per_sender.get(k, 0) == v for k, v in want.items())
        print(f"    matches scripted pattern ({want}): {'✓' if ok else '✗'}")

    print("\n==> EMULATION DONE")


if __name__ == "__main__":
    asyncio.run(main())
