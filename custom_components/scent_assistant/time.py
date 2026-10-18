"""Time entities for Scent Diffuser."""
from __future__ import annotations

import logging
from datetime import time

from homeassistant.components.time import TimeEntity
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
    """Set up time entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]
    if device.device_type == DeviceType.SCENTIMENT:
        return

    if device.device_type == DeviceType.AROMA_WAVE:
        async_add_entities([
            AromaWaveModeStartTime(device, entry, mode_idx=1),
            AromaWaveModeEndTime(device, entry, mode_idx=1),
        ])

    async_add_entities([
        DiffuserStartTime(device, entry),
        DiffuserEndTime(device, entry),
    ])


class DiffuserStartTime(ScentEntity, TimeEntity):
    """Start time for the daily spray schedule."""

    _attr_name = "Start Time"
    _attr_icon = "mdi:clock-start"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "start_time")

    @property
    def native_value(self) -> time | None:
        return time(self._device.state.start_hour, self._device.state.start_minute)

    async def async_set_value(self, value: time) -> None:
        """Set the start time and write schedule to device."""
        self._device.state.start_hour = value.hour
        self._device.state.start_minute = value.minute
        await self._device.set_schedule(
            weekday_mask=0x7F,  # all days
            start_hour=value.hour,
            start_minute=value.minute,
            end_hour=self._device.state.end_hour,
            end_minute=self._device.state.end_minute,
            work_seconds=self._device.state.work_seconds or 10,
            pause_seconds=self._device.state.pause_seconds or 120,
        )


class DiffuserEndTime(ScentEntity, TimeEntity):
    """End time for the daily spray schedule."""

    _attr_name = "End Time"
    _attr_icon = "mdi:clock-end"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "end_time")

    @property
    def native_value(self) -> time | None:
        return time(self._device.state.end_hour, self._device.state.end_minute)

    async def async_set_value(self, value: time) -> None:
        """Set the end time and write schedule to device."""
        self._device.state.end_hour = value.hour
        self._device.state.end_minute = value.minute
        await self._device.set_schedule(
            weekday_mask=0x7F,  # all days
            start_hour=self._device.state.start_hour,
            start_minute=self._device.state.start_minute,
            end_hour=value.hour,
            end_minute=value.minute,
            work_seconds=self._device.state.work_seconds or 10,
            pause_seconds=self._device.state.pause_seconds or 120,
        )

class AromaWaveModeStartTime(ScentEntity, TimeEntity):
    """Start time for an AromaWave scheduled mode."""

    _attr_icon = "mdi:clock-start"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry, mode_idx: int) -> None:
        self._mode_idx = mode_idx
        self._attr_name = f"Mode {mode_idx} start time"
        super().__init__(device, f"aromawave_mode_{mode_idx}_start")

    @property
    def native_value(self) -> time | None:
        mode = self._device.state.aromawave_modes.get(self._mode_idx)
        if mode is None or mode.start_hour is None or mode.start_minute is None:
            return None
        return time(mode.start_hour, mode.start_minute)

    async def async_set_value(self, value: time) -> None:
        await self._device.set_aromawave_mode(
            self._mode_idx, start_hour=value.hour, start_minute=value.minute,
        )

class AromaWaveModeEndTime(ScentEntity, TimeEntity):
    """End time for an AromaWave scheduled mode."""

    _attr_icon = "mdi:clock-end"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry, mode_idx: int) -> None:
        self._mode_idx = mode_idx
        self._attr_name = f"Mode {mode_idx} end time"
        super().__init__(device, f"aromawave_mode_{mode_idx}_end")

    @property
    def native_value(self) -> time | None:
        mode = self._device.state.aromawave_modes.get(self._mode_idx)
        if mode is None or mode.end_hour is None or mode.end_minute is None:
            return None
        return time(mode.end_hour, mode.end_minute)

    async def async_set_value(self, value: time) -> None:
        await self._device.set_aromawave_mode(
            self._mode_idx, end_hour=value.hour, end_minute=value.minute,
        )

