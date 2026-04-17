"""Unit tests for Sentry initialisation (opt-in)."""

from __future__ import annotations

import pytest

from src.shared.observability import sentry as sentry_mod


class TestInitSentry:
    def test_no_dsn_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # Should not raise even when sentry_sdk is unused.
        sentry_mod.init_sentry()

    def test_blank_dsn_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "   ")
        sentry_mod.init_sentry()

    def test_with_dsn_invokes_sdk_init(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
        monkeypatch.setenv("ENVIRONMENT", "test")
        captured: dict[str, object] = {}

        try:
            import sentry_sdk
        except ImportError:
            pytest.skip("sentry-sdk not installed")

        def _fake_init(*_args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(sentry_sdk, "init", _fake_init)
        sentry_mod.init_sentry()
        assert captured.get("environment") == "test"
        assert captured.get("send_default_pii") is False
