"""Seed the database with a baseline dev / demo dataset.

Creates:
  - One institution ("DuttaMessenger Demo School")
  - 15 permissions + 3 system roles (super_admin / admin / member)
  - 1 super_admin user + 2 admins + 5 members
  - 1 simple group ("Staff Room") + 1 topics group ("Class 7A")
  - Both groups populated with every member
  - 5 seed messages in the simple group so Flutter has something to show

Idempotent — safe to re-run. If the demo institution already exists the
script exits cleanly without touching the data.

Usage:
    .venv/bin/python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from sqlalchemy import select

from src.modules.acl.services.acl_service import ACLService
from src.modules.auth.models.db_models import Institution
from src.modules.auth.services.auth_service import AuthService
from src.modules.chat.services.message_service import MessageService
from src.modules.groups.models.db_models import Topic
from src.modules.groups.services.group_service import GroupService
from src.shared.database import SessionLocal

logger = structlog.get_logger()

DEMO_INSTITUTION_NAME = "DuttaMessenger Demo School"
DEMO_ADMIN_EMAIL = "admin@demo.school"
DEMO_PASSWORD = "DemoPass123!"  # noqa: S105 — seed script only


async def seed() -> int:
    """Populate the baseline dataset. Returns a process exit code."""
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(Institution).where(Institution.name == DEMO_INSTITUTION_NAME)
        )
        if existing is not None:
            print(f"seed: '{DEMO_INSTITUTION_NAME}' already present — nothing to do.")
            return 0

        print("seed: creating institution + seeding ACL permissions + roles")
        inst = await AuthService.create_institution(
            session,
            name=DEMO_INSTITUTION_NAME,
            domain="demo.school",
        )
        roles = await ACLService.seed_institution_roles(session, institution_id=inst.id)

        print("seed: creating users (1 super_admin + 2 admins + 5 members)")
        users: dict[str, object] = {}
        users["super_admin"] = await AuthService.register_user(
            session,
            institution_id=inst.id,
            email=DEMO_ADMIN_EMAIL,
            password=DEMO_PASSWORD,
            full_name="Demo SuperAdmin",
        )
        await ACLService.assign_role(
            session,
            institution_id=inst.id,
            user_id=users["super_admin"].id,
            role_id=roles["super_admin"].id,
            assigned_by=users["super_admin"].id,
        )
        for i in range(1, 3):
            u = await AuthService.register_user(
                session,
                institution_id=inst.id,
                email=f"admin{i}@demo.school",
                password=DEMO_PASSWORD,
                full_name=f"Admin {i}",
            )
            await ACLService.assign_role(
                session,
                institution_id=inst.id,
                user_id=u.id,
                role_id=roles["admin"].id,
                assigned_by=users["super_admin"].id,
            )
            users[f"admin{i}"] = u
        for i in range(1, 6):
            u = await AuthService.register_user(
                session,
                institution_id=inst.id,
                email=f"user{i}@demo.school",
                password=DEMO_PASSWORD,
                full_name=f"User {i}",
            )
            await ACLService.assign_role(
                session,
                institution_id=inst.id,
                user_id=u.id,
                role_id=roles["member"].id,
                assigned_by=users["super_admin"].id,
            )
            users[f"user{i}"] = u

        print("seed: creating groups (simple + topics)")
        simple_group = await GroupService.create_group(
            session,
            institution_id=inst.id,
            creator_id=users["super_admin"].id,
            name="Staff Room",
            description="Simple-mode group for school staff",
            mode="simple",
        )
        topics_group = await GroupService.create_group(
            session,
            institution_id=inst.id,
            creator_id=users["super_admin"].id,
            name="Class 7A",
            description="Topics-mode group for Class 7A",
            mode="topics",
        )
        await GroupService.create_topic(
            session,
            institution_id=inst.id,
            group_id=topics_group.id,
            actor_id=users["super_admin"].id,
            name="Announcements",
            icon_emoji="📣",
        )

        print("seed: adding members to both groups")
        for key in ("admin1", "admin2", "user1", "user2", "user3", "user4", "user5"):
            u = users[key]
            for g in (simple_group, topics_group):
                await GroupService.add_member(
                    session,
                    institution_id=inst.id,
                    group_id=g.id,
                    actor_id=users["super_admin"].id,
                    target_user_id=u.id,
                    role="member" if key.startswith("user") else "admin",
                )

        print("seed: opening conversation + 5 demo messages (simple group)")
        conv = await MessageService.open_conversation(
            session,
            institution_id=inst.id,
            actor_id=users["super_admin"].id,
            group_id=simple_group.id,
        )
        for key in ("admin1", "admin2", "user1", "user2", "user3"):
            await MessageService.open_conversation(
                session,
                institution_id=inst.id,
                actor_id=users[key].id,
                group_id=simple_group.id,
            )
        seed_lines = [
            ("super_admin", "Welcome to the Staff Room 👋"),
            ("admin1", "PTA meeting next Tuesday at 4pm."),
            ("user1", "Can we reschedule to 4:30? I have bus duty."),
            ("admin2", "Let's try 4:30 — I'll update the calendar."),
            ("user3", "Thanks, works for me."),
        ]
        for key, text in seed_lines:
            await MessageService.send_message(
                session,
                institution_id=inst.id,
                actor_id=users[key].id,
                conversation_id=conv.id,
                content=text,
            )

        t_row = await session.execute(select(Topic).where(Topic.group_id == topics_group.id))
        general = t_row.scalars().first()
        if general is not None:
            await MessageService.open_conversation(
                session,
                institution_id=inst.id,
                actor_id=users["super_admin"].id,
                group_id=topics_group.id,
                topic_id=general.id,
            )

        await session.commit()

        print()
        print(f"seed: done. institution_id = {inst.id}")
        print(f"  login: {DEMO_ADMIN_EMAIL} / {DEMO_PASSWORD}")
        print(f"  simple group  : {simple_group.id}  (conv {conv.id})")
        print(f"  topics group  : {topics_group.id}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed()))
