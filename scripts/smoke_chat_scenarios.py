"""Two realistic chat emulation scenarios, run back-to-back.

Scenario A — **Topic-mode isolation** (one group, three topics).
  - 4 users in "Class 9B" (mode=topics).
  - Alice + Bob subscribe ONLY to the "algebra" topic conversation.
  - Carol + Dave subscribe ONLY to the "homework" topic conversation.
  - Alice posts in algebra; assert Bob sees it and Carol/Dave do NOT.
  - Carol posts in homework; assert Dave sees it and Alice/Bob do NOT.
  - DB verify: messages are tagged with the correct conversation_id and
    each conversation has the correct topic_id.

Scenario B — **5 parallel 2-person conversations** (10 users, 5 groups).
  - 10 users, paired into 5 independent 2-person groups.
  - All 5 pairs chat simultaneously via 10 concurrent WebSockets.
  - Each pair exchanges 4 messages (back-and-forth).
  - Cross-pair isolation: pair A's messages must NEVER appear in pair B.
  - DB verify: per-conversation counts match, total = 5 × 4 = 20.

Run against a live uvicorn on :8765 with all ENABLE_* flags on.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field

import websockets
from sqlalchemy import select, text

from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.services.message_service import MessageService
from src.modules.groups.models.db_models import Topic
from src.modules.groups.services.group_service import GroupService
from src.shared.database import SessionLocal
from src.shared.middleware.auth import create_access_token

BASE_WS = "ws://127.0.0.1:8765"


def mint(user_id: str, institution_id: str) -> str:
    return create_access_token(
        user_id=uuid.UUID(user_id),
        institution_id=uuid.UUID(institution_id),
    )


@dataclass
class UserOutcome:
    name: str
    sent: int = 0
    received: list[tuple[str, str]] = field(default_factory=list)  # (sender_tag, content)
    latencies_ms: list[float] = field(default_factory=list)


async def run_user(
    *,
    name: str,
    token: str,
    conversation_id: str,
    my_lines: list[str],
    ready: asyncio.Event,
    start: asyncio.Event,
    stop_after_sec: float = 1.0,
) -> UserOutcome:
    """Connect one WS, subscribe to ONE conversation, send scripted lines,
    log every inbound `message.new` with the tagged sender name."""
    outcome = UserOutcome(name=name)

    async with websockets.connect(f"{BASE_WS}/api/v1/ws/chat") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        established = json.loads(await ws.recv())
        assert established["type"] == "connection.established", established

        await ws.send(
            json.dumps({"type": "subscribe", "conversation_id": conversation_id})
        )
        sub_ack = json.loads(await ws.recv())
        assert sub_ack["type"] == "subscribed", sub_ack

        ready.set()
        await start.wait()

        done = asyncio.Event()

        async def _receiver() -> None:
            while not done.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                frame = json.loads(raw)
                if frame.get("type") != "message.new":
                    continue
                content = frame["message"]["content"]
                sender_tag = _sender_tag(content)
                if sender_tag is not None:
                    outcome.received.append((sender_tag, content))

        async def _sender() -> None:
            for line in my_lines:
                await asyncio.sleep(random.uniform(0.10, 0.35))
                t0 = time.perf_counter()
                await ws.send(
                    json.dumps(
                        {
                            "type": "message.send",
                            "conversation_id": conversation_id,
                            "content": f"[{name}] {line}",
                        }
                    )
                )
                outcome.latencies_ms.append(1000 * (time.perf_counter() - t0))
                outcome.sent += 1

        recv_task = asyncio.create_task(_receiver())
        send_task = asyncio.create_task(_sender())
        await send_task
        await asyncio.sleep(stop_after_sec)
        done.set()
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

    return outcome


def _sender_tag(content: str) -> str | None:
    if content.startswith("[") and "]" in content:
        return content[1 : content.index("]")]
    return None


# ---------------------------------------------------------------------------
# Scenario A — Topic-mode isolation
# ---------------------------------------------------------------------------


async def scenario_a_topics() -> None:
    print("\n" + "=" * 60)
    print("SCENARIO A — topic-mode isolation (1 group, 2 active topics)")
    print("=" * 60)

    async with SessionLocal() as session:
        inst = await AuthService.create_institution(
            session, name=f"TopicEmu-{uuid.uuid4().hex[:6]}", domain=f"t-{uuid.uuid4().hex[:4]}.test"
        )
        users = {}
        for n in ("alice", "bob", "carol", "dave"):
            users[n] = await AuthService.register_user(
                session,
                institution_id=inst.id,
                email=f"{n}-{uuid.uuid4().hex[:4]}@t.test",
                password="Sup3rStr0ng!",
                full_name=n.capitalize(),
            )
        await session.flush()

        # Topics-mode group; creates "General" automatically.
        group = await GroupService.create_group(
            session,
            institution_id=inst.id,
            creator_id=users["alice"].id,
            name="Class 9B",
            mode="topics",
        )
        algebra = await GroupService.create_topic(
            session,
            institution_id=inst.id,
            group_id=group.id,
            actor_id=users["alice"].id,
            name="algebra",
            icon_emoji="➗",
        )
        homework = await GroupService.create_topic(
            session,
            institution_id=inst.id,
            group_id=group.id,
            actor_id=users["alice"].id,
            name="homework",
            icon_emoji="📝",
        )
        for n in ("bob", "carol", "dave"):
            await GroupService.add_member(
                session,
                institution_id=inst.id,
                group_id=group.id,
                actor_id=users["alice"].id,
                target_user_id=users[n].id,
            )

        # Open topic conversations for the two "subscribers of each"
        conv_algebra = await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["alice"].id,
            group_id=group.id,
            topic_id=algebra.id,
        )
        await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["bob"].id,
            group_id=group.id,
            topic_id=algebra.id,
        )
        conv_homework = await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["carol"].id,
            group_id=group.id,
            topic_id=homework.id,
        )
        await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["dave"].id,
            group_id=group.id,
            topic_id=homework.id,
        )
        await session.commit()

    print(f"    algebra  conv  = {conv_algebra.id}")
    print(f"    homework conv  = {conv_homework.id}")

    tokens = {n: mint(users[n].id, inst.id) for n in ("alice", "bob", "carol", "dave")}

    ready = {n: asyncio.Event() for n in ("alice", "bob", "carol", "dave")}
    start = asyncio.Event()

    algebra_lines_alice = ["x^2 = 9 means x = ±3", "quadratic formula recap?"]
    algebra_lines_bob = ["nice, discriminant b^2-4ac"]
    homework_lines_carol = ["homework due friday", "chapter 7 exercises 4-12"]
    homework_lines_dave = ["got it — thanks Carol"]

    tasks = [
        asyncio.create_task(
            run_user(
                name="alice",
                token=tokens["alice"],
                conversation_id=str(conv_algebra.id),
                my_lines=algebra_lines_alice,
                ready=ready["alice"],
                start=start,
            )
        ),
        asyncio.create_task(
            run_user(
                name="bob",
                token=tokens["bob"],
                conversation_id=str(conv_algebra.id),
                my_lines=algebra_lines_bob,
                ready=ready["bob"],
                start=start,
            )
        ),
        asyncio.create_task(
            run_user(
                name="carol",
                token=tokens["carol"],
                conversation_id=str(conv_homework.id),
                my_lines=homework_lines_carol,
                ready=ready["carol"],
                start=start,
            )
        ),
        asyncio.create_task(
            run_user(
                name="dave",
                token=tokens["dave"],
                conversation_id=str(conv_homework.id),
                my_lines=homework_lines_dave,
                ready=ready["dave"],
                start=start,
            )
        ),
    ]

    for ev in ready.values():
        await ev.wait()
    print("    all 4 subscribed to their topic — chatter begins")
    start.set()

    results = {r.name: r for r in await asyncio.gather(*tasks)}

    print("\n  per-user inbound (sender → count)")
    for name in ("alice", "bob", "carol", "dave"):
        cnt: dict[str, int] = {}
        for sender, _ in results[name].received:
            cnt[sender] = cnt.get(sender, 0) + 1
        print(f"    {name:5} saw {cnt}")

    print("\n  topic-isolation assertions")
    # Algebra topic: {alice, bob}; Homework: {carol, dave}.
    # Algebra side should see 0 messages from carol/dave and vice versa.
    for name in ("alice", "bob"):
        bleed = sum(1 for s, _ in results[name].received if s in ("carol", "dave"))
        ok = bleed == 0
        print(f"    {name:5} saw {bleed} bleed from homework topic {'✓' if ok else '✗'}")
    for name in ("carol", "dave"):
        bleed = sum(1 for s, _ in results[name].received if s in ("alice", "bob"))
        ok = bleed == 0
        print(f"    {name:5} saw {bleed} bleed from algebra topic {'✓' if ok else '✗'}")

    print("\n  DB verify — messages are tagged with correct conversation_id")
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT c.topic_id, u.full_name AS sender, m.content "
                    "FROM messages m "
                    "JOIN conversations c ON c.id = m.conversation_id "
                    "JOIN users u ON u.id = m.sender_id "
                    "WHERE c.group_id = :g "
                    "ORDER BY m.created_at"
                ),
                {"g": group.id},
            )
        ).mappings().all()
        algebra_rows = [r for r in rows if str(r["topic_id"]) == str(algebra.id)]
        homework_rows = [r for r in rows if str(r["topic_id"]) == str(homework.id)]
        print(f"    algebra  topic: {len(algebra_rows)} messages; senders={set(r['sender'] for r in algebra_rows)}")
        print(f"    homework topic: {len(homework_rows)} messages; senders={set(r['sender'] for r in homework_rows)}")
        ok = (
            set(r["sender"] for r in algebra_rows) <= {"Alice", "Bob"}
            and set(r["sender"] for r in homework_rows) <= {"Carol", "Dave"}
        )
        print(f"    per-topic sender constraint holds: {'✓' if ok else '✗'}")


# ---------------------------------------------------------------------------
# Scenario B — 5 parallel 2-person groups
# ---------------------------------------------------------------------------


async def scenario_b_five_pairs() -> None:
    print("\n" + "=" * 60)
    print("SCENARIO B — 5 parallel 2-person conversations (10 users)")
    print("=" * 60)

    pairs = [("u1a", "u1b"), ("u2a", "u2b"), ("u3a", "u3b"), ("u4a", "u4b"), ("u5a", "u5b")]

    async with SessionLocal() as session:
        inst = await AuthService.create_institution(
            session,
            name=f"PairsEmu-{uuid.uuid4().hex[:6]}",
            domain=f"p-{uuid.uuid4().hex[:4]}.test",
        )
        users: dict[str, object] = {}
        for a, b in pairs:
            for n in (a, b):
                users[n] = await AuthService.register_user(
                    session,
                    institution_id=inst.id,
                    email=f"{n}-{uuid.uuid4().hex[:4]}@p.test",
                    password="Sup3rStr0ng!",
                    full_name=n,
                )
        await session.flush()

        # 5 groups, 2 members each, one shared conversation per group.
        conversations: dict[tuple[str, str], str] = {}
        for a, b in pairs:
            group = await GroupService.create_group(
                session,
                institution_id=inst.id,
                creator_id=users[a].id,
                name=f"Pair-{a}-{b}",
                mode="simple",
            )
            await GroupService.add_member(
                session,
                institution_id=inst.id,
                group_id=group.id,
                actor_id=users[a].id,
                target_user_id=users[b].id,
            )
            conv = await MessageService.open_conversation(
                session,
                institution_id=inst.id,
                actor_id=users[a].id,
                group_id=group.id,
            )
            await MessageService.open_conversation(
                session,
                institution_id=inst.id,
                actor_id=users[b].id,
                group_id=group.id,
            )
            conversations[(a, b)] = str(conv.id)
        await session.commit()

    print(f"    seeded 10 users across 5 pairs; each pair has its own conversation")

    tokens = {n: mint(users[n].id, inst.id) for n in users}

    # Back-and-forth script per pair — 2 messages per side = 4 per pair.
    convo_scripts = [
        ["hi!", "how's it going?"],        # "a" side
        ["hey!", "good, you?"],            # "b" side
    ]

    ready = {n: asyncio.Event() for n in users}
    start = asyncio.Event()

    tasks = []
    for a, b in pairs:
        conv_id = conversations[(a, b)]
        tasks.append(
            asyncio.create_task(
                run_user(
                    name=a,
                    token=tokens[a],
                    conversation_id=conv_id,
                    my_lines=convo_scripts[0],
                    ready=ready[a],
                    start=start,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                run_user(
                    name=b,
                    token=tokens[b],
                    conversation_id=conv_id,
                    my_lines=convo_scripts[1],
                    ready=ready[b],
                    start=start,
                )
            )
        )

    for ev in ready.values():
        await ev.wait()
    print("    all 10 sockets subscribed — 5 conversations begin simultaneously")
    t0 = time.perf_counter()
    start.set()

    results = {r.name: r for r in await asyncio.gather(*tasks)}
    wall_sec = time.perf_counter() - t0

    print(f"\n  wall-clock to exchange 20 messages across 5 pairs: {wall_sec:.2f}s")

    # Per-pair isolation — each user should see 2 of the partner and 0 of others.
    print("\n  pair-isolation assertions")
    total_bleed = 0
    for a, b in pairs:
        for me, partner in ((a, b), (b, a)):
            partner_count = sum(1 for s, _ in results[me].received if s == partner)
            stranger_count = sum(
                1
                for s, _ in results[me].received
                if s != partner and s != me
            )
            total_bleed += stranger_count
            ok = partner_count == 2 and stranger_count == 0
            print(
                f"    {me:4} saw partner({partner}):{partner_count}  "
                f"strangers:{stranger_count}  {'✓' if ok else '✗'}"
            )
    print(f"\n  total cross-pair bleed across 10 users: {total_bleed} "
          f"{'✓' if total_bleed == 0 else '✗'}")

    # DB verify — 5 conversations × 4 messages = 20 rows; isolated by conv.
    print("\n  DB verify — per-conversation counts")
    async with SessionLocal() as session:
        per_conv: dict[str, int] = {}
        for conv_id in conversations.values():
            n = (
                await session.execute(
                    text("SELECT COUNT(*) FROM messages WHERE conversation_id = :c"),
                    {"c": conv_id},
                )
            ).scalar_one()
            per_conv[conv_id] = n
        for cid, n in per_conv.items():
            print(f"    {cid}: {n} messages {'✓' if n == 4 else '✗'}")
        total = sum(per_conv.values())
        print(f"    total across 5 conversations: {total} (expected 20) {'✓' if total == 20 else '✗'}")

    # Aggregate latency across all 10 users
    all_lat = [v for r in results.values() for v in r.latencies_ms]
    print(
        f"\n  send latency across 10 sockets (20 messages):  "
        f"p50={sorted(all_lat)[len(all_lat) // 2]:.2f}ms  max={max(all_lat):.2f}ms"
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    await scenario_a_topics()
    await scenario_b_five_pairs()
    print("\n==> ALL SCENARIOS DONE")


if __name__ == "__main__":
    asyncio.run(main())
