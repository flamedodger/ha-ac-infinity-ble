"""Runtime for the optional adaptive speed controller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .adaptive import DEFAULTS, AdaptiveResult, calculate, is_daytime, parse_time
from .coordinator import ACInfinityDataUpdateCoordinator
from .vendor.ac_infinity_ble import ACInfinityController

_LOGGER = logging.getLogger(__name__)


class AdaptiveController:
    """Own adaptive settings, diagnostics, and rate-limited fan commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.device = device
        self.options: dict[str, Any] = {**DEFAULTS, **entry.options}
        self.result = AdaptiveResult(0, "disabled", None, None)
        self.sensors_healthy = False
        self.daytime = False
        self.last_climate_update: datetime | None = None
        self.last_command: datetime | None = None
        self.last_started: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._command_lock = asyncio.Lock()
        self._apply_task: asyncio.Task[None] | None = None
        self._remove_coordinator_listener = coordinator.async_add_listener(
            self._handle_update
        )
        self._handle_update()

    @property
    def current_level(self) -> int:
        """Return the controller's actual level, with off represented as zero."""
        return self.device.speed if self.device.is_on else 0

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update listener."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _handle_update(self) -> None:
        now = dt_util.now()
        temperature = self.device.temperature
        humidity = self.device.humidity
        readings_valid = (
            temperature is not None
            and humidity is not None
            and 0 < temperature < 60
            and 0 <= humidity <= 100
        )
        if readings_valid:
            self.last_climate_update = now

        stale_after = timedelta(minutes=float(self.options["sensor_stale_minutes"]))
        self.sensors_healthy = bool(
            self.last_climate_update and now - self.last_climate_update <= stale_after
        )
        self.daytime = is_daytime(
            now.time(),
            parse_time(str(self.options["lights_on"])),
            parse_time(str(self.options["lights_off"])),
        )
        self.result = calculate(
            temperature=temperature if readings_valid else None,
            humidity=humidity if readings_valid else None,
            current_level=self.current_level,
            daytime=self.daytime,
            options=self.options,
            sensors_healthy=self.sensors_healthy,
        )
        if not bool(self.options["adaptive_enabled"]):
            self.result = AdaptiveResult(
                self.current_level,
                "disabled",
                self.result.leaf_temperature,
                self.result.leaf_vpd,
            )
        self._notify_listeners()
        if bool(self.options["adaptive_enabled"]) and (
            self._apply_task is None or self._apply_task.done()
        ):
            self._apply_task = self.hass.async_create_task(
                self._async_apply_requested_level(),
                "AC Infinity adaptive speed update",
            )

    async def async_set_option(self, key: str, value: Any) -> None:
        """Persist an adaptive setting and immediately recalculate."""
        self.options[key] = value
        updated = dict(self.entry.options)
        updated[key] = value
        self.hass.config_entries.async_update_entry(self.entry, options=updated)
        self._handle_update()

    async def _async_apply_requested_level(self) -> None:
        async with self._command_lock:
            if not bool(self.options["adaptive_enabled"]):
                return
            now = dt_util.now()
            interval = timedelta(
                minutes=float(self.options["command_interval_minutes"])
            )
            if self.last_command and now - self.last_command < interval:
                return

            current = self.current_level
            requested = self.result.requested_level
            if requested == current:
                return

            next_level = current + (1 if requested > current else -1)
            try:
                if next_level <= 0:
                    minimum_run = timedelta(
                        minutes=float(self.options["minimum_run_minutes"])
                    )
                    if self.last_started and now - self.last_started < minimum_run:
                        return
                    await self.device.turn_off()
                else:
                    await self.device.set_speed(next_level)
                    if current == 0:
                        self.last_started = now
            except Exception:
                _LOGGER.warning(
                    "Adaptive control could not change %s from level %s toward %s",
                    self.device.name,
                    current,
                    requested,
                    exc_info=True,
                )
                return
            self.last_command = now
            _LOGGER.debug(
                "Adaptive control moved %s from level %s to %s (target %s: %s)",
                self.device.name,
                current,
                next_level,
                requested,
                self.result.reason,
            )

    async def async_shutdown(self) -> None:
        """Stop adaptive callbacks before the BLE coordinator shuts down."""
        self._remove_coordinator_listener()
        if self._apply_task and not self._apply_task.done():
            self._apply_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._apply_task
        self._listeners.clear()
