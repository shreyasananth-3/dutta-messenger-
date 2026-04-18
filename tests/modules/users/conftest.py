"""Shared fixtures for users-module tests.

Creates a fresh institution + two users per test (admin = first registered,
other = second). Every test starts in a rolled-back transaction (root
conftest pattern) so there's no cross-test pollution.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.auth.services.auth_service import AuthService

_PASSWORD = "Sup3rStr0ngP@ss!"


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    inst = await AuthService.create_institution(
        db_session,
        name=f"School {uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.test",
    )
    await db_session.flush()
    return inst


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, institution: Institution) -> User:
    """First-registered user. Per the heuristic admin check, this is the
    institution admin until ACL (Stage 4b) replaces it with a real role."""
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="admin@users-test.test",
        password=_PASSWORD,
        full_name="Admin User",
    )
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def other_user(db_session: AsyncSession, institution: Institution, admin_user: User) -> User:
    """Second-registered user. NOT the admin under the heuristic."""
    user = await AuthService.register_user(
        db_session,
        institution_id=institution.id,
        email="other@users-test.test",
        password=_PASSWORD,
        full_name="Other Person",
    )
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def foreign_institution(db_session: AsyncSession) -> Institution:
    """An unrelated institution, for cross-tenant fuzz tests."""
    inst = await AuthService.create_institution(
        db_session,
        name=f"Foreign {uuid.uuid4().hex[:8]}",
        domain=f"{uuid.uuid4().hex[:6]}.foreign.test",
    )
    await db_session.flush()
    return inst


@pytest_asyncio.fixture
async def foreign_user(db_session: AsyncSession, foreign_institution: Institution) -> User:
    """A user who belongs to `foreign_institution`. Used to verify that
    institution-A users cannot read / poke institution-B rows."""
    user = await AuthService.register_user(
        db_session,
        institution_id=foreign_institution.id,
        email="foreigner@users-test.test",
        password=_PASSWORD,
        full_name="Foreign Person",
    )
    await db_session.flush()
    return user
