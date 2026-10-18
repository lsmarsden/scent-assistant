"""Fixtures shared by the device and integration tiers."""
from __future__ import annotations

import pytest

from tests.fakes import FakeBluetooth


@pytest.fixture
def bt(monkeypatch: pytest.MonkeyPatch) -> FakeBluetooth:
    """Stand in for the Bluetooth stack as seen by link.py."""
    fake = FakeBluetooth()
    fake.install(monkeypatch)
    return fake
