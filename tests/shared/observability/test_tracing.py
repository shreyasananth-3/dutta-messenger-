"""Unit tests for OpenTelemetry tracing init (opt-in)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from src.shared.observability import tracing as tracing_mod


class TestInitTracing:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        # Disabled means no exception even with a fresh app.
        tracing_mod.init_tracing(FastAPI())

    @pytest.mark.parametrize("flag", ["false", "no", "0", "off"])
    def test_explicit_false_values(self, monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
        monkeypatch.setenv("OTEL_ENABLED", flag)
        assert tracing_mod._enabled() is False

    @pytest.mark.parametrize("flag", ["true", "1", "yes", "TRUE"])
    def test_explicit_truthy_values(self, monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
        monkeypatch.setenv("OTEL_ENABLED", flag)
        assert tracing_mod._enabled() is True


class TestObservabilityInit:
    def test_init_observability_runs_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # All four subsystems should run without error in their default
        # (disabled / development) configuration.
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        from src.shared.observability import init_observability

        init_observability(FastAPI())
