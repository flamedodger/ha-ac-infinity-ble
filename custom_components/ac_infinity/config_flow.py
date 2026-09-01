"""Config flow for the local AC Infinity Bluetooth integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_process_advertisements,
)
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .discovery import device_from_service_info


DISCOVERY_TIMEOUT = 12


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover and validate AC Infinity Bluetooth controllers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle discovery initiated by Home Assistant Bluetooth."""
        device = device_from_service_info(discovery_info)
        if device is None:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": device.name}
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user select a safely filtered discovered controller."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices.get(address)
            if discovery_info is None:
                errors["base"] = "device_disappeared"
            else:
                device = device_from_service_info(discovery_info)
                if device is None:
                    errors["base"] = "device_disappeared"
                else:
                    await self.async_set_unique_id(
                        discovery_info.address, raise_on_progress=False
                    )
                    self._abort_if_unique_id_configured()
                    # Do not connect and then disconnect inside the flow. The
                    # entry setup can reuse this fresh discovery and establish
                    # the one persistent session without a second BLE scan.
                    return self.async_create_entry(
                        title=device.name,
                        data={
                            CONF_ADDRESS: discovery_info.address,
                            CONF_SERVICE_DATA: asdict(device),
                        },
                    )

        self._collect_discovered_devices()
        if not self._discovered_devices:
            try:
                discovery = await async_process_advertisements(
                    self.hass,
                    lambda service_info: (
                        device_from_service_info(service_info) is not None
                    ),
                    {"connectable": True},
                    BluetoothScanningMode.ACTIVE,
                    DISCOVERY_TIMEOUT,
                )
            except TimeoutError:
                return self.async_abort(reason="no_devices_found")
            self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        devices: dict[str, str] = {}
        for address, service_info in self._discovered_devices.items():
            if device := device_from_service_info(service_info):
                devices[address] = f"{device.name} ({address})"

        if not devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(devices)}),
            errors=errors,
        )

    def _collect_discovered_devices(self) -> None:
        """Collect only supported AC Infinity advertisements from HA's cache."""
        if discovery := self._discovery_info:
            if device_from_service_info(discovery):
                self._discovered_devices[discovery.address] = discovery
            return

        current_addresses = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass, connectable=True):
            if (
                discovery.address in current_addresses
                or discovery.address in self._discovered_devices
                or device_from_service_info(discovery) is None
            ):
                continue
            self._discovered_devices[discovery.address] = discovery
