"""HTTP-level tests for groups — 7-point checklist + cross-tenant."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import User
from tests.modules.groups.conftest import auth_header, make_institution, make_user

API = "/api/v1"


class TestCreateGroup:
    @pytest.mark.asyncio
    async def test_happy_simple(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Class 7A", "mode": "simple"},
        )
        assert r.status_code == 201
        assert r.json()["data"]["mode"] == "simple"
        assert r.json()["data"]["created_by_user_id"] == alice.id

    @pytest.mark.asyncio
    async def test_happy_topics(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Staff Room", "mode": "topics"},
        )
        assert r.status_code == 201
        assert r.json()["data"]["mode"] == "topics"

    @pytest.mark.asyncio
    async def test_unauth_401(self, groups_client: AsyncClient) -> None:
        r = await groups_client.post(f"{API}/groups", json={"name": "X", "mode": "simple"})
        assert r.status_code in {401, 403}

    @pytest.mark.asyncio
    async def test_invalid_mode_422(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "X", "mode": "weird"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_name_422(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "", "mode": "simple"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_name_accepted(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "कक्षा ७अ 📚", "mode": "simple"},
        )
        assert r.status_code == 201
        assert r.json()["data"]["name"] == "कक्षा ७अ 📚"


class TestListGroups:
    @pytest.mark.asyncio
    async def test_only_own_groups_returned(
        self, groups_client: AsyncClient, alice: User, bob: User
    ) -> None:
        a = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Alice G", "mode": "simple"},
        )
        assert a.status_code == 201
        b = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(bob),
            json={"name": "Bob G", "mode": "simple"},
        )
        assert b.status_code == 201

        r = await groups_client.get(f"{API}/groups", headers=auth_header(alice))
        names = {row["name"] for row in r.json()["data"]}
        assert "Alice G" in names
        assert "Bob G" not in names


class TestGetGroup:
    @pytest.mark.asyncio
    async def test_non_member_sees_404(
        self, groups_client: AsyncClient, alice: User, bob: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Private", "mode": "simple"},
        )
        group_id = created.json()["data"]["id"]
        r = await groups_client.get(f"{API}/groups/{group_id}", headers=auth_header(bob))
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_id_404(self, groups_client: AsyncClient, alice: User) -> None:
        r = await groups_client.get(f"{API}/groups/{uuid.uuid4()}", headers=auth_header(alice))
        assert r.status_code == 404


class TestMembers:
    @pytest.mark.asyncio
    async def test_add_and_list_idempotent(
        self, groups_client: AsyncClient, alice: User, bob: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Team", "mode": "simple"},
        )
        gid = created.json()["data"]["id"]
        first = await groups_client.post(
            f"{API}/groups/{gid}/members",
            headers=auth_header(alice),
            json={"user_id": bob.id, "role": "member"},
        )
        assert first.status_code == 200
        assert first.json()["data"]["reused"] is False

        second = await groups_client.post(
            f"{API}/groups/{gid}/members",
            headers=auth_header(alice),
            json={"user_id": bob.id, "role": "member"},
        )
        assert second.status_code == 200
        assert second.json()["data"]["reused"] is True

        listing = await groups_client.get(f"{API}/groups/{gid}/members", headers=auth_header(alice))
        assert listing.status_code == 200
        ids = {row["user_id"] for row in listing.json()["data"]}
        assert alice.id in ids
        assert bob.id in ids

    @pytest.mark.asyncio
    async def test_non_admin_cannot_add(
        self, groups_client: AsyncClient, alice: User, bob: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "T", "mode": "simple"},
        )
        gid = created.json()["data"]["id"]
        # Add bob as member first
        await groups_client.post(
            f"{API}/groups/{gid}/members",
            headers=auth_header(alice),
            json={"user_id": bob.id, "role": "member"},
        )
        # Now bob tries to add alice again (alice is already there, but bob isn't admin)
        r = await groups_client.post(
            f"{API}/groups/{gid}/members",
            headers=auth_header(bob),
            json={"user_id": alice.id, "role": "admin"},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_remove_owner_forbidden(
        self, groups_client: AsyncClient, alice: User, bob: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "T", "mode": "simple"},
        )
        gid = created.json()["data"]["id"]
        r = await groups_client.delete(
            f"{API}/groups/{gid}/members/{alice.id}",
            headers=auth_header(alice),
        )
        assert r.status_code == 403


class TestTopics:
    @pytest.mark.asyncio
    async def test_default_general_topic_created(
        self, groups_client: AsyncClient, alice: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Staff", "mode": "topics"},
        )
        gid = created.json()["data"]["id"]
        r = await groups_client.get(f"{API}/groups/{gid}/topics", headers=auth_header(alice))
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["data"]}
        assert "General" in names

    @pytest.mark.asyncio
    async def test_create_topic_in_simple_mode_conflict(
        self, groups_client: AsyncClient, alice: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "S", "mode": "simple"},
        )
        gid = created.json()["data"]["id"]
        r = await groups_client.post(
            f"{API}/groups/{gid}/topics",
            headers=auth_header(alice),
            json={"name": "Announcements"},
        )
        assert r.status_code == 409 or r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_duplicate_topic_409(
        self, groups_client: AsyncClient, alice: User
    ) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "T", "mode": "topics"},
        )
        gid = created.json()["data"]["id"]
        first = await groups_client.post(
            f"{API}/groups/{gid}/topics",
            headers=auth_header(alice),
            json={"name": "Homework"},
        )
        assert first.status_code == 201
        second = await groups_client.post(
            f"{API}/groups/{gid}/topics",
            headers=auth_header(alice),
            json={"name": "Homework"},
        )
        assert second.status_code == 409 or second.status_code == 400


class TestCrossTenant:
    @pytest.mark.asyncio
    async def test_cross_institution_group_404(
        self, groups_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        inst_a = await make_institution(db_session, name="A")
        inst_b = await make_institution(db_session, name="B")
        user_a = await make_user(db_session, institution=inst_a, email="a@x.test")
        user_b = await make_user(db_session, institution=inst_b, email="b@x.test")

        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(user_a),
            json={"name": "Secret A", "mode": "simple"},
        )
        group_id = created.json()["data"]["id"]

        r = await groups_client.get(f"{API}/groups/{group_id}", headers=auth_header(user_b))
        assert r.status_code == 404


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_then_not_in_list(self, groups_client: AsyncClient, alice: User) -> None:
        created = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Temp", "mode": "simple"},
        )
        gid = created.json()["data"]["id"]
        r = await groups_client.delete(f"{API}/groups/{gid}", headers=auth_header(alice))
        assert r.status_code == 204

        listing = await groups_client.get(f"{API}/groups", headers=auth_header(alice))
        ids = {row["id"] for row in listing.json()["data"]}
        assert gid not in ids


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_create_emits_group_created_audit(
        self,
        groups_client: AsyncClient,
        db_session: AsyncSession,
        alice: User,
    ) -> None:
        from sqlalchemy import text

        r = await groups_client.post(
            f"{API}/groups",
            headers=auth_header(alice),
            json={"name": "Audited", "mode": "simple"},
        )
        assert r.status_code == 201
        row = (
            (
                await db_session.execute(
                    text(
                        "SELECT action FROM audit_logs WHERE actor_id = :a "
                        "AND action = 'group.created'"
                    ),
                    {"a": alice.id},
                )
            )
            .mappings()
            .first()
        )
        assert row is not None
