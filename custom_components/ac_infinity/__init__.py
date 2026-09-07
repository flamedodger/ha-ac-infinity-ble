"""Local Bluetooth support for AC Infinity controllers."""

from __future__ import annotations

import asyncio
import logging

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_PORT, DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .models import ACInfinityData
from .vendor.ac_infinity_ble import ACInfinityController, DeviceInfo

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.FAN]
_LOGGER = logging.getLogger(__name__)
SETUP_TIMEOUT = 55
STALE_CONNECTION_TIMEOUT = 10


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an AC Infinity controller from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    try:
        async with asyncio.timeout(STALE_CONNECTION_TIMEOUT):
            await close_stale_connections_by_address(address.upper())
    except Exception:  # noqa: BLE001 - stale cleanup is best effort
        _LOGGER.warning(
            "Could not complete stale BLE connection cleanup for %s",
            address,
            exc_info=True,
        )

    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Could not find AC Infinity controller at {address}; close the phone app"
        )

    device_info = DeviceInfo(**entry.data[CONF_SERVICE_DATA])
    controller = ACInfinityController(
        ble_device, state=device_info, port=entry.options.get(CONF_PORT, 1)
    )
    coordinator = ACInfinityDataUpdateCoordinator(
        hass, _LOGGER, entry, controller
    )
    try:
        async with asyncio.timeout(SETUP_TIMEOUT):
            await coordinator.async_config_entry_first_refresh()
    except TimeoutError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(
            f"Timed out connecting to AC Infinity controller at {address}"
        ) from err
    except Exception:
        await coordinator.async_shutdown()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, controller, coordinator
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Release the old BLE session and reload with the newly selected port."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities and cleanly release Bluetooth."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data: ACInfinityData = hass.data[DOMAIN].pop(entry.entry_id)
    await data.coordinator.async_shutdown()
    return True
