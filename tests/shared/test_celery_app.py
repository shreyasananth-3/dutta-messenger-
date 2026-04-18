"""Unit tests for the shared Celery instance.

We do NOT spin up a real Celery worker or broker here — that belongs in
the integration layer (Stage 4f notifications module brings that up).
These tests only verify the configuration contract so Stage-4 module
authors can rely on the settings they'll inherit.
"""

from __future__ import annotations

from src.config import settings
from src.shared.celery_app import celery_app


class TestCeleryInstance:
    def test_instance_name(self) -> None:
        assert celery_app.main == "dutta_messenger"

    def test_broker_url_matches_settings(self) -> None:
        assert celery_app.conf.broker_url == settings.CELERY_BROKER_URL

    def test_result_backend_matches_settings(self) -> None:
        assert celery_app.conf.result_backend == settings.CELERY_RESULT_BACKEND


class TestCeleryConfig:
    def test_json_only_serialisation(self) -> None:
        """No pickle — json only, per security baseline."""
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert "json" in celery_app.conf.accept_content
        assert "pickle" not in celery_app.conf.accept_content

    def test_utc_timezone(self) -> None:
        assert celery_app.conf.timezone == "UTC"
        assert celery_app.conf.enable_utc is True

    def test_late_acks_for_reliability(self) -> None:
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_time_limits_are_finite(self) -> None:
        """Unbounded tasks block workers. Must have both hard + soft limits."""
        assert celery_app.conf.task_time_limit is not None
        assert celery_app.conf.task_soft_time_limit is not None
        assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit

    def test_worker_recycling_configured(self) -> None:
        """Memory hygiene — Pillow leaks from repeated image ops."""
        assert celery_app.conf.worker_max_tasks_per_child == 1000

    def test_task_tracking_enabled(self) -> None:
        assert celery_app.conf.task_track_started is True

    def test_result_expiration_bounded(self) -> None:
        assert celery_app.conf.result_expires == 3600


class TestTaskRegistration:
    def test_only_known_modules_register_tasks(self) -> None:
        """Every user-level task in the shared registry must be declared
        by one of the modules that actually owns background work.

        Today: notifications (FCM fanout). Add to the allow-list when a
        new module starts enqueueing tasks so typos are surfaced.
        """
        allowed_prefixes = ("notifications.",)
        user_tasks = [name for name in celery_app.tasks if not name.startswith("celery.")]
        unexpected = [n for n in user_tasks if not n.startswith(allowed_prefixes)]
        assert unexpected == [], f"Unexpected Celery tasks registered: {unexpected}"
