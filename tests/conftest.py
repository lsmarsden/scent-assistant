"""Fixtures shared by the device and integration tiers."""
from __future__ import annotations

import pytest

from tests.fakes import FakeLink


@pytest.fixture
def link(monkeypatch: pytest.MonkeyPatch) -> FakeLink:
    """Stand in for the Bluetooth stack as seen by device.py."""
    fake = FakeLink()
    fake.install(monkeypatch)
    return fake
