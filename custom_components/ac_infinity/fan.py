"""Fan entity for an AC Infinity Bluetooth controller."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.percentage import (
    int_states_in_range,
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .const import DEVICE_MODEL, DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .models import ACInfinityData
from .vendor.ac_infinity_ble import ACInfinityController

SPEED_RANGE = (1, 10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the controller fan entity."""
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ACInfinityFan(data.coordinator, data.device)])


class ACInfinityFan(CoordinatorEntity[ACInfinityDataUpdateCoordinator], FanEntity):
    """Represent the selected AC Infinity controller port as a fan."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator)
        self._device = device
        self._attr_extra_state_attributes = {"physical_port": device.port}
        self._attr_unique_id = f"{device.address}_fan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            name=device.name,
            model=DEVICE_MODEL.get(
                device.state.type, f"Controller type {device.state.type}"
            ),
            manufacturer="AC Infinity",
            sw_version=str(device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )
        self._async_update_attrs()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set speed in ten discrete steps; zero turns the fan off."""
        speed = 0
        if percentage > 0:
            speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self._device.set_speed(speed)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on, optionally at a requested percentage."""
        speed = None
        if percentage is not None:
            speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self._device.turn_on(speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off without overwriting the stored ON speed."""
        await self._device.turn_off()

    @callback
    def _async_update_attrs(self) -> None:
        """Update Home Assistant state from the controller model."""
        self._attr_is_on = self._device.is_on
        speed = self._device.speed
        self._attr_percentage = (
            ranged_value_to_percentage(SPEED_RANGE, speed) if speed > 0 else 0
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle telemetry, command, and periodic model updates."""
        self._async_update_attrs()
        self.async_write_ha_state()
