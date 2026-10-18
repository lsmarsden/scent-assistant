"""Sensor entities for Scent Diffuser."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    DeviceType,
)
from .device import ScentDiffuserDevice
from .entity import ScentEntity

_LOGGER = logging.getLogger(__name__)

SCENT_MARKETING_TYPES = {
    DeviceType.SCENT_MARKETING_AK,
    DeviceType.SCENT_MARKETING_GW,
    DeviceType.SCENT_MARKETING_GW_XOR,
}
GW_TYPES = {DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    device: ScentDiffuserDevice = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        DiffuserStatusSensor(device, entry),
        DiffuserConnectionSensor(device, entry),
    ]
    if device.device_type == DeviceType.SCENTIMENT:
        entities.append(DiffuserBatterySensor(device, entry))

    if device.device_type in GW_TYPES:
        entities.append(DiffuserBatterySensor(device, entry))
        entities.append(DiffuserOilSensor(device, entry))

    if device.device_type in SCENT_MARKETING_TYPES:
        entities.append(DiffuserDetectionDiagnostic(device, entry))

    async_add_entities(entities)


class DiffuserStatusSensor(ScentEntity, SensorEntity):
    """Shows the current spray cycle phase."""

    _attr_name = "Status"
    _attr_icon = "mdi:spray"

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "status")

    @property
    def native_value(self) -> str:
        return self._device.state.phase

    @property
    def extra_state_attributes(self) -> dict:
        state = self._device.state
        if self._device.device_type == DeviceType.SCENTIMENT:
            return {"level": state.level}
        return {
            "work_seconds": state.work_seconds,
            "pause_seconds": state.pause_seconds,
            "start_time": f"{state.start_hour:02d}:{state.start_minute:02d}",
            "end_time": f"{state.end_hour:02d}:{state.end_minute:02d}",
        }


class DiffuserBatterySensor(ScentEntity, SensorEntity):
    """Battery level (Scentiment only)."""

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "battery")

    @property
    def native_value(self) -> int | None:
        return self._device.state.battery

    _requires = "battery"


class DiffuserOilSensor(ScentEntity, SensorEntity):
    """Remaining fragrance oil percentage (Scent Marketing GW family)."""

    _attr_name = "Oil remaining"
    _attr_icon = "mdi:water-percent"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "oil")

    @property
    def native_value(self) -> int | None:
        return self._device.state.oil_remaining

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        if self._device.state.oil_remaining_ml is not None:
            attrs["remaining_ml"] = self._device.state.oil_remaining_ml
        if self._device.state.oil_total_ml is not None:
            attrs["total_ml"] = self._device.state.oil_total_ml
        return attrs

    _requires = "oil_remaining"


class DiffuserDetectionDiagnostic(ScentEntity, SensorEntity):
    """Exposes Scent Marketing detection metadata as a diagnostic entity.

    The state is the detected protocol family; attributes carry the raw
    advertisement bytes plus the manufacturer ID, PID and WiFi flag. This
    is what a non-technical reporter screenshots when something doesn't
    work — no need to dig through HA's text logs.
    """

    _attr_name = "Detected family"
    _attr_icon = "mdi:bluetooth-settings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "sm_detection")

    @property
    def native_value(self) -> str:
        return self._device.device_type.value

    @property
    def available(self) -> bool:
        return True  # diagnostics stay readable when the device is unreachable

    @property
    def extra_state_attributes(self) -> dict:
        meta = self._device.sm_metadata or {}
        return {
            "manufacturer_id": meta.get("mfr_id"),
            "pid": meta.get("pid"),
            "wifi_flag": meta.get("wifi_flag"),
            "heartbeat": meta.get("heartbeat"),
            "mac_from_advertisement": meta.get("mac_from_adv"),
            "raw_advertisement_hex": meta.get("raw_hex"),
            "model": self._device.model_name,
            "firmware_version": self._device.state.firmware_version,
            "password_required": self._device.state.password_required,
        }


class DiffuserConnectionSensor(ScentEntity, SensorEntity):
    """Shows the current connection mode (BLE/Cloud/Offline)."""

    _attr_name = "Connection"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_registry_enabled_default = False

    def __init__(self, device: ScentDiffuserDevice, entry: ConfigEntry) -> None:
        super().__init__(device, "connection")

    @property
    def native_value(self) -> str:
        return self._device.connection_mode

    @property
    def available(self) -> bool:
        return True  # reports "offline" itself
