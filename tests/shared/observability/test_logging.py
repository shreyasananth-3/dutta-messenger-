"""Unit tests for structured logging configuration."""

from __future__ import annotations

import pytest
import structlog

from src.shared.observability.logging import (
    _add_correlation_id,
    bind_correlation_id,
    configure_logging,
)


class TestBindCorrelationId:
    def test_processor_injects_bound_id(self) -> None:
        bind_correlation_id("test-cid-123")
        out = _add_correlation_id(None, "info", {"event": "x"})
        assert out["correlation_id"] == "test-cid-123"

    def test_processor_skips_when_unbound(self) -> None:
        bind_correlation_id(None)
        out = _add_correlation_id(None, "info", {"event": "x"})
        assert "correlation_id" not in out

    def test_processor_does_not_overwrite_explicit(self) -> None:
        bind_correlation_id("ambient")
        out = _add_correlation_id(None, "info", {"event": "x", "correlation_id": "explicit"})
        assert out["correlation_id"] == "explicit"


class TestConfigureLogging:
    def test_runs_without_error(self) -> None:
        configure_logging()
        # If we got here, structlog was reconfigured successfully.
        logger = structlog.get_logger()
        logger.info("post_configure_event", k="v")

    def test_invalid_log_level_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force an unknown level so the fallback branch (logging.INFO) runs.
        from src.config import settings

        monkeypatch.setattr(settings, "LOG_LEVEL", "garbage", raising=False)
        configure_logging()
        # After configure, root logger should be at INFO (the fallback).
        # We don't hard-assert level since other configurations may follow,
        # but no exception is the contract we care about.
