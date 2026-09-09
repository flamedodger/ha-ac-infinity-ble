"""Adaptive-control health sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .adaptive_entity import AdaptiveEntity
from .const import DOMAIN
from .models import ACInfinityData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ClimateSensorsHealthy(data.adaptive)])


class ClimateSensorsHealthy(AdaptiveEntity, BinarySensorEntity):
    """Report whether adaptive control has usable current climate data."""

    _attr_name = "Adaptive climate sensors healthy"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, adaptive) -> None:
        super().__init__(adaptive, "climate_sensors_healthy")

    @property
    def is_on(self) -> bool:
        return self.adaptive.sensors_healthy

    @property
    def extra_state_attributes(self):
        last_update = self.adaptive.last_climate_update
        return {"last_climate_update": last_update.isoformat() if last_update else None}
