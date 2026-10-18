"""Button entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DeviceType
from .device import ScentDiffuserDevice
from .entity import ScentEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]
    if device.device_type == DeviceType.SCENTIMENT:
        return

    async_add_entities([
        TimeSyncButton(device, entry),
    ])


class TimeSyncButton(ScentEntity, ButtonEntity):
    """Button to manually sync the device clock."""

    _attr_name = "Sync Time"
    _attr_icon = "mdi:clock-check"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "time_sync")

    @property
    def available(self) -> bool:
        return self._device.connection_mode == "ble"

    async def async_press(self) -> None:
        """Sync the device clock to current local time."""
        success = await self._device.sync_time()
        if success:
            _LOGGER.info("Time synced to %s", self._device.name)
        else:
            _LOGGER.warning("Time sync failed for %s", self._device.name)
