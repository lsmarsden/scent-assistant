"""Base entity shared by every Scent Assistant platform."""
from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .device import ScentDiffuserDevice


class ScentEntity(Entity):
    """Identity, device info, state subscription and availability.

    Subclasses add their value and command. `_requires` names a
    DiffuserState field the device must have reported before the entity is
    available, for values HA can't know until the first push.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _requires: str | None = None

    def __init__(self, device: ScentDiffuserDevice, key: str) -> None:
        self._device = device
        self._attr_unique_id = f"{device.unique_id}_{key}"
        self._attr_device_info = device.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._device.register_state_callback(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        if not self._device.available:
            return False
        return self._requires is None or getattr(self._device.state, self._requires) is not None
