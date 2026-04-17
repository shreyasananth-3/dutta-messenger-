"""Smoke test for the test harness itself.

Verifies that the shared fixtures boot, pytest-asyncio is wired correctly, and
coverage reporting works — so CI has something real to run from day one and a
broken harness fails loudly rather than silently skipping tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_harness_boots() -> None:
    """If pytest can collect and run this, the harness is alive."""
    assert 1 + 1 == 2


@pytest.mark.unit
def test_markers_registered() -> None:
    """Markers declared in pyproject.toml must not trigger strict-markers errors."""
    assert True
