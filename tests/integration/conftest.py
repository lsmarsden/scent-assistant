"""Home Assistant fixtures: config entries shaped like the two production ones."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scent_assistant.const import DOMAIN
from tests.fakes import AW_ADDRESS, GW_ADDRESS

# A326's advertisement payload with the MAC replaced by GW_ADDRESS.
GW_ADV_HEX = "A1000D000100010200000AAABBCCDDEE0100EF"

GW_ENTRY_DATA = {
    "ble_address": GW_ADDRESS,
    "ble_name": "A326",
    "connection_mode": "ble",
    "device_type": "scent_marketing_gw",
    "gw_password": "8888",
    "sm_metadata": {
        "heartbeat": False,
        "mac_from_adv": GW_ADDRESS.replace(":", ""),
        "mfr_id": 17932,
        "pid": 13,
        "raw_hex": GW_ADV_HEX,
        "wifi_flag": "ble",
    },
}
AW_ENTRY_DATA = {
    "ble_address": AW_ADDRESS,
    "ble_name": "dev505",
    "connection_mode": "ble",
    "device_type": "aroma_wave",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Let HA load custom_components/scent_assistant."""


@pytest.fixture(autouse=True)
async def unload_entries(hass: HomeAssistant):
    """Unload entries so each device's supervisor task stops before teardown."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def gw_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title="A326", unique_id=GW_ADDRESS,
                           data=GW_ENTRY_DATA)


@pytest.fixture
def aw_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title="dev505", unique_id=AW_ADDRESS,
                           data=AW_ENTRY_DATA)
