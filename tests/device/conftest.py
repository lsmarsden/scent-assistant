"""Device managers for our two diffusers, wired to the fake BLE link."""
from __future__ import annotations

import pytest

from custom_components.scent_assistant.const import DeviceType
from custom_components.scent_assistant.device import ScentDiffuserDevice
from tests.fakes import AW_ADDRESS, GW_ADDRESS, FakeLink, settle


@pytest.fixture
async def make_device(link: FakeLink):
    """Build (and later shut down) a device manager for one of our diffusers."""
    devices: list[ScentDiffuserDevice] = []

    def make(device_type: DeviceType, **kwargs) -> ScentDiffuserDevice:
        defaults = {
            DeviceType.SCENT_MARKETING_GW: {"ble_address": GW_ADDRESS, "ble_name": "A326",
                                            "sm_metadata": {"pid": 13}},
            DeviceType.AROMA_WAVE: {"ble_address": AW_ADDRESS, "ble_name": "dev505"},
        }.get(device_type, {"ble_address": GW_ADDRESS, "ble_name": "diffuser"})
        dev = ScentDiffuserDevice(hass=object(), device_type=device_type,
                                  **{**defaults, **kwargs})
        devices.append(dev)
        return dev

    yield make
    for dev in devices:
        await dev.async_shutdown()
    await settle()


@pytest.fixture
async def connected(make_device, link: FakeLink):
    """Start a device and wait for its first connection."""

    async def start(device_type: DeviceType, **kwargs) -> ScentDiffuserDevice:
        dev = make_device(device_type, **kwargs)
        await dev.async_setup()
        await settle()
        assert link.clients, "device never connected"
        return dev

    return start
