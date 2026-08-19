"""The led ble integration models."""
from __future__ import annotations

from dataclasses import dataclass

from .coordinator import ACInfinityDataUpdateCoordinator
from .vendor.ac_infinity_ble import ACInfinityController


@dataclass
class ACInfinityData:
    """Data for the AC Infinity integration."""

    title: str
    device: ACInfinityController
    coordinator: ACInfinityDataUpdateCoordinator
