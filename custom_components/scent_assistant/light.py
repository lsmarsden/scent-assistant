"""Light entities for Scent Diffuser (Scentiment RGB only)."""
from __future__ import annotations

import logging

from homeassistant.components.light import (
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
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
    """Set up light entities (Scentiment only)."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]
    if device.device_type != DeviceType.SCENTIMENT:
        return
    async_add_entities([ScentimentRgbLight(device, entry)])


class ScentimentRgbLight(ScentEntity, LightEntity):
    """RGB LED on the Scentiment Diffuser Air 2."""

    _attr_name = "LED"
    _attr_icon = "mdi:led-on"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "led")

    @property
    def is_on(self) -> bool | None:
        return self._device.state.rgb_on

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._device.state.rgb_color

    async def async_turn_on(self, **kwargs) -> None:
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if rgb is not None:
            await self._device.set_rgb_color(rgb[0], rgb[1], rgb[2])
        # Always make sure the LED itself is on after a color change.
        await self._device.set_rgb_led(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.set_rgb_led(False)
