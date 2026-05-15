"""Config flow for Scent Diffuser integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from bleak import BleakScanner

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import (
    DOMAIN,
    CONF_DEVICE_TYPE,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CLOUD_USERNAME,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_DEVICE_ID,
    CONF_CLOUD_USER_ID,
    CONF_CONNECTION_MODE,
    CONF_GW_PASSWORD,
    DEFAULT_SCAN_TIMEOUT,
    DeviceType,
)
from .protocol_ble import detect_device_type, extract_scent_marketing_metadata
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

# Human readable labels
# order is most likely first when auto-detect misses
_DEVICE_TYPE_LABELS: dict[str, str] ={
    DeviceType.SCENT_MARKETING_GW.value: "Scent Marketing GW (EE01 service, password-protected)",
    DeviceType.SCENT_MARKETING_GW_XOR.value: "Scent Marketing GW - WiFi/encrypted variant",
    DeviceType.SCENT_MARKETING_AK.value: "Scent Marketing AK (FFF0 / FFF6)",
    DeviceType.AROMA_LINK.value: "Aroma-Link / JCloud / Cavit / similar",
    DeviceType.TUYA_BLE.value: "ShinePick / Tuya BLE (BT-ivy*)",
    DeviceType.SCENTIMENT.value: "Scentiment Diffuser Air 2",
}

class ScentDiffuserConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Scent Diffuser."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, dict] = {}
        self._cloud_client: AromaLinkCloudClient | None = None
        self._cloud_devices: list = []
        self._selected_ble_address: str | None = None
        self._selected_ble_name: str | None = None
        self._selected_device_type: str | None = None
        self._selected_sm_metadata: dict | None = None
        self._selected_gw_password: str | None = None
        # true when current step reached from push discovery
        # advertisement (async_step_bluetooth). used by manual type step
        # to know which next step to chain after user picks.
        self._auto_detect_failed: bool = False
    def _create_ble_entry(self) -> config_entries.ConfigFlowResult:
        """Build the BLE-mode config entry. Shared by all BLE setup paths."""
        entry_data: dict[str, Any] = {
            CONF_BLE_ADDRESS: self._selected_ble_address,
            CONF_BLE_NAME: self._selected_ble_name,
            CONF_DEVICE_TYPE: self._selected_device_type,
            CONF_CONNECTION_MODE: "ble",
        }
        if self._selected_sm_metadata:
            entry_data["sm_metadata"] = self._selected_sm_metadata
        if self._selected_gw_password:
            entry_data[CONF_GW_PASSWORD] = self._selected_gw_password
        return self.async_create_entry(
            title=self._selected_ble_name or "Scent Diffuser",
            data=entry_data,
        )

    async def async_step_gw_password(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Optional password prompt for Scent Marketing GW devices."""
        if user_input is not None:
            pwd = (user_input.get(CONF_GW_PASSWORD) or "").strip()
            # The firmware accepts up to 4 ASCII chars. We trim silently.
            self._selected_gw_password = pwd[:4] if pwd else None
            return self._create_ble_entry()
        return self.async_show_form(
            step_id="gw_password",
            data_schema=vol.Schema({
                vol.Optional(CONF_GW_PASSWORD, default=""): str,
            }),
            description_placeholders={"device": self._selected_ble_name or ""},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - choose connection method."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["ble_scan", "cloud"],
        )

    # ------------------------------------------------------------------
    # BLE flow
    # ------------------------------------------------------------------

    async def _route_after_type_chosen(self) -> config_entries.ConfigFlowResult:
        """Pick next step based on (possibly user-overriden) family.

        GW devices may be password protected. everything else can create entry directly.
        DeviceType is StrEnum so '==' against '.value' works
        for stored strings and live enum numbers.
        """
        if self._selected_device_type in (
            DeviceType.SCENT_MARKETING_GW.value,
            DeviceType.SCENT_MARKETING_GW_XOR.value,
        ):
            return await self.async_step_gw_password()
        return self._create_ble_entry()

    async def async_step_bluetooth(
            self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        """Handle BT advertisement matched by manifest matchers.

        Preferred entry point: HA pushes fully-cahced advertisement
         (with manufacturer data intact)
        the momement device comes into range, so detection
        is reliable, moreso than a user-triggered scan.
        """
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        adv = discovery_info.advertisement
        name = discovery_info.name or (adv.local_name if adv else "") or ""
        dtype = detect_device_type(name, adv)
        if not name:
            name = f"Scent Diffuser {discovery_info.address[-8:]}"

        self._selected_ble_address = discovery_info.address
        self._selected_ble_name = name
        self._selected_device_type = (dtype.value if dtype else None)
        self._selected_sm_metadata = (
            extract_scent_marketing_metadata(adv)
            if dtype and dtype.value.startswith("scent_marketing")
            else None
        )

    #     Show the device on discovery card so it's clear what we're adding
        self.context["title_placeholders"] = {"name": name}

        if dtype is None:
            self._auto_detect_failed = True
            return await self.async_step_manual_type()
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm adding an auto-discovered device"""
        if user_input is not None:
            return await self._route_after_type_chosen()

        type_label = _DEVICE_TYPE_LABELS.get(
            self._selected_device_type or "", self._selected_device_type or "unknown"
        )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self._selected_ble_name or "",
                "device_type": type_label,
            },
        )

    async def async_step_manual_type(
            self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick a device family when auto-detection fails"""
        if user_input is not None:
            self._selected_device_type = user_input[CONF_DEVICE_TYPE]
            return await self._route_after_type_chosen()

        return self.async_show_form(
            step_id="manual_type",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): vol.In(_DEVICE_TYPE_LABELS),
            }),
            description_placeholders={
                "device": self._selected_ble_name or self._selected_ble_address or "this device",
            },
        )

    async def async_step_ble_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick a device from HA's cached BT discovery.

        Uses bluetooth.async_dsicovered_service_info() instead of running
        a one-shot Bleak scan: HA already maintains a continuously-updated
        advertisement cache (with manufaacturer data) across all of its BT
        adapters and proxies, so we get the full picture instead of a 10s
        snapshot."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input.get("ble_address")
            if address:
                device_info = self._discovered_devices.get(address, {})
                self._selected_ble_address = address
                self._selected_ble_name = device_info.get("name", "")
                self._selected_device_type = device_info.get("device_type")
                self._selected_sm_metadata = device_info.get("sm_metadata")

                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                # If detection failed, send user through manual picker
                #  rather than silently falling back to AROMA_LINK
                # which writes to wrong characteristic for everything else
                if not device_info.get("auto_detected"):
                    self._auto_detect_failed = True
                    return await self.async_step_manual_type()

                return await self._route_after_type_chosen()

        # Pull the cached service-info list from HA's BT integration
        self._discovered_devices = {}
        existing_addresses = {
            entry.unique_id
            for entry in self._async_current_entries(include_ignore=False)
            if entry.unique_id
        }
        for service_info in bluetooth.async_discovered_service_info(self.hass):
            if service_info.address in existing_addresses:
                continue
            adv = service_info.advertisement
            name = service_info.name or (adv.local_name if adv else "") or ""
            dtype = detect_device_type(name, adv)
            if not name and dtype is None:
        #         Skip unnamed devices we can't identify
        # the picker would just be too long anyway
                continue
            if not name:
                name = f"Scent Diffuser {service_info.address[-8:]}"
            sm_meta = (
                extract_scent_marketing_metadata(adv)
                if dtype and dtype.value.startswith("scent_marketing")
                else None
            )
            self._discovered_devices[service_info.address] = {
                "name": name,
                "device_type": dtype.value if dtype else None,
                "rssi": service_info.rssi,
                "auto_detected": dtype is not None,
                "sm_metadata": sm_meta,
            }

        if not self._discovered_devices:
            errors["base"] = "no_devices"
            return self.async_show_form(
                step_id="ble_scan",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Build selection list - auto-detected devices shown first with marker
        device_options = {}
        for addr, info in sorted(
            self._discovered_devices.items(),
            key=lambda x: (not x[1].get("auto_detected", False), -x[1]["rssi"]),
        ):
            short_mac = addr[-8:]
            if info.get("auto_detected"):
                device_options[addr] = f"✓ {info['name']} ({short_mac})"
            else:
                device_options[addr] = f"? {info['name']} ({short_mac})"

        return self.async_show_form(
            step_id="ble_scan",
            data_schema=vol.Schema({
                vol.Required("ble_address"): vol.In(device_options),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Cloud flow
    # ------------------------------------------------------------------

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle cloud login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_CLOUD_USERNAME]
            password = user_input[CONF_CLOUD_PASSWORD]

            self._cloud_client = AromaLinkCloudClient()
            if await self._cloud_client.login(username, password):
                self._cloud_devices = await self._cloud_client.get_devices()
                if self._cloud_devices:
                    # Store credentials for next step
                    self._cloud_username = username
                    self._cloud_password = password
                    return await self.async_step_cloud_device()
                errors["base"] = "no_devices"
            else:
                errors["base"] = "invalid_auth"

            await self._cloud_client.close()

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema({
                vol.Required(CONF_CLOUD_USERNAME): str,
                vol.Required(CONF_CLOUD_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_cloud_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select cloud device."""
        if user_input is not None:
            device_id = user_input[CONF_CLOUD_DEVICE_ID]

            # Find device name
            device_name = "Scent Diffuser"
            for dev in self._cloud_devices:
                if dev.device_id == device_id:
                    device_name = dev.name
                    break

            await self.async_set_unique_id(f"cloud_{device_id}")
            self._abort_if_unique_id_configured()

            data = {
                CONF_DEVICE_TYPE: "aroma_link",
                CONF_CLOUD_USERNAME: self._cloud_username,
                CONF_CLOUD_PASSWORD: self._cloud_password,
                CONF_CLOUD_DEVICE_ID: device_id,
                CONF_CLOUD_USER_ID: self._cloud_client.user_id,
                CONF_CONNECTION_MODE: "cloud",
            }

            if self._cloud_client:
                await self._cloud_client.close()

            return self.async_create_entry(title=device_name, data=data)

        device_options = {
            dev.device_id: f"{dev.name} ({'online' if dev.online else 'offline'})"
            for dev in self._cloud_devices
        }

        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema({
                vol.Required(CONF_CLOUD_DEVICE_ID): vol.In(device_options),
            }),
        )
