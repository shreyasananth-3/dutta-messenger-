"""HTTP-level tests for notification routes — 7-point checklist + cross-tenant."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.notifications.models.db_models import FcmToken, Notification
from src.shared.middleware.auth import create_access_token
from tests.modules.notifications.factories import (
    fresh_token_string,
    make_institution,
    make_token,
    make_user,
)

API = "/api/v1"


def auth_header(user: User) -> dict[str, str]:
    """Bearer header for `user` — mirrors the root conftest's `auth_headers`."""
    token = create_access_token(
        user_id=uuid.UUID(user.id),
        institution_id=uuid.UUID(user.institution_id),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="alice@x.test")


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="bob@x.test")


class TestPostTokens:
    @pytest.mark.asyncio
    async def test_happy_path_200(self, notif_client: AsyncClient, alice: User) -> None:
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": fresh_token_string(), "device_type": "ios"},
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["reused"] is False
        assert body["data"]["token"]["is_active"] is True

    @pytest.mark.asyncio
    async def test_missing_auth_returns_401_or_403(self, notif_client: AsyncClient) -> None:
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": fresh_token_string()},
        )
        assert r.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_validation_error_returns_422(
        self, notif_client: AsyncClient, alice: User
    ) -> None:
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": "", "device_type": "palm-pilot"},
            headers=auth_header(alice),
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_idempotent_same_token_returns_200_not_409(
        self,
        notif_client: AsyncClient,
        alice: User,
    ) -> None:
        token_str = fresh_token_string()
        first = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": token_str, "device_type": "ios"},
            headers=auth_header(alice),
        )
        second = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": token_str, "device_type": "ios"},
            headers=auth_header(alice),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["reused"] is True

    @pytest.mark.asyncio
    async def test_unicode_device_name_accepted(
        self, notif_client: AsyncClient, alice: User
    ) -> None:
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={
                "token": fresh_token_string(),
                "device_name": "रमेश का iPhone 📱",
                "device_type": "ios",
            },
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["token"]["device_name"] == "रमेश का iPhone 📱"

    @pytest.mark.asyncio
    async def test_max_length_token_accepted(self, notif_client: AsyncClient, alice: User) -> None:
        big_token = "a" * 500
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": big_token, "device_type": "android"},
            headers=auth_header(alice),
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_over_max_length_token_returns_422(
        self, notif_client: AsyncClient, alice: User
    ) -> None:
        r = await notif_client.post(
            f"{API}/notifications/tokens",
            json={"token": "a" * 501, "device_type": "android"},
            headers=auth_header(alice),
        )
        assert r.status_code == 422


class TestDeleteTokens:
    @pytest.mark.asyncio
    async def test_revoke_returns_204(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        r = await notif_client.delete(
            f"{API}/notifications/tokens/{row.id}",
            headers=auth_header(alice),
        )
        assert r.status_code == 204
        refreshed = await db_session.get(FcmToken, row.id)
        assert refreshed is not None
        assert refreshed.is_active is False

    @pytest.mark.asyncio
    async def test_revoke_unknown_returns_404(self, notif_client: AsyncClient, alice: User) -> None:
        r = await notif_client.delete(
            f"{API}/notifications/tokens/{uuid.uuid4()}",
            headers=auth_header(alice),
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_revoke_other_user_token_returns_404(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
        bob: User,
        institution: Institution,
    ) -> None:
        row = await make_token(db_session, user=alice, institution=institution)
        r = await notif_client.delete(
            f"{API}/notifications/tokens/{row.id}",
            headers=auth_header(bob),
        )
        assert r.status_code == 404


class TestUnreadCount:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_notifications(
        self, notif_client: AsyncClient, alice: User
    ) -> None:
        r = await notif_client.get(
            f"{API}/notifications/unread-count",
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["unread"] == 0

    @pytest.mark.asyncio
    async def test_counts_only_callers_unread(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
        bob: User,
    ) -> None:
        db_session.add(
            Notification(
                user_id=alice.id,
                type="message",
                title="hello",
                body="ping",
            )
        )
        db_session.add(
            Notification(
                user_id=bob.id,
                type="message",
                title="hello",
                body="ping",
            )
        )
        await db_session.flush()

        r = await notif_client.get(
            f"{API}/notifications/unread-count",
            headers=auth_header(alice),
        )
        assert r.json()["data"]["unread"] == 1

    @pytest.mark.asyncio
    async def test_unauth_returns_401_or_403(self, notif_client: AsyncClient) -> None:
        r = await notif_client.get(f"{API}/notifications/unread-count")
        assert r.status_code in {401, 403}


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_mark_all_unread_for_caller(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
    ) -> None:
        for _ in range(3):
            db_session.add(
                Notification(
                    user_id=alice.id,
                    type="message",
                    title="t",
                    body="b",
                )
            )
        await db_session.flush()
        r = await notif_client.post(
            f"{API}/notifications/mark-read",
            json={"notification_ids": []},
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["marked"] == 3

    @pytest.mark.asyncio
    async def test_mark_read_ignores_other_users_ids(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
        bob: User,
    ) -> None:
        bob_note = Notification(
            user_id=bob.id,
            type="message",
            title="t",
            body="b",
        )
        db_session.add(bob_note)
        await db_session.flush()

        r = await notif_client.post(
            f"{API}/notifications/mark-read",
            json={"notification_ids": [str(bob_note.id)]},
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["marked"] == 0


class TestCrossTenantRoutes:
    @pytest.mark.asyncio
    async def test_cross_institution_delete_returns_404(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
    ) -> None:
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        user_a = await make_user(db_session, institution=inst_a)
        user_b = await make_user(db_session, institution=inst_b)
        row = await make_token(db_session, user=user_a, institution=inst_a)

        r = await notif_client.delete(
            f"{API}/notifications/tokens/{row.id}",
            headers=auth_header(user_b),
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_unread_count_rejects_forged_institution(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
    ) -> None:
        """The explicit `User.institution_id` join blocks a JWT whose
        `institution_id` claim was swapped to another institution's."""
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        user_a = await make_user(db_session, institution=inst_a)
        db_session.add(Notification(user_id=user_a.id, type="message", title="t", body="b"))
        await db_session.flush()

        # JWT with user_a's id but institution_b's id — defence-in-depth.
        forged = create_access_token(
            user_id=uuid.UUID(user_a.id),
            institution_id=uuid.UUID(inst_b.id),
        )
        r = await notif_client.get(
            f"{API}/notifications/unread-count",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["unread"] == 0


class TestMarkReadAudit:
    @pytest.mark.asyncio
    async def test_mark_read_writes_audit_row(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
    ) -> None:
        """`mark_read` emits one `notifications.marked_read` audit row."""
        from sqlalchemy import text

        db_session.add(Notification(user_id=alice.id, type="message", title="t", body="b"))
        await db_session.flush()

        r = await notif_client.post(
            f"{API}/notifications/mark-read",
            json={"notification_ids": []},
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["marked"] == 1

        row = (
            (
                await db_session.execute(
                    text(
                        "SELECT action, resource_type, metadata "
                        "FROM audit_logs WHERE actor_id = :a AND action = :act"
                    ),
                    {"a": alice.id, "act": "notifications.marked_read"},
                )
            )
            .mappings()
            .first()
        )
        assert row is not None
        assert row["resource_type"] == "notification"

    @pytest.mark.asyncio
    async def test_mark_read_no_audit_when_nothing_marked(
        self,
        db_session: AsyncSession,
        notif_client: AsyncClient,
        alice: User,
    ) -> None:
        """No audit row when zero notifications actually changed state."""
        from sqlalchemy import text

        r = await notif_client.post(
            f"{API}/notifications/mark-read",
            json={"notification_ids": []},
            headers=auth_header(alice),
        )
        assert r.status_code == 200
        assert r.json()["data"]["marked"] == 0

        row = (
            (
                await db_session.execute(
                    text("SELECT action FROM audit_logs WHERE actor_id = :a AND action = :act"),
                    {"a": alice.id, "act": "notifications.marked_read"},
                )
            )
            .mappings()
            .first()
        )
        assert row is None
