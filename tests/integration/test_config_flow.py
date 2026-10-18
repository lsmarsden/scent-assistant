"""Config flow: Bluetooth discovery and the manual scan picker."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scent_assistant.const import (
    AROMAWAVE_MFR_ID,
    DOMAIN,
    SM_MFR_ID_GW,
)

from .conftest import AW_ADDRESS, AW_ENTRY_DATA, GW_ADDRESS, GW_ADV_HEX, GW_ENTRY_DATA


def service_info(address: str, name: str, manufacturer_data: dict[int, bytes]
                 ) -> BluetoothServiceInfoBleak:
    adv = AdvertisementData(
        local_name=name, manufacturer_data=manufacturer_data, service_data={},
        service_uuids=[], tx_power=None, rssi=-60, platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name, address=address, rssi=-60, manufacturer_data=manufacturer_data,
        service_data={}, service_uuids=[], source="local",
        device=BLEDevice(address, name, None), advertisement=adv,
        connectable=True, time=0, tx_power=None,
    )


A326 = service_info(GW_ADDRESS, "A326", {SM_MFR_ID_GW: bytes.fromhex(GW_ADV_HEX)})
DEV505 = service_info(AW_ADDRESS, "dev505", {AROMAWAVE_MFR_ID: b"\x01\x02"})
UNKNOWN = service_info("AA:BB:CC:DD:EE:03", "dev777", {})


@pytest.fixture(autouse=True)
def no_setup() -> Generator[AsyncMock]:
    """Create entries without starting the integration."""
    with patch("custom_components.scent_assistant.async_setup_entry",
               return_value=True) as mock:
        yield mock


async def discover(hass: HomeAssistant, info: BluetoothServiceInfoBleak):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
    )


async def test_discover_a326_with_password(hass: HomeAssistant) -> None:
    result = await discover(hass, A326)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "bluetooth_confirm")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "gw_password")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"gw_password": " 88889 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "A326"
    # Same shape as the production entry (password trimmed to 4 chars).
    assert result["data"] == GW_ENTRY_DATA
    assert result["result"].unique_id == GW_ADDRESS


async def test_discover_a326_without_password(hass: HomeAssistant) -> None:
    result = await discover(hass, A326)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"gw_password": ""})
    assert "gw_password" not in result["data"]


async def test_discover_dev505(hass: HomeAssistant) -> None:
    result = await discover(hass, DEV505)
    assert result["description_placeholders"]["device_type"].startswith("Aromawave")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == AW_ENTRY_DATA


async def test_unrecognised_device_asks_for_type(hass: HomeAssistant) -> None:
    result = await discover(hass, UNKNOWN)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "manual_type")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_type": "aroma_wave"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["device_type"] == "aroma_wave"


async def test_already_configured_is_ignored(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=GW_ADDRESS, data=GW_ENTRY_DATA).add_to_hass(hass)
    result = await discover(hass, A326)
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "already_configured")


async def test_scan_lists_unconfigured_devices(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=GW_ADDRESS, data=GW_ENTRY_DATA).add_to_hass(hass)
    with patch(
        "custom_components.scent_assistant.config_flow.bluetooth.async_discovered_service_info",
        return_value=[A326, DEV505, UNKNOWN],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ble_scan"}
        )

    options = result["data_schema"].schema["ble_address"].container
    # Recognised devices first, A326 left out because it is configured.
    assert list(options) == [AW_ADDRESS, "AA:BB:CC:DD:EE:03"]
    assert options[AW_ADDRESS].startswith("✓ dev505")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ble_address": AW_ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == AW_ENTRY_DATA
