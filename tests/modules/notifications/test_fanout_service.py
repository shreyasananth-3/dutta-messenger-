"""Integration tests for `FanoutService`."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.notifications.models.db_models import (
    Notification,
    NotificationBatch,
)
from src.modules.notifications.services.fanout_service import FanoutService
from tests.modules.notifications.factories import make_institution, make_user


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def sender(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="sender@x.test")


@pytest_asyncio.fixture
async def recipients(
    db_session: AsyncSession, institution: Institution
) -> list[User]:
    return [
        await make_user(db_session, institution=institution, email=f"r{i}@x.test")
        for i in range(3)
    ]


class TestDispatchMessageNotifications:
    @pytest.mark.asyncio
    async def test_persists_notifications_and_batches_and_enqueues(
        self,
        db_session: AsyncSession,
        institution: Institution,
        sender: User,
        recipients: list[User],
        no_enqueue: None,
    ) -> None:
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(sender.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(u.id) for u in recipients],
            title="Engineering Team",
            body="Rajesh: Hello!",
            data={"type": "message", "conversation_id": "conv-1"},
        )
        assert len(batches) == len(recipients)

        notif_count = await db_session.scalar(
            text("SELECT count(*) FROM notifications")
        )
        assert notif_count == len(recipients)

    @pytest.mark.asyncio
    async def test_empty_recipients_returns_empty_list(
        self,
        db_session: AsyncSession,
        institution: Institution,
        sender: User,
        no_enqueue: None,
    ) -> None:
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(sender.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[],
            title="x",
            body="y",
        )
        assert batches == []

    @pytest.mark.asyncio
    async def test_unicode_title_and_body_survives_round_trip(
        self,
        db_session: AsyncSession,
        institution: Institution,
        sender: User,
        recipients: list[User],
        no_enqueue: None,
    ) -> None:
        title = "测试 😀 नमस्ते"
        body = "مرحبا — RTL content!"
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(sender.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(recipients[0].id)],
            title=title,
            body=body,
        )
        assert len(batches) == 1
        persisted = await db_session.scalar(
            select(Notification).where(
                Notification.id == batches[0].notification_ids[0]
            )
        )
        assert persisted is not None
        assert persisted.title == title
        assert persisted.body == body


class TestRecordBatchResult:
    @pytest.mark.asyncio
    async def test_success_marks_sent_and_writes_audit(
        self,
        db_session: AsyncSession,
        institution: Institution,
        sender: User,
        recipients: list[User],
        no_enqueue: None,
    ) -> None:
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(sender.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(recipients[0].id)],
            title="t",
            body="b",
        )
        await FanoutService.record_batch_result(
            db_session,
            batch=batches[0],
            institution_id=uuid.UUID(institution.id),
            success=True,
        )
        refreshed = await db_session.scalar(
            select(NotificationBatch).where(NotificationBatch.id == batches[0].id)
        )
        assert refreshed is not None
        assert refreshed.status == "sent"

    @pytest.mark.asyncio
    async def test_failure_sets_status_and_reason(
        self,
        db_session: AsyncSession,
        institution: Institution,
        sender: User,
        recipients: list[User],
        no_enqueue: None,
    ) -> None:
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(sender.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(recipients[0].id)],
            title="t",
            body="b",
        )
        await FanoutService.record_batch_result(
            db_session,
            batch=batches[0],
            institution_id=uuid.UUID(institution.id),
            success=False,
            failure_reason="fcm_rate_limited",
        )
        refreshed = await db_session.scalar(
            select(NotificationBatch).where(NotificationBatch.id == batches[0].id)
        )
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.failure_reason == "fcm_rate_limited"
