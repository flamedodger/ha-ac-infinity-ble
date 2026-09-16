"""Protocol and discovery regression tests for the repaired integration."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

COMPONENT = Path(__file__).parents[1] / "custom_components" / "ac_infinity"

# Load the discovery module without importing Home Assistant's integration
# package; the test container intentionally does not install all of HA Core.
custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(COMPONENT.parent)]
ac_infinity = types.ModuleType("custom_components.ac_infinity")
ac_infinity.__path__ = [str(COMPONENT)]
sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.ac_infinity", ac_infinity)

discovery = importlib.import_module("custom_components.ac_infinity.discovery")
adaptive = importlib.import_module("custom_components.ac_infinity.adaptive")
ble = importlib.import_module("custom_components.ac_infinity.vendor.ac_infinity_ble")
device_module = importlib.import_module(
    "custom_components.ac_infinity.vendor.ac_infinity_ble.device"
)
Protocol = importlib.import_module(
    "custom_components.ac_infinity.vendor.ac_infinity_ble.protocol"
).Protocol
crc16 = importlib.import_module(
    "custom_components.ac_infinity.vendor.ac_infinity_ble.util"
).crc16

LIVE_ADVERTISEMENT = bytes.fromhex(
    "aabbccddeeff345050444a03070009841a1e000000006404000000"
)
LIVE_TELEMETRY_OFF = bytearray.fromhex(
    "1eff0209031c000009721a1700640000000113910002ffff0001ffff0001ffff0001"
)
LIVE_TELEMETRY_ON = bytearray.fromhex(
    "1eff0209031c0000095e1a4d005c0000001213910012ffff0001ffff0001ffff0001"
)
LIVE_ACK = bytes.fromhex("a5130006769585f2000310001200ff011629")
LIVE_MODEL_RESPONSE = bytes.fromhex(
    "a51300301a6663cc0001100101110105120105130700c25a200064001404"
    "0000000015040000000016080000000000000000170400000000ff00c513"
)
CONTROLLER_67_MODEL_RESPONSE = bytes.fromhex(
    "a510002e000215e6000110010311010112010a13070f521c48163e2d1404"
    "0000070815040000070816080000070800000708170409000f003665"
)


class AdaptiveControlTests(unittest.TestCase):
    def test_daytime_handles_overnight_light_period(self) -> None:
        self.assertTrue(
            adaptive.is_daytime(
                adaptive.parse_time("18:00:00"),
                adaptive.parse_time("18:00:00"),
                adaptive.parse_time("06:00:00"),
            )
        )
        self.assertFalse(
            adaptive.is_daytime(
                adaptive.parse_time("12:00:00"),
                adaptive.parse_time("18:00:00"),
                adaptive.parse_time("06:00:00"),
            )
        )

    def test_disabled_sensor_input_holds_current_level(self) -> None:
        result = adaptive.calculate(
            temperature=None,
            humidity=60,
            current_level=4,
            daytime=True,
            options=adaptive.DEFAULTS,
            sensors_healthy=False,
        )
        self.assertEqual(result.requested_level, 4)
        self.assertEqual(result.reason, "sensor safety hold")

    def test_high_temperature_increases_requested_level(self) -> None:
        result = adaptive.calculate(
            temperature=31,
            humidity=50,
            current_level=1,
            daytime=True,
            options=adaptive.DEFAULTS,
            sensors_healthy=True,
        )
        self.assertEqual(result.requested_level, 4)
        self.assertEqual(result.reason, "high temperature")

    def test_manual_override_uses_selected_level(self) -> None:
        options = {**adaptive.DEFAULTS, "manual_override": True, "manual_fan_level": 7}
        result = adaptive.calculate(
            temperature=25,
            humidity=50,
            current_level=1,
            daytime=True,
            options=options,
            sensors_healthy=True,
        )
        self.assertEqual(result.requested_level, 7)
        self.assertEqual(result.reason, "manual override")


def service_info(manufacturer_data: dict[int, bytes]) -> types.SimpleNamespace:
    """Build the small service-info surface used by discovery filtering."""
    advertisement = types.SimpleNamespace(manufacturer_data=manufacturer_data)
    return types.SimpleNamespace(advertisement=advertisement)


def current_service_info(
    manufacturer_data: dict[int, bytes], name: str | None = None
) -> types.SimpleNamespace:
    """Build the direct manufacturer-data surface current HA exposes."""
    return types.SimpleNamespace(manufacturer_data=manufacturer_data, name=name)


class DiscoveryTests(unittest.TestCase):
    def test_unrelated_device_is_ignored_instead_of_raising_500(self) -> None:
        unrelated = service_info({76: b"not an AC Infinity advertisement"})
        self.assertIsNone(discovery.device_from_service_info(unrelated))

    def test_malformed_matching_manufacturer_is_ignored(self) -> None:
        malformed = service_info({2306: b"short"})
        self.assertIsNone(discovery.device_from_service_info(malformed))

    def test_controller_69_is_discovered(self) -> None:
        found = discovery.device_from_service_info(
            service_info({2306: LIVE_ADVERTISEMENT})
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.type, 7)
        self.assertEqual(found.version, 3)
        self.assertEqual(found.name, "E-4PPDJ")

    def test_current_home_assistant_service_info_is_discovered(self) -> None:
        found = discovery.device_from_service_info(
            current_service_info({2306: LIVE_ADVERTISEMENT})
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.type, 7)

    def test_name_only_controller_69_is_discovered(self) -> None:
        found = discovery.device_from_service_info(
            current_service_info({}, name="ACI-E")
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.type, 7)
        self.assertEqual(found.version, 3)

    def test_similar_name_without_manufacturer_data_is_rejected(self) -> None:
        self.assertIsNone(
            discovery.device_from_service_info(
                current_service_info({}, name="ACI-E-clone")
            )
        )


class ProtocolTests(unittest.TestCase):
    @staticmethod
    def _prepare_telemetry_tracking(controller, now: float = 100.0) -> None:
        controller.loop = types.SimpleNamespace(time=lambda: now)
        controller._telemetry_event = asyncio.Event()
        controller._telemetry_generation = 0
        controller._last_telemetry_monotonic = None
        controller._connection_started_monotonic = now

    def test_cached_config_state_is_not_live_climate(self) -> None:
        async def exercise() -> None:
            controller = ble.ACInfinityController(
                types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF"),
                state=ble.DeviceInfo(
                    type=1,
                    name="A-test",
                    version=3,
                    tmp=26.6,
                    hum=55.1,
                    vpd=1.05,
                ),
            )

            self.assertIsNone(controller.temperature)
            self.assertIsNone(controller.humidity)
            self.assertIsNone(controller.vpd)
            self.assertEqual(controller.telemetry_generation, 0)

        asyncio.run(exercise())

    def test_only_real_telemetry_advances_climate_generation(self) -> None:
        controller = object.__new__(ble.ACInfinityController)
        controller._state = ble.DeviceInfo(type=7, name="E-test", version=3)
        controller._protocol = Protocol()
        controller._port = 1
        controller._callbacks = []
        controller._desired_work_type = None
        controller._notify_future = None
        controller._pending_sequence = None
        controller._pending_command = None
        self._prepare_telemetry_tracking(controller)

        controller._fire_callbacks(ble.CallbackType.UPDATE_RESPONSE)
        self.assertEqual(controller.telemetry_generation, 0)

        controller._notification_handler(0, LIVE_TELEMETRY_ON)
        self.assertEqual(controller.telemetry_generation, 1)
        self.assertEqual(controller.temperature, 23.98)
        self.assertEqual(controller.humidity, 67.33)
        self.assertEqual(controller.vpd, 0.92)

    def test_stale_detection_uses_real_telemetry_age(self) -> None:
        controller = object.__new__(ble.ACInfinityController)
        self._prepare_telemetry_tracking(controller, now=200.0)
        controller._last_telemetry_monotonic = 150.0

        self.assertFalse(controller.telemetry_is_stale(60))
        self.assertTrue(controller.telemetry_is_stale(30))

    def test_model_response_and_off_state(self) -> None:
        values = Protocol().parse_model_response(LIVE_MODEL_RESPONSE, 0x1A66)
        self.assertEqual(values[0x10], b"\x01")
        self.assertEqual(values[0x11], b"\x05")
        self.assertEqual(values[0x12], b"\x05")

    def test_controller_67_response_header_is_accepted(self) -> None:
        values = Protocol().parse_model_response(CONTROLLER_67_MODEL_RESPONSE, 2)
        self.assertEqual(values[0x10], b"\x03")
        self.assertEqual(values[0x11], b"\x01")
        self.assertEqual(values[0x12], b"\x0a")

    def test_set_acknowledgement_is_validated(self) -> None:
        Protocol().parse_set_response(LIVE_ACK, 0x7695)
        rejected = bytearray(LIVE_ACK)
        rejected[11] = 1
        rejected[-2:] = bytes(crc16(list(rejected), 8, 8))
        with self.assertRaises(ValueError):
            Protocol().parse_set_response(rejected, 0x7695)

    def test_mode_only_off_preserves_stored_level(self) -> None:
        packet = Protocol().set_mode(7, 1, 0, 0x1234)
        self.assertEqual(packet[10:-2], bytes((0x10, 1, 1, 0xFF, 0)))

    def test_telemetry_does_not_complete_a_command(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(type=7, name="E-test", version=3)
            controller._protocol = Protocol()
            controller._port = 1
            controller._callbacks = []
            controller._desired_work_type = None
            controller._notify_future = asyncio.get_running_loop().create_future()
            controller._pending_sequence = 0x7695
            controller._pending_command = 3
            self._prepare_telemetry_tracking(controller)

            controller._notification_handler(0, LIVE_TELEMETRY_ON)
            self.assertFalse(controller._notify_future.done())
            controller._notification_handler(0, bytearray(LIVE_ACK))
            self.assertEqual(await controller._notify_future, LIVE_ACK)

        asyncio.run(exercise())

    def test_physical_port_telemetry_overrides_global_state(self) -> None:
        controller = object.__new__(ble.ACInfinityController)
        controller._state = ble.DeviceInfo(
            type=7, name="E-test", version=3, work_type=2, fan=5
        )
        controller._protocol = Protocol()
        controller._port = 1
        controller._callbacks = []
        controller._desired_work_type = 2
        controller._notify_future = None
        controller._pending_sequence = None
        controller._pending_command = None
        self._prepare_telemetry_tracking(controller)

        controller._notification_handler(0, LIVE_TELEMETRY_OFF)
        self.assertEqual(controller.state.work_type, 2)
        # The physical port reports ON at zero; global and requested state
        # must not fabricate output for the fan.
        self.assertEqual(controller.state.fan, 0)

    def test_control_keeps_connection_open(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(
                type=7, name="E-test", version=3, level_on=5
            )
            controller._protocol = Protocol()
            controller._port = 1
            controller._callbacks = []
            controller._desired_work_type = None
            controller._sequence = 0x7694
            controller._ensure_connected = AsyncMock()
            controller._send_command = AsyncMock(return_value=LIVE_ACK)
            controller._execute_disconnect = AsyncMock()
            self._prepare_telemetry_tracking(controller)

            await controller.turn_on(5)
            self.assertFalse(controller.is_on)
            self.assertEqual(controller.state.level_on, 5)
            controller._notification_handler(0, LIVE_TELEMETRY_ON)
            self.assertTrue(controller.is_on)
            self.assertEqual(controller.speed, 1)
            controller._execute_disconnect.assert_not_awaited()

        asyncio.run(exercise())

    def test_single_port_control_updates_acknowledged_output(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(
                type=1,
                name="A-test",
                version=0,
                work_type=1,
                fan=0,
                level_on=5,
            )
            controller._protocol = Protocol()
            controller._port = 0
            controller._callbacks = []
            controller._sequence = 0x7694
            controller._ensure_connected = AsyncMock()
            controller._send_command = AsyncMock(return_value=LIVE_ACK)

            await controller.set_speed(3)

            self.assertTrue(controller.is_on)
            self.assertEqual(controller.speed, 3)
            self.assertEqual(controller.state.work_type, 2)
            self.assertEqual(controller.state.level_on, 3)

        asyncio.run(exercise())

    def test_callback_unregister_is_idempotent(self) -> None:
        controller = object.__new__(ble.ACInfinityController)
        controller._callbacks = []
        callback = lambda _state, _kind: None
        unregister = controller.register_callback(callback)

        unregister()
        unregister()

        self.assertEqual(controller._callbacks, [])

    def test_abandon_clears_poisoned_client_state(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._stopped = False
            controller._expected_disconnect = False
            controller._disconnect_timer = None
            controller._notify_future = asyncio.get_running_loop().create_future()
            controller._pending_sequence = 1
            controller._pending_command = 3
            controller._client = object()
            controller._read_char = object()
            controller._write_char = object()
            controller._connection_started_monotonic = 100.0
            controller._last_telemetry_monotonic = 50.0
            controller._telemetry_event = asyncio.Event()

            controller.abandon()

            self.assertTrue(controller._stopped)
            self.assertTrue(controller._expected_disconnect)
            self.assertIsNone(controller._notify_future)
            self.assertIsNone(controller._client)
            self.assertIsNone(controller._read_char)
            self.assertIsNone(controller._write_char)

        asyncio.run(exercise())

    def test_timeout_drops_stale_session(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(type=7, name="E-test", version=3)
            controller._advertisement_data = None
            controller._execute_command_locked = AsyncMock(side_effect=TimeoutError)
            controller._execute_disconnect = AsyncMock()

            with self.assertRaises(device_module.BleakError):
                await controller._send_command_locked(b"request")
            controller._execute_disconnect.assert_awaited_once()

        asyncio.run(exercise())

    def test_disconnect_timeout_cannot_block_shutdown(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(type=7, name="E-test", version=3)
            client = types.SimpleNamespace(
                is_connected=True,
                stop_notify=AsyncMock(side_effect=TimeoutError),
                disconnect=AsyncMock(side_effect=TimeoutError),
            )

            await controller._safe_disconnect_client(client, "read-char")

            client.stop_notify.assert_awaited_once_with("read-char")
            client.disconnect.assert_awaited_once()

        asyncio.run(exercise())

    def test_write_timeout_is_bounded(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._client = types.SimpleNamespace(
                write_gatt_char=AsyncMock(side_effect=TimeoutError)
            )
            controller._read_char = "read-char"
            controller._write_char = "write-char"
            controller._pending_sequence = None
            controller._pending_command = None
            controller._notify_future = None
            controller.loop = asyncio.get_running_loop()

            with self.assertRaises(TimeoutError):
                await controller._execute_command_locked(
                    bytes.fromhex("a5130001000000000003")
                )
            self.assertIsNone(controller._notify_future)
            self.assertIsNone(controller._pending_sequence)
            self.assertIsNone(controller._pending_command)

        asyncio.run(exercise())

    def test_retry_reconnects_before_resending(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._ensure_connected = AsyncMock()
            controller._send_command_while_connected = AsyncMock(
                side_effect=[device_module.BleakError("transient"), b"reply"]
            )

            self.assertEqual(await controller._send_command(b"request"), b"reply")
            self.assertEqual(controller._ensure_connected.await_count, 2)

        asyncio.run(exercise())

    def test_failed_control_restores_previous_state(self) -> None:
        async def exercise() -> None:
            controller = object.__new__(ble.ACInfinityController)
            controller._state = ble.DeviceInfo(
                type=7,
                name="E-test",
                version=3,
                work_type=2,
                fan=5,
                level_on=5,
            )
            controller._protocol = Protocol()
            controller._callbacks = []
            controller._desired_work_type = None
            controller._sequence = 100
            controller._port = 1
            controller._ensure_connected = AsyncMock()
            controller._send_command = AsyncMock(side_effect=TimeoutError)

            with self.assertRaises(TimeoutError):
                await controller.turn_off()
            self.assertTrue(controller.is_on)
            self.assertEqual(controller.speed, 5)
            self.assertIsNone(controller._desired_work_type)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
