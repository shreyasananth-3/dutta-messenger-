"""Tests for the Celery push task — drives `_run_batch` with a mocked FCM."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.models.db_models import Institution, User
from src.modules.notifications.models.db_models import (
    FcmToken,
    NotificationBatch,
)
from src.modules.notifications.services.fanout_service import FanoutService
from src.modules.notifications.tasks import push_task
from src.shared.observability.metrics import NOTIFICATIONS_DELIVERED
from tests.modules.notifications.factories import (
    MockFcmClient,
    MockFcmResult,
    make_institution,
    make_token,
    make_user,
)


def _metric(label: str) -> float:
    """Read the current counter value for one label, robust to ordering."""
    sample = NOTIFICATIONS_DELIVERED.labels(result=label)._value.get()
    return float(sample)


@pytest_asyncio.fixture
async def institution(db_session: AsyncSession) -> Institution:
    return await make_institution(db_session)


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="alice@x.test")


@pytest_asyncio.fixture
async def bob(db_session: AsyncSession, institution: Institution) -> User:
    return await make_user(db_session, institution=institution, email="bob@x.test")


@pytest_asyncio.fixture
async def batch_with_token(
    db_session: AsyncSession,
    institution: Institution,
    alice: User,
    bob: User,
    no_enqueue: None,
) -> NotificationBatch:
    """Alice sends a message, Bob is the recipient with one active token."""
    await make_token(db_session, user=bob, institution=institution)
    batches = await FanoutService.dispatch_message_notifications(
        db_session,
        sender_id=uuid.UUID(alice.id),
        institution_id=uuid.UUID(institution.id),
        recipient_user_ids=[uuid.UUID(bob.id)],
        title="Hi Bob",
        body="Ping",
        data={"type": "message"},
    )
    return batches[0]


class TestRunBatch:
    @pytest.mark.asyncio
    async def test_success_marks_batch_sent_and_increments_metric(
        self,
        db_session: AsyncSession,
        institution: Institution,
        batch_with_token: NotificationBatch,
        mock_fcm_client: MockFcmClient,
    ) -> None:
        before = _metric("success")
        result = await push_task.run_batch(
            db_session,
            batch_id=batch_with_token.id,
            institution_id=uuid.UUID(institution.id),
        )
        assert result["status"] == "sent"
        assert _metric("success") == pytest.approx(before + 1)
        refreshed = await db_session.scalar(
            select(NotificationBatch).where(NotificationBatch.id == batch_with_token.id)
        )
        assert refreshed is not None
        assert refreshed.status == "sent"
        assert mock_fcm_client.calls[0]["title"] == "Hi Bob"

    @pytest.mark.asyncio
    async def test_failure_marks_batch_failed_and_increments_metric(
        self,
        db_session: AsyncSession,
        institution: Institution,
        batch_with_token: NotificationBatch,
        mock_fcm_client: MockFcmClient,
    ) -> None:
        mock_fcm_client.responses = [
            MockFcmResult(success_count=0, failure_count=1, error="transport")
        ]
        before = _metric("failure")
        result = await push_task.run_batch(
            db_session,
            batch_id=batch_with_token.id,
            institution_id=uuid.UUID(institution.id),
        )
        assert result["status"] == "failed"
        assert _metric("failure") == pytest.approx(before + 1)
        refreshed = await db_session.scalar(
            select(NotificationBatch).where(NotificationBatch.id == batch_with_token.id)
        )
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.failure_reason == "transport"

    @pytest.mark.asyncio
    async def test_unregistered_tokens_are_deactivated(
        self,
        db_session: AsyncSession,
        institution: Institution,
        alice: User,
        bob: User,
        no_enqueue: None,
        mock_fcm_client: MockFcmClient,
    ) -> None:
        await make_token(db_session, user=bob, institution=institution)
        token_row = await db_session.scalar(select(FcmToken).where(FcmToken.user_id == bob.id))
        assert token_row is not None
        mock_fcm_client.responses = [
            MockFcmResult(
                success_count=0,
                failure_count=1,
                unregistered_tokens=[token_row.token],
            )
        ]
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(bob.id)],
            title="t",
            body="b",
        )
        await push_task.run_batch(
            db_session,
            batch_id=batches[0].id,
            institution_id=uuid.UUID(institution.id),
        )
        refreshed = await db_session.scalar(select(FcmToken).where(FcmToken.id == token_row.id))
        assert refreshed is not None
        assert refreshed.is_active is False

    @pytest.mark.asyncio
    async def test_missing_batch_returns_status_missing(
        self,
        db_session: AsyncSession,
        institution: Institution,
    ) -> None:
        result = await push_task.run_batch(
            db_session,
            batch_id=str(uuid.uuid4()),
            institution_id=uuid.UUID(institution.id),
        )
        assert result["status"] == "missing"

    @pytest.mark.asyncio
    async def test_no_active_tokens_marks_batch_failed(
        self,
        db_session: AsyncSession,
        institution: Institution,
        alice: User,
        bob: User,
        no_enqueue: None,
        mock_fcm_client: MockFcmClient,
    ) -> None:
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(bob.id)],
            title="t",
            body="b",
        )
        result = await push_task.run_batch(
            db_session,
            batch_id=batches[0].id,
            institution_id=uuid.UUID(institution.id),
        )
        assert result["status"] == "failed"
        assert result["reason"] == "no_active_tokens"


class TestConfigureClient:
    def test_configure_and_current(self) -> None:
        original = push_task.current_fcm_client()
        try:
            new_client: Any = MockFcmClient()
            push_task.configure_fcm_client(new_client)
            assert push_task.current_fcm_client() is new_client
        finally:
            push_task.configure_fcm_client(original)


class TestDefaultFcmClient:
    def test_default_client_reports_all_success(self) -> None:
        client = push_task.FcmClient()
        resp = client.send_multicast(tokens=["a", "b", "c"], title="t", body="b", data={"k": "v"})
        assert resp.success_count == 3
        assert resp.failure_count == 0
        assert resp.unregistered_tokens == []
        assert resp.error is None


class TestJsonable:
    def test_none_returns_none(self) -> None:
        assert push_task._jsonable(None) is None

    def test_dict_returns_dict_identity(self) -> None:
        data = {"a": 1}
        assert push_task._jsonable(data) is data

    def test_iterable_is_coerced_via_dict(self) -> None:
        assert push_task._jsonable([("k", "v")]) == {"k": "v"}


class TestNotificationMissingPath:
    @pytest.mark.asyncio
    async def test_returns_notification_missing(
        self,
        db_session: AsyncSession,
        institution: Institution,
        alice: User,
        bob: User,
        no_enqueue: None,
        mock_fcm_client: MockFcmClient,
    ) -> None:
        await make_token(db_session, user=bob, institution=institution)
        batches = await FanoutService.dispatch_message_notifications(
            db_session,
            sender_id=uuid.UUID(alice.id),
            institution_id=uuid.UUID(institution.id),
            recipient_user_ids=[uuid.UUID(bob.id)],
            title="t",
            body="b",
        )
        # Blow away the notification row so _process_batch hits the branch.
        await db_session.execute(
            text("DELETE FROM notifications WHERE id = :id"),
            {"id": batches[0].notification_ids[0]},
        )
        result = await push_task.run_batch(
            db_session,
            batch_id=batches[0].id,
            institution_id=uuid.UUID(institution.id),
        )
        assert result["status"] == "failed"
        assert result["reason"] == "notification_missing"
