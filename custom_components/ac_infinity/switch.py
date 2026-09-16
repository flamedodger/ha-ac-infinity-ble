"""Adaptive-control switches."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities(
        [
            AdaptiveSwitch(data.adaptive, "adaptive_enabled", "Adaptive control"),
            AdaptiveSwitch(data.adaptive, "manual_override", "Manual override"),
        ]
    )


class AdaptiveSwitch(AdaptiveEntity, SwitchEntity):
    """Persisted adaptive Boolean setting."""

    def __init__(self, adaptive, key: str, name: str) -> None:
        super().__init__(adaptive, key)
        self._key = key
        self._attr_name = name

    @property
    def is_on(self) -> bool:
        return bool(self.adaptive.options[self._key])

    async def async_turn_on(self, **kwargs) -> None:
        await self.adaptive.async_set_option(self._key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.adaptive.async_set_option(self._key, False)
