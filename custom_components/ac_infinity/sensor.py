"""Sensor entities for an AC Infinity Bluetooth controller."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_MODEL, DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .models import ACInfinityData
from .vendor.ac_infinity_ble import ACInfinityController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up controller environmental sensors."""
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    entities: list[ACInfinitySensor] = [
        TemperatureSensor(data.coordinator, data.device),
        HumiditySensor(data.coordinator, data.device),
    ]
    if data.device.state.version >= 3 and data.device.state.type in (7, 9, 11, 12):
        entities.append(VpdSensor(data.coordinator, data.device))
    async_add_entities(entities)


class ACInfinitySensor(
    CoordinatorEntity[ACInfinityDataUpdateCoordinator], SensorEntity
):
    """Base class for controller sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        """Initialize a sensor."""
        super().__init__(coordinator)
        self._device = device
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Publish notification-driven sensor changes."""
        self._async_update_attrs()
        self.async_write_ha_state()

    @callback
    def _async_update_attrs(self) -> None:
        """Update the native value."""
        raise NotImplementedError


class TemperatureSensor(ACInfinitySensor):
    """Controller temperature sensor."""

    _attr_name = "Temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.address}_temperature"
        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        self._attr_native_value = self._device.temperature


class HumiditySensor(ACInfinitySensor):
    """Controller humidity sensor."""

    _attr_name = "Humidity"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.address}_humidity"
        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        self._attr_native_value = self._device.humidity


class VpdSensor(ACInfinitySensor):
    """Controller vapor-pressure deficit sensor."""

    _attr_name = "VPD"
    _attr_native_unit_of_measurement = UnitOfPressure.KPA
    _attr_device_class = SensorDeviceClass.ATMOSPHERIC_PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.address}_vpd"
        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        self._attr_native_value = self._device.vpd
