"""HTTP-level tests for chat — send/edit/delete/list/read + cross-tenant."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import User
from src.modules.groups.services.group_service import GroupService
from tests.modules.chat.conftest import (
    auth_header,
    make_group_and_conv,
    make_institution,
    make_user,
)

API = "/api/v1"


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "hello world"},
        )
        assert r.status_code == 201
        assert r.json()["data"]["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_unauth_401(self, chat_client: AsyncClient) -> None:
        r = await chat_client.post(
            f"{API}/chat/conversations/{uuid.uuid4()}/messages",
            json={"content": "x"},
        )
        assert r.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_empty_422(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": ""},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_over_max_length_422(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "a" * 4097},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_accepted(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "नमस्ते 👋 你好"},
        )
        assert r.status_code == 201
        assert r.json()["data"]["content"] == "नमस्ते 👋 你好"


class TestListMessages:
    @pytest.mark.asyncio
    async def test_newest_first(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        for i in range(3):
            await chat_client.post(
                f"{API}/chat/conversations/{conv.id}/messages",
                headers=auth_header(alice),
                json={"content": f"msg {i}"},
            )
        r = await chat_client.get(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        contents = {m["content"] for m in r.json()["data"]}
        assert contents == {"msg 0", "msg 1", "msg 2"}
        # Newest-first ordering: latest created_at must be at index 0.
        rows = r.json()["data"]
        assert rows[0]["created_at"] >= rows[-1]["created_at"]


class TestEditDelete:
    @pytest.mark.asyncio
    async def test_edit_own_message(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        sent = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "original"},
        )
        mid = sent.json()["data"]["id"]
        r = await chat_client.patch(
            f"{API}/chat/messages/{mid}",
            headers=auth_header(alice),
            json={"content": "edited"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["content"] == "edited"

    @pytest.mark.asyncio
    async def test_cannot_edit_others_message(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
        bob: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        # Add bob to the group + conv
        await GroupService.add_member(
            db_session,
            institution_id=institution.id,
            group_id=_grp.id,
            actor_id=alice.id,
            target_user_id=bob.id,
        )
        from src.modules.chat.services.message_service import MessageService

        await MessageService.open_conversation(
            db_session,
            institution_id=institution.id,
            actor_id=bob.id,
            group_id=_grp.id,
        )
        await db_session.flush()

        sent = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "alice's message"},
        )
        mid = sent.json()["data"]["id"]
        r = await chat_client.patch(
            f"{API}/chat/messages/{mid}",
            headers=auth_header(bob),
            json={"content": "bob tries to edit"},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_is_soft(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        sent = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "to delete"},
        )
        mid = sent.json()["data"]["id"]
        r = await chat_client.delete(f"{API}/chat/messages/{mid}", headers=auth_header(alice))
        assert r.status_code == 204

        listing = await chat_client.get(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
        )
        row = next(m for m in listing.json()["data"] if m["id"] == mid)
        assert row["deleted_at"] is not None
        assert row["content"] == "[deleted]"


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        sent = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "mark me read"},
        )
        mid = sent.json()["data"]["id"]
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/read",
            headers=auth_header(alice),
            json={"last_read_message_id": mid},
        )
        assert r.status_code == 200


class TestCrossTenant:
    @pytest.mark.asyncio
    async def test_non_member_cannot_send(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        alice_a = await make_user(db_session, institution=inst_a, email="a@x.test")
        bob_b = await make_user(db_session, institution=inst_b, email="b@x.test")
        _grp, conv = await make_group_and_conv(db_session, institution=inst_a, creator=alice_a)
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(bob_b),
            json={"content": "sneaky"},
        )
        assert r.status_code == 404


class TestReply:
    @pytest.mark.asyncio
    async def test_reply_fk_captured(
        self,
        chat_client: AsyncClient,
        db_session: AsyncSession,
        institution,
        alice: User,
    ) -> None:
        _grp, conv = await make_group_and_conv(db_session, institution=institution, creator=alice)
        first = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "parent"},
        )
        pid = first.json()["data"]["id"]
        r = await chat_client.post(
            f"{API}/chat/conversations/{conv.id}/messages",
            headers=auth_header(alice),
            json={"content": "child", "reply_to_message_id": pid},
        )
        assert r.status_code == 201
        assert r.json()["data"]["reply_to_message_id"] == pid
