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
    SM_GW_DP_CUSTOMIZE_GEAR,
    SM_GW_DP_MODE_TASKS,
)
from .device import ScentDiffuserDevice

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
    else:
        entities.append(WorkDurationNumber(device, entry))
        entities.append(PauseDurationNumber(device, entry))


    async_add_entities(entities)

class WorkDurationNumber(NumberEntity):
    """Spray work duration in seconds."""

    _attr_has_entity_name = True
    _attr_name = "Work Duration"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_work_duration"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._device.state.work_seconds or 10

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_work_duration(int(value))


class PauseDurationNumber(NumberEntity):
    """Pause duration between sprays in seconds."""

    _attr_has_entity_name = True
    _attr_name = "Pause Duration"
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 15
    _attr_native_max_value = 3600
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_pause_duration"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._device.state.pause_seconds or 120

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_pause_duration(int(value))


class GwWorkDurationNumber(NumberEntity):
    """GW customise work duration in seconds (5-35)"""

    _attr_has_entity_name = True
    _attr_name = "Work Duration"
    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 5
    _attr_native_max_value = 35
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_gw_work_duration"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.work_seconds or None

    @property
    def available(self) -> bool:
        return self._device.available and self._device.has_observed_dp(SM_GW_DP_CUSTOMIZE_GEAR)

    async def async_set_native_value(self, value: float) -> None:
        pause = self._device.state.pause_seconds or 60
        await self._device.set_customize(int(value), pause)

class GwPauseDurationNumber(NumberEntity):
    """GW customise pause duration in seconds (60-300)"""

    _attr_has_entity_name = True
    _attr_name = "Pause Duration"
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "s"
    _attr_native_min_value = 60
    _attr_native_max_value = 300
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_gw_pause_duration"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.pause_seconds or None

    @property
    def available(self) -> bool:
        return self._device.available and self._device.has_observed_dp(SM_GW_DP_CUSTOMIZE_GEAR)

    async def async_set_native_value(self, value: float) -> None:
        work = self._device.state.work_seconds or 5
        await self._device.set_customize(work, int(value))


class GwGradeNumber(NumberEntity):
    """Spray grade (1-5) for Scent Marketing GW devices.

    Setting the grade re-emits a DP-4 frame with an all-day all-week task
    and the new grade in the trailing sentinel - matching the iOS app's
    "24-hour mode" behaviour.
    """

    _attr_has_entity_name = True
    _attr_name = "Grade"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_grade"
        self._attr_device_info = device.device_info
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.grade

    @property
    def available(self) -> bool:
        return self._device.available and self._device.has_observed_dp(SM_GW_DP_MODE_TASKS)

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_grade(int(value))


class ScentimentLevelNumber(NumberEntity):
    """Spray intensity level (Scentiment, 1-3)."""

    _attr_has_entity_name = True
    _attr_name = "Level"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_level"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.unique_id)},
        }
        device.register_state_callback(self._on_state_update)

    def _on_state_update(self) -> None:
        if self.hass is None:
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._device.state.level

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_level(int(value))
