"""Unit tests for the secrets provider abstraction."""

from __future__ import annotations

import importlib

import pytest


def _reload(monkeypatch: pytest.MonkeyPatch, backend: str) -> object:
    monkeypatch.setenv("SECRETS_BACKEND", backend)
    import src.shared.security.secrets_provider as sp_mod

    return importlib.reload(sp_mod)


class TestEnvBackend:
    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sp = _reload(monkeypatch, "env")
        monkeypatch.setenv("MY_SECRET", "hunter2")
        assert sp.get_secret("MY_SECRET") == "hunter2"

    def test_returns_default_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sp = _reload(monkeypatch, "env")
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        assert sp.get_secret("MISSING_SECRET", default="fallback") == "fallback"

    def test_default_default_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sp = _reload(monkeypatch, "env")
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        assert sp.get_secret("MISSING_SECRET") is None


class TestUnimplementedBackends:
    @pytest.mark.parametrize("backend", ["aws", "gcp", "vault"])
    def test_raises_not_implemented(self, monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
        sp = _reload(monkeypatch, backend)
        with pytest.raises(NotImplementedError):
            sp.get_secret("ANYTHING")


class TestUnknownBackend:
    def test_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sp = _reload(monkeypatch, "made-up")
        with pytest.raises(ValueError):
            sp.get_secret("ANYTHING")


@pytest.fixture(autouse=True)
def _restore_default_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the secrets module back to env after every test."""
    yield
    monkeypatch.setenv("SECRETS_BACKEND", "env")
    import src.shared.security.secrets_provider as sp_mod

    importlib.reload(sp_mod)
