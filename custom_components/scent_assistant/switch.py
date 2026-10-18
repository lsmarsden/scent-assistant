"""Switch entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DeviceType,
    LinkPolicy,
)
from .device import ScentDiffuserDevice
from .entity import ScentEntity

GW_TYPES = {DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR}

_LOGGER = logging.getLogger(__name__)

SCENT_MARKETING_TYPES = {
    DeviceType.SCENT_MARKETING_AK,
    DeviceType.SCENT_MARKETING_GW,
    DeviceType.SCENT_MARKETING_GW_XOR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [DiffuserPowerSwitch(device, entry)]

    is_cloud = entry.data.get("connection_mode") == "cloud"
    if device.supports_fan and not is_cloud:
        entities.append(DiffuserFanSwitch(device, entry))

    # Scent Marketing devices expose extra controls when running on BLE.
    if device.device_type in SCENT_MARKETING_TYPES and not is_cloud:
        entities.append(DiffuserLockSwitch(device, entry))
        # AK lamp = control-bitmask bit; GW lamp = DP-11 light. Both are
        # already wired through device.set_lamp().
        entities.append(DiffuserLampSwitch(device, entry))
        if device.device_type == DeviceType.SCENT_MARKETING_AK:
            entities.append(DiffuserFanSwitch(device, entry))

    if device.device_type == DeviceType.AROMA_WAVE and not is_cloud:
        entities.append(AromaWaveModeSwitch(device, entry, mode_idx=1))

    hold_unique_id = f"{device.unique_id}_hold_connection"
    if device.link_policy is LinkPolicy.PERSISTENT:
        entities.append(DiffuserHoldConnectionSwitch(device, entry))
    elif stale := er.async_get(hass).async_get_entity_id("switch", DOMAIN, hold_unique_id):
        # The device was switched to on-demand in the options flow.
        er.async_get(hass).async_remove(stale)

    async_add_entities(entities)


class DiffuserPowerSwitch(ScentEntity, SwitchEntity):
    """Power on/off switch for the diffuser."""

    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "power")

    @property
    def is_on(self) -> bool | None:
        return self._device.state.power

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_power(False)


class DiffuserLockSwitch(ScentEntity, SwitchEntity):
    """Child-lock switch (Scent Marketing devices)."""

    _attr_name = "Child lock"
    _attr_icon = "mdi:lock"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "lock")
        # GW devices report it in their state push; AK has no read-back.
        if device.device_type in GW_TYPES:
            self._requires = "lock"

    @property
    def is_on(self) -> bool | None:
        return self._device.state.lock

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_lock(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_lock(False)


class DiffuserLampSwitch(ScentEntity, SwitchEntity):
    """Auxiliary LED / lamp switch (Scent Marketing AK family)."""

    _attr_name = "Lamp"
    _attr_icon = "mdi:lightbulb"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "lamp")
        # GW devices report it in their state push; AK has no read-back.
        if device.device_type in GW_TYPES:
            self._requires = "light_on"

    @property
    def is_on(self) -> bool | None:
        return self._device.state.light_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_lamp(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_lamp(False)


class DiffuserFanSwitch(ScentEntity, SwitchEntity):
    """Fan on/off switch (Aroma-Link only, requires Bluetooth)."""

    _attr_name = "Fan"
    _attr_icon = "mdi:fan"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._is_cloud_only = entry.data.get("connection_mode") == "cloud"
        super().__init__(device, "fan")

    @property
    def is_on(self) -> bool | None:
        return self._device.state.fan

    @property
    def available(self) -> bool:
        if self._is_cloud_only:
            return False
        return self._device.connection_mode == "ble"

    @property
    def extra_state_attributes(self) -> dict:
        if self._is_cloud_only:
            return {"note": "Fan control requires Bluetooth connection"}
        return {}

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_fan(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_fan(False)

class AromaWaveModeSwitch(ScentEntity, SwitchEntity):
    """Toggle one of the AromaWave scheduled modes on or off."""
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry, mode_idx: int) -> None:
        self._mode_idx = mode_idx
        self._attr_name = f"Mode {mode_idx} enabled"
        super().__init__(device, f"aromawave_mode_{mode_idx}_enabled")

    @property
    def is_on(self) -> bool | None:
        mode = self._device.state.aromawave_modes.get(self._mode_idx)
        return mode.enabled if mode else None

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_aromawave_mode(self._mode_idx, enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_aromawave_mode(self._mode_idx, enabled=False)


class DiffuserHoldConnectionSwitch(ScentEntity, SwitchEntity):
    """Whether HA holds the BLE connection. Turn off to let the vendor app
    connect; HA connects briefly for commands meanwhile and takes the
    connection back after 10 minutes."""

    _attr_name = "Hold connection"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "hold_connection")

    @property
    def is_on(self) -> bool | None:
        return self._device.link_held

    @property
    def available(self) -> bool:
        # Always: it's how you get the connection back after the vendor
        # app has held the device.
        return True

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.set_hold_connection(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_hold_connection(False)
