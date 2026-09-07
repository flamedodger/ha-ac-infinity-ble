"""Pure adaptive fan-control calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time

DEFAULTS: dict[str, bool | float | int | str] = {
    "adaptive_enabled": False,
    "manual_override": False,
    "lights_on": "12:00:00",
    "lights_off": "06:00:00",
    "day_temperature_target": 28.0,
    "night_temperature_target": 25.0,
    "day_humidity_target": 60.0,
    "night_humidity_target": 55.0,
    "day_vpd_target": 1.05,
    "night_vpd_target": 0.95,
    "leaf_temperature_offset": -2.0,
    "minimum_fan_level": 1,
    "maximum_fan_level": 10,
    "manual_fan_level": 3,
    "temperature_hysteresis": 0.5,
    "humidity_hysteresis": 3.0,
    "vpd_hysteresis": 0.08,
    "temperature_per_level": 1.0,
    "humidity_per_level": 4.0,
    "vpd_per_level": 0.10,
    "command_interval_minutes": 5,
    "minimum_run_minutes": 10,
    "sensor_stale_minutes": 15,
}


@dataclass(frozen=True)
class AdaptiveResult:
    """Calculated adaptive-control state."""

    requested_level: int
    reason: str
    leaf_temperature: float | None
    leaf_vpd: float | None


def parse_time(value: str) -> time:
    """Parse an option time stored as HH:MM[:SS]."""
    hour, minute, *seconds = (int(part) for part in value.split(":"))
    return time(hour, minute, seconds[0] if seconds else 0)


def is_daytime(now_time: time, lights_on: time, lights_off: time) -> bool:
    """Return whether a time falls inside a possibly overnight light period."""
    if lights_on < lights_off:
        return lights_on <= now_time < lights_off
    return now_time >= lights_on or now_time < lights_off


def leaf_vpd(temperature: float, humidity: float, offset: float) -> tuple[float, float]:
    """Calculate estimated leaf temperature and leaf VPD in kPa."""
    leaf_temperature = temperature + offset
    air_svp = 0.61078 * math.exp((17.27 * temperature) / (temperature + 237.3))
    leaf_svp = 0.61078 * math.exp(
        (17.27 * leaf_temperature) / (leaf_temperature + 237.3)
    )
    return leaf_temperature, leaf_svp - air_svp * humidity / 100


def calculate(
    *,
    temperature: float | None,
    humidity: float | None,
    current_level: int,
    daytime: bool,
    options: dict[str, bool | float | int | str],
    sensors_healthy: bool,
) -> AdaptiveResult:
    """Calculate the requested discrete fan level and diagnostic reason."""
    if temperature is None or humidity is None:
        return AdaptiveResult(current_level, "sensor safety hold", None, None)

    estimated_leaf_temperature, calculated_vpd = leaf_vpd(
        temperature,
        humidity,
        float(options["leaf_temperature_offset"]),
    )

    if bool(options["manual_override"]):
        return AdaptiveResult(
            int(options["manual_fan_level"]),
            "manual override",
            estimated_leaf_temperature,
            calculated_vpd,
        )
    if not sensors_healthy:
        return AdaptiveResult(
            current_level,
            "sensor safety hold",
            estimated_leaf_temperature,
            calculated_vpd,
        )

    period = "day" if daytime else "night"
    temperature_target = float(options[f"{period}_temperature_target"])
    humidity_target = float(options[f"{period}_humidity_target"])
    vpd_target = float(options[f"{period}_vpd_target"])

    temperature_demand = math.ceil(
        max(
            0,
            temperature - temperature_target - float(options["temperature_hysteresis"]),
        )
        / float(options["temperature_per_level"])
    )
    humidity_demand = math.ceil(
        max(0, humidity - humidity_target - float(options["humidity_hysteresis"]))
        / float(options["humidity_per_level"])
    )
    vpd_demand = math.ceil(
        max(0, vpd_target - calculated_vpd - float(options["vpd_hysteresis"]))
        / float(options["vpd_per_level"])
    )

    demands = {
        "high temperature": temperature_demand,
        "high humidity": humidity_demand,
        "low leaf VPD": vpd_demand,
    }
    reason, peak = max(demands.items(), key=lambda item: item[1])
    if peak <= 0:
        reason = "background minimum"

    minimum = int(options["minimum_fan_level"])
    maximum = max(minimum, int(options["maximum_fan_level"]))
    requested = min(maximum, max(minimum, minimum + peak))
    return AdaptiveResult(
        requested,
        reason,
        estimated_leaf_temperature,
        calculated_vpd,
    )
