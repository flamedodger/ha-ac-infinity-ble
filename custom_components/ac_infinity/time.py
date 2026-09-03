"""Adaptive-control light schedule times."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .adaptive import parse_time
from .adaptive_entity import AdaptiveEntity
from .const import DOMAIN
from .models import ACInfinityData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AdaptiveTime(data.adaptive, "lights_on", "Lights on"),
            AdaptiveTime(data.adaptive, "lights_off", "Lights off"),
        ]
    )


class AdaptiveTime(AdaptiveEntity, TimeEntity):
    """Persist a daily schedule boundary."""

    def __init__(self, adaptive, key: str, name: str) -> None:
        super().__init__(adaptive, key)
        self._key = key
        self._attr_name = name

    @property
    def native_value(self) -> time:
        return parse_time(str(self.adaptive.options[self._key]))

    async def async_set_value(self, value: time) -> None:
        await self.adaptive.async_set_option(self._key, value.isoformat())
