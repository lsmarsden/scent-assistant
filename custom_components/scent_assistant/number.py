"""Number entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DeviceType,
)
from .device import ScentDiffuserDevice
from .entity import ScentEntity

_LOGGER = logging.getLogger(__name__)

GW_TYPES = {DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    if device.device_type == DeviceType.SCENTIMENT:
        async_add_entities([ScentimentLevelNumber(device, entry)])
        return

    entities: list[NumberEntity] = []
    if device.device_type in GW_TYPES:
        # GW devices use DP-15 with hard 5-35s / 60-300s bounds, not the
        # legacy schedule fields the AromaLink/Tuya entities write
        entities.append(GwWorkDurationNumber(device, entry))
        entities.append(GwPauseDurationNumber(device, entry))
        entities.append(GwGradeNumber(device, entry))
    elif device.device_type == DeviceType.AROMA_WAVE:
        entities.append(AromaWaveModeWorkNumber(device, entry, mode_idx=1))
        entities.append(AromaWaveModePauseNumber(device, entry, mode_idx=1))
    else:
        entities.append(WorkDurationNumber(device, entry))
        entities.append(PauseDurationNumber(device, entry))


    async_add_entities(entities)

class WorkDurationNumber(ScentEntity, NumberEntity):
    """Spray work duration in seconds."""

    _attr_name = "Work Duration"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "work_duration")

    @property
    def native_value(self) -> float:
        return self._device.state.work_seconds or 10

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_work_duration(int(value))


class PauseDurationNumber(ScentEntity, NumberEntity):
    """Pause duration between sprays in seconds."""

    _attr_name = "Pause Duration"
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 15
    _attr_native_max_value = 3600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "pause_duration")

    @property
    def native_value(self) -> float:
        return self._device.state.pause_seconds or 120

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_pause_duration(int(value))


class GwWorkDurationNumber(ScentEntity, NumberEntity):
    """GW customise work duration in seconds (5-35)"""

    _attr_name = "Work Duration"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 35
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "gw_work_duration")

    @property
    def native_value(self) -> float | None:
        return self._device.state.work_seconds or None

    _requires = "work_seconds"

    async def async_set_native_value(self, value: float) -> None:
        pause = self._device.state.pause_seconds or 60
        await self._device.set_customize(int(value), pause)

class GwPauseDurationNumber(ScentEntity, NumberEntity):
    """GW customise pause duration in seconds (60-300)"""

    _attr_name = "Pause Duration"
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 60
    _attr_native_max_value = 300
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "gw_pause_duration")

    @property
    def native_value(self) -> float | None:
        return self._device.state.pause_seconds or None

    _requires = "pause_seconds"

    async def async_set_native_value(self, value: float) -> None:
        work = self._device.state.work_seconds or 5
        await self._device.set_customize(work, int(value))


class GwGradeNumber(ScentEntity, NumberEntity):
    """Spray grade (1-5) for Scent Marketing GW devices.

    Setting the grade re-emits a DP-4 frame with an all-day all-week task
    and the new grade in the trailing sentinel - matching the iOS app's
    "24-hour mode" behaviour.
    """

    _attr_name = "Grade"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "grade")

    @property
    def native_value(self) -> float | None:
        return self._device.state.grade

    _requires = "grade"

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_grade(int(value))


class ScentimentLevelNumber(ScentEntity, NumberEntity):
    """Spray intensity level (Scentiment, 1-3)."""

    _attr_name = "Level"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "level")

    @property
    def native_value(self) -> float | None:
        return self._device.state.level

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_level(int(value))

class AromaWaveModeWorkNumber(ScentEntity, NumberEntity):
    """Work-spray duration for an AromaWave scheduled mode (seconds)."""

    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 1
    _attr_native_max_value = 600
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry, mode_idx: int) -> None:
        self._mode_idx = mode_idx
        self._attr_name = f"Mode {mode_idx} work duration"
        super().__init__(device, f"aromawave_mode_{mode_idx}_work")

    @property
    def native_value(self) -> float | None:
        mode = self._device.state.aromawave_modes.get(self._mode_idx)
        return mode.work_seconds if mode else None

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_aromawave_mode(self._mode_idx, work_seconds=int(value))

class AromaWaveModePauseNumber(ScentEntity, NumberEntity):
    """Pause duration between sprays for an AromaWave scheduled mode (seconds)."""

    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 1
    _attr_native_max_value = 3600
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry, mode_idx: int) -> None:
        self._mode_idx = mode_idx
        self._attr_name = f"Mode {mode_idx} pause duration"
        super().__init__(device, f"aromawave_mode_{mode_idx}_pause")

    @property
    def native_value(self) -> float | None:
        mode = self._device.state.aromawave_modes.get(self._mode_idx)
        return mode.pause_seconds if mode else None

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_aromawave_mode(self._mode_idx, pause_seconds=int(value))