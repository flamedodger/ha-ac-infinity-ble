"""Safe AC Infinity advertisement discovery helpers."""

from __future__ import annotations

from typing import Any

from .vendor.ac_infinity_ble.const import MANUFACTURER_ID
from .vendor.ac_infinity_ble.models import DeviceInfo
from .vendor.ac_infinity_ble.protocol import parse_manufacturer_data

NAME_ONLY_CONTROLLER_69 = "ACI-E"


def device_from_service_info(service_info: Any) -> DeviceInfo | None:
    """Return an AC Infinity device or None for unrelated/malformed BLE data."""
    # BluetoothServiceInfoBleak exposes manufacturer data directly. Keep the
    # AdvertisementData fallback for older HA releases and library callers.
    manufacturer_data = getattr(service_info, "manufacturer_data", None)
    if manufacturer_data is None:
        advertisement = getattr(service_info, "advertisement", None)
        manufacturer_data = getattr(advertisement, "manufacturer_data", {})
    payload = manufacturer_data.get(MANUFACTURER_ID)
    if payload is None:
        # Some Controller 69 units expose only their exact local name through
        # Home Assistant/BlueZ, even during an active scan. Use the known BLE
        # protocol defaults; entry setup still verifies the AC Infinity GATT
        # characteristics before it sends the read-only model query.
        if getattr(service_info, "name", None) == NAME_ONLY_CONTROLLER_69:
            return DeviceInfo(type=7, name=NAME_ONLY_CONTROLLER_69, version=3)
        return None
    try:
        return parse_manufacturer_data(payload)
    except (IndexError, UnicodeDecodeError, ValueError):
        return None
