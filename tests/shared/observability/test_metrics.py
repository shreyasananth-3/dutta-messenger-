"""Unit tests for Prometheus metric registration."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.shared.observability.metrics import (
    AUTH_FAILURES,
    MESSAGES_SENT,
    RATE_LIMITED_REQUESTS,
    WEBSOCKET_CONNECTIONS,
    register_metrics,
)


class TestMetricCounters:
    def test_messages_sent_counter_increments(self) -> None:
        before = MESSAGES_SENT.labels(conversation_type="dm")._value.get()
        MESSAGES_SENT.labels(conversation_type="dm").inc()
        after = MESSAGES_SENT.labels(conversation_type="dm")._value.get()
        assert after == before + 1

    def test_auth_failures_counter_increments(self) -> None:
        before = AUTH_FAILURES.labels(reason="bad_password")._value.get()
        AUTH_FAILURES.labels(reason="bad_password").inc(2)
        after = AUTH_FAILURES.labels(reason="bad_password")._value.get()
        assert after == before + 2

    def test_rate_limited_counter_increments(self) -> None:
        before = RATE_LIMITED_REQUESTS.labels(rule="default")._value.get()
        RATE_LIMITED_REQUESTS.labels(rule="default").inc()
        after = RATE_LIMITED_REQUESTS.labels(rule="default")._value.get()
        assert after == before + 1

    def test_websocket_gauge_set_and_inc(self) -> None:
        WEBSOCKET_CONNECTIONS.set(0)
        WEBSOCKET_CONNECTIONS.inc()
        assert WEBSOCKET_CONNECTIONS._value.get() == 1
        WEBSOCKET_CONNECTIONS.dec()
        assert WEBSOCKET_CONNECTIONS._value.get() == 0


class TestRegisterMetrics:
    def test_metrics_endpoint_exposed(self) -> None:
        app = FastAPI()
        register_metrics(app)
        with TestClient(app) as c:
            r = c.get("/metrics")
            assert r.status_code == 200
            body = r.text
            assert "dutta_messages_sent_total" in body
