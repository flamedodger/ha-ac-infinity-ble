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
TELEMETRY_RECONNECT_SECONDS = 120
TELEMETRY_WAIT_SECONDS = 10


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
        self._remove_bluetooth_callback = bluetooth.async_register_callback(
            hass,
            self._handle_bluetooth_advertisement,
            {
                "address": controller.address,
                "manufacturer_id": 2306,
                "connectable": True,
            },
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_complete = False

    @callback
    def _handle_bluetooth_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Update controller state from a Home Assistant BLE advertisement."""
        try:
            self.controller.set_ble_device_and_advertisement_data(
                service_info.device, service_info
            )
        except (UnicodeDecodeError, ValueError):
            _LOGGER.debug(
                "Ignoring malformed AC Infinity advertisement from %s",
                service_info.address,
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
            if self.controller.telemetry_is_stale(TELEMETRY_RECONNECT_SECONDS):
                _LOGGER.warning(
                    "AC Infinity climate telemetry is stale; reconnecting %s",
                    self.controller.name,
                )
                self.controller.invalidate_climate()
                await self.controller.wait_for_advertisement_telemetry(
                    TELEMETRY_WAIT_SECONDS
                )
        except (BleakError, TimeoutError, EOFError) as exc:
            raise UpdateFailed(f"Bluetooth update failed: {exc}") from exc
        return self.controller.state

    async def async_shutdown(self) -> None:
        """Remove callbacks and release the controller for other BLE clients."""
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return

            try:
                # Home Assistant can ask a coordinator to shut down more than once
                # while a failed setup or reload is unwinding. Clear each remover
                # before calling it so callback cleanup is safely repeatable.
                remove_bluetooth = self._remove_bluetooth_callback
                self._remove_bluetooth_callback = None
                if remove_bluetooth is not None:
                    try:
                        remove_bluetooth()
                    except ValueError:
                        _LOGGER.debug("Bluetooth callback was already removed")
            finally:
                try:
                    remove_controller = self._remove_controller_callback
                    self._remove_controller_callback = None
                    if remove_controller is not None:
                        remove_controller()
                finally:
                    # A callback cleanup failure must never strand the GATT
                    # connection or prevent coordinator teardown during reload.
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
                        self._shutdown_complete = True
