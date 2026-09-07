"""Shared entity support for adaptive fan control."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, Entity

from .adaptive_control import AdaptiveController
from .const import DEVICE_MODEL, DOMAIN


class AdaptiveEntity(Entity):
    """Base entity attached to the physical AC Infinity controller."""

    _attr_has_entity_name = True

    def __init__(self, adaptive: AdaptiveController, key: str) -> None:
        self.adaptive = adaptive
        self._attr_unique_id = f"{adaptive.device.address}_adaptive_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, adaptive.device.address)},
            name=adaptive.device.name,
            model=DEVICE_MODEL.get(
                adaptive.device.state.type,
                f"Controller type {adaptive.device.state.type}",
            ),
            manufacturer="AC Infinity",
            sw_version=str(adaptive.device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, adaptive.device.address)},
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.adaptive.add_listener(self._handle_adaptive_update))

    def _handle_adaptive_update(self) -> None:
        self.async_write_ha_state()
