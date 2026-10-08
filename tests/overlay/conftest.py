"""Fixtures for the overlay tests."""

import pytest

from generators.overlay import registry
from tests.overlay.overlay_helpers import TEST_TYPE, draw_test_box


@pytest.fixture
def box_drawer(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register the test-only `test_box` overlay primitive for one test; returns its type name."""
    monkeypatch.setitem(registry.OVERLAY_DRAWERS, TEST_TYPE, draw_test_box)
    return TEST_TYPE
