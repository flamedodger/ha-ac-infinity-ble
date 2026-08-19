"""Connection and state coordinator for AC Infinity Bluetooth."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_SECONDS
from .vendor.ac_infinity_ble import ACInfinityController, CallbackType, DeviceInfo


_LOGGER = logging.getLogger(__name__)
SHUTDOWN_TIMEOUT = 15


class ACInfinityDataUpdateCoordinator(DataUpdateCoordinator[DeviceInfo]):
    """Keep the BLE session healthy and publish notification-driven updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        entry: ConfigEntry,
        controller: ACInfinityController,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_SECONDS),
        )
        self.controller = controller
        self._remove_controller_callback = controller.register_callback(
            self._handle_controller_update
        )

    @callback
    def _handle_controller_update(
        self, state: DeviceInfo, _update_type: CallbackType
    ) -> None:
        """Publish advertisements, telemetry, command ACKs, and model reads."""
        # async_set_updated_data() resets the periodic refresh timer. Telemetry
        # can arrive several times a second, so using it here would starve model
        # polls forever. Publish listeners directly and keep the poll schedule.
        self.data = state
        self.async_update_listeners()

    async def _async_update_data(self) -> DeviceInfo:
        """Refresh model state and reconnect when needed."""
        if not self.controller.connected:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.controller.address, connectable=True
            )
            if ble_device is None:
                raise UpdateFailed("Controller is not in Bluetooth range")
            self.controller.set_ble_device(ble_device)
        try:
            await self.controller.update()
        except (BleakError, TimeoutError, EOFError) as exc:
            raise UpdateFailed(f"Bluetooth update failed: {exc}") from exc
        return self.controller.state

    async def async_shutdown(self) -> None:
        """Remove callbacks and release the controller for other BLE clients."""
        try:
            self._remove_controller_callback()
        finally:
            # A callback cleanup failure must never strand the GATT connection
            # or prevent DataUpdateCoordinator teardown during reload.
            try:
                try:
                    async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                        await self.controller.stop()
                except TimeoutError:
                    _LOGGER.warning(
                        "Timed out releasing AC Infinity BLE session; "
                        "abandoning poisoned client so reload can continue"
                    )
                    self.controller.abandon()
            finally:
                await super().async_shutdown()
