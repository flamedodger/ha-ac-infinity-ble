"""Adaptive-control target and tuning numbers."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .adaptive_entity import AdaptiveEntity
from .const import DOMAIN
from .models import ACInfinityData


@dataclass(frozen=True, kw_only=True)
class AdaptiveNumberDescription(NumberEntityDescription):
    """Describe one persisted adaptive number."""


NUMBERS = (
    AdaptiveNumberDescription(
        key="day_temperature_target",
        name="Day temperature target",
        native_min_value=15,
        native_max_value=35,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    AdaptiveNumberDescription(
        key="night_temperature_target",
        name="Night temperature target",
        native_min_value=15,
        native_max_value=35,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    AdaptiveNumberDescription(
        key="day_humidity_target",
        name="Day maximum humidity",
        native_min_value=35,
        native_max_value=90,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    AdaptiveNumberDescription(
        key="night_humidity_target",
        name="Night maximum humidity",
        native_min_value=35,
        native_max_value=90,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    AdaptiveNumberDescription(
        key="day_vpd_target",
        name="Day leaf VPD target",
        native_min_value=0.4,
        native_max_value=2.0,
        native_step=0.05,
        native_unit_of_measurement=UnitOfPressure.KPA,
    ),
    AdaptiveNumberDescription(
        key="night_vpd_target",
        name="Night leaf VPD target",
        native_min_value=0.4,
        native_max_value=2.0,
        native_step=0.05,
        native_unit_of_measurement=UnitOfPressure.KPA,
    ),
    AdaptiveNumberDescription(
        key="leaf_temperature_offset",
        name="Leaf temperature offset",
        native_min_value=-8,
        native_max_value=3,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    AdaptiveNumberDescription(
        key="minimum_fan_level",
        name="Minimum fan level",
        native_min_value=0,
        native_max_value=10,
        native_step=1,
    ),
    AdaptiveNumberDescription(
        key="maximum_fan_level",
        name="Maximum fan level",
        native_min_value=0,
        native_max_value=10,
        native_step=1,
    ),
    AdaptiveNumberDescription(
        key="manual_fan_level",
        name="Manual fan level",
        native_min_value=0,
        native_max_value=10,
        native_step=1,
    ),
    AdaptiveNumberDescription(
        key="temperature_hysteresis",
        name="Temperature hysteresis",
        native_min_value=0.2,
        native_max_value=3,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    AdaptiveNumberDescription(
        key="humidity_hysteresis",
        name="Humidity hysteresis",
        native_min_value=1,
        native_max_value=10,
        native_step=0.5,
        native_unit_of_measurement=PERCENTAGE,
    ),
    AdaptiveNumberDescription(
        key="vpd_hysteresis",
        name="VPD hysteresis",
        native_min_value=0.02,
        native_max_value=0.3,
        native_step=0.01,
        native_unit_of_measurement=UnitOfPressure.KPA,
    ),
    AdaptiveNumberDescription(
        key="temperature_per_level",
        name="Temperature excess per level",
        native_min_value=0.2,
        native_max_value=3,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
    ),
    AdaptiveNumberDescription(
        key="humidity_per_level",
        name="Humidity excess per level",
        native_min_value=1,
        native_max_value=10,
        native_step=0.5,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
    ),
    AdaptiveNumberDescription(
        key="vpd_per_level",
        name="VPD deficit per level",
        native_min_value=0.02,
        native_max_value=0.3,
        native_step=0.01,
        native_unit_of_measurement=UnitOfPressure.KPA,
        entity_registry_enabled_default=False,
    ),
    AdaptiveNumberDescription(
        key="command_interval_minutes",
        name="Minimum command interval",
        native_min_value=1,
        native_max_value=30,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    AdaptiveNumberDescription(
        key="minimum_run_minutes",
        name="Minimum run time",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    AdaptiveNumberDescription(
        key="sensor_stale_minutes",
        name="Sensor stale after",
        native_min_value=2,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AdaptiveNumber(data.adaptive, description) for description in NUMBERS
    )


class AdaptiveNumber(AdaptiveEntity, NumberEntity):
    """Persisted adaptive numeric setting."""

    entity_description: AdaptiveNumberDescription

    def __init__(self, adaptive, description: AdaptiveNumberDescription) -> None:
        super().__init__(adaptive, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        return float(self.adaptive.options[self.entity_description.key])

    async def async_set_native_value(self, value: float) -> None:
        if self.entity_description.native_step == 1:
            value = int(value)
        await self.adaptive.async_set_option(self.entity_description.key, value)
