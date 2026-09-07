from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, replace

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTCharacteristic, BleakGATTServiceCollection
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .const import (
    MANUFACTURER_ID,
    POSSIBLE_READ_CHARACTERISTIC_UUIDS,
    POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
    CallbackType,
)
from .exceptions import CharacteristicMissingError
from .models import DeviceInfo
from .protocol import (
    MULTI_PORT_TYPES,
    Protocol,
    parse_manufacturer_data,
    physical_port_count,
)
from .util import get_bit, get_bits, get_short

BLEAK_BACKOFF_TIME = 0.25
CONNECT_TIMEOUT = 30
GATT_TIMEOUT = 10
DISCONNECT_TIMEOUT = 5
DISCONNECT_DELAY = 120
DEFAULT_ATTEMPTS = 3

_LOGGER = logging.getLogger(__name__)


class ACInfinityController:
    def __init__(
        self,
        ble_device: BLEDevice,
        state: DeviceInfo | None = None,
        advertisement_data: AdvertisementData | None = None,
        port: int = 1,
    ) -> None:
        """Init the ACInfinityController."""
        if not state and not advertisement_data:
            raise ValueError("Must provide either state or advertisement_data")

        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        if state is None:
            assert advertisement_data is not None
            manufacturer_data = advertisement_data.manufacturer_data.get(
                MANUFACTURER_ID
            )
            if manufacturer_data is None:
                raise ValueError("Advertisement is not from an AC Infinity controller")
            state = parse_manufacturer_data(manufacturer_data)
        self._state = state
        if not 1 <= port <= physical_port_count(state.type):
            raise ValueError("Invalid physical controller port")
        # Multi-port selector 0 is controller-wide state, not physical port 1.
        self._port = port if state.type in MULTI_PORT_TYPES else 0
        self._state.fan = None
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._read_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._protocol: Protocol = Protocol()
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[DeviceInfo, CallbackType], None]] = []
        self._notify_future: asyncio.Future[bytearray] | None = None
        self._pending_sequence: int | None = None
        self._pending_command: int | None = None
        self._sequence = 1
        self._stopped = False

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        manufacturer_data = advertisement_data.manufacturer_data.get(MANUFACTURER_ID)
        if manufacturer_data is None:
            raise ValueError("Advertisement is not from an AC Infinity controller")
        info = parse_manufacturer_data(manufacturer_data)
        # Advertisement output follows the controller's screen selection, not
        # necessarily the port this entity controls. Only telemetry owns output.
        info.fan = None
        info.fan_state = None
        self._state = replace(
            self._state, **{k: v for k, v in asdict(info).items() if v is not None}
        )
        self._fire_callbacks(CallbackType.ADVERTISEMENT)

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Refresh the connectable BLE device supplied by Home Assistant."""
        self._ble_device = ble_device

    @property
    def connected(self) -> bool:
        """Return whether the persistent GATT session is connected."""
        return bool(self._client and self._client.is_connected)

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._state.name

    @property
    def is_on(self) -> bool:
        """Get whether the device is on."""
        return bool(self._state.fan)

    @property
    def port(self) -> int:
        """Physical port exposed by this entity (single-port controllers: 1)."""
        return self._port or 1

    @property
    def _response_port(self) -> int | None:
        return self._port if self._state.type in MULTI_PORT_TYPES else None

    @property
    def speed(self) -> int:
        """Get the speed of the device."""
        return self._state.fan or 0

    @property
    def temperature(self) -> float:
        """Get the temperature of the device."""
        return self._state.tmp or 0

    @property
    def humidity(self) -> float:
        """Get the humidity of the device."""
        return self._state.hum or 0

    @property
    def vpd(self) -> float:
        """Get the vpd of the device."""
        return self._state.vpd or 0

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def state(self) -> DeviceInfo:
        """Return the state."""
        return self._state

    @property
    def sequence(self) -> int:
        """Increment and return the sequence number."""
        if self._sequence == 65535:
            self._sequence = 0
        self._sequence += 1
        return self._sequence

    async def update(self) -> None:
        """Update the controller."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Updating", self.name)
        sequence = self.sequence
        command = self._protocol.get_model_data(self._state.type, self._port, sequence)
        if data := await self._send_command(command):
            values = self._protocol.parse_model_response(
                data, sequence, self._response_port
            )
            self._state.work_type = values[0x10][0]
            self._state.level_off = values[0x11][0] & 0x0F
            self._state.level_on = values[0x12][0] & 0x0F
            # ON/OFF levels are presets, not measurements of current output.
            self._fire_callbacks(CallbackType.UPDATE_RESPONSE)

    async def turn_on(self, speed: int | None = None) -> None:
        """Resume the saved ON speed unless a speed was explicitly requested."""
        if speed is not None:
            await self.set_speed(speed)
        else:
            await self._set_mode(2)

    async def turn_off(self) -> None:
        """Select OFF without rewriting either saved speed preset."""
        await self._set_mode(1)

    async def _set_mode(self, work_type: int) -> None:
        """Change only mode; physical telemetry confirms the resulting output."""
        await self._ensure_connected()
        sequence = self.sequence
        command = self._protocol.set_mode(
            self._state.type, work_type, self._port, sequence
        )
        response = await self._send_command(command)
        self._protocol.parse_set_response(response, sequence, self._response_port)
        self._fire_callbacks(CallbackType.UPDATE_RESPONSE)

    async def set_speed(self, speed: int) -> None:
        """Set the speed of the controller."""
        if speed not in range(0, 11):
            raise ValueError("Speed must be between 0 and 10")
        if speed == 0:
            await self.turn_off()
            return
        await self._ensure_connected()
        _LOGGER.debug("%s: Set speed to %s", self.name, speed)
        sequence = self.sequence
        command = self._protocol.set_level(
            self._state.type, 2, speed, self._port, sequence
        )
        response = await self._send_command(command)
        self._protocol.parse_set_response(response, sequence, self._response_port)
        self._state.level_on = speed
        # ACK confirms a setting, not motor output. Notifications update fan.
        self._fire_callbacks(CallbackType.UPDATE_RESPONSE)

    async def stop(self) -> None:
        """Stop the controller."""
        _LOGGER.debug("%s: Stop", self.name)
        self._stopped = True
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        await self._execute_disconnect()

    def abandon(self) -> None:
        """Drop local references to a poisoned client without blocking reload."""
        self._stopped = True
        self._expected_disconnect = True
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        if self._notify_future and not self._notify_future.done():
            self._notify_future.cancel()
        self._notify_future = None
        self._pending_sequence = None
        self._pending_command = None
        self._client = None
        self._read_char = None
        self._write_char = None

    def _fire_callbacks(self, type: CallbackType) -> None:
        """Fire the callbacks."""
        for callback in self._callbacks:
            callback(self._state, type)

    def register_callback(
        self, callback: Callable[[DeviceInfo, CallbackType], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        registered = True

        def unregister_callback() -> None:
            nonlocal registered
            if not registered:
                return
            registered = False
            try:
                self._callbacks.remove(callback)
            except ValueError:
                # HA can invoke coordinator shutdown more than once during a
                # reload. Cleanup must be idempotent so controller.stop() still
                # runs and releases the BLE connection.
                pass

        self._callbacks.append(callback)
        return unregister_callback

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._stopped:
            raise BleakError("AC Infinity controller client has been stopped")
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, waiting; RSSI: %s",
                self.name,
                self.rssi,
            )
        if self._client and self._client.is_connected:
            self._reset_disconnect_timer()
            return
        async with self._connect_lock:
            # Check again while holding the lock
            if self._stopped:
                raise BleakError("AC Infinity controller client has been stopped")
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.name, self.rssi)
            async with asyncio.timeout(CONNECT_TIMEOUT):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self.name,
                    self._disconnected,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._ble_device,
                )
            _LOGGER.debug("%s: Connected; RSSI: %s", self.name, self.rssi)
            resolved = self._resolve_characteristics(client.services)
            if not resolved:
                await self._safe_disconnect_client(client)
                self._read_char = None
                self._write_char = None
                raise CharacteristicMissingError(
                    "AC Infinity read/write characteristics were not found"
                )

            self._client = client
            self._reset_disconnect_timer()

            _LOGGER.debug(
                "%s: Subscribe to notifications; RSSI: %s", self.name, self.rssi
            )
            try:
                async with asyncio.timeout(GATT_TIMEOUT):
                    await client.start_notify(
                        self._read_char, self._notification_handler
                    )
                if self._stopped:
                    raise BleakError("AC Infinity controller client was stopped")
            except BaseException:
                if self._disconnect_timer:
                    self._disconnect_timer.cancel()
                    self._disconnect_timer = None
                self._client = None
                self._read_char = None
                self._write_char = None
                await self._safe_disconnect_client(client)
                raise

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())
        if len(data) >= 18 and data[0] == 0x1E and data[1] == 0xFF:
            self._state.is_degree = get_bit(data[6], 0)
            self._state.tmp_state = get_bits(data[6], 1, 2)
            self._state.hum_state = get_bits(data[6], 3, 2)
            self._state.vpd_state = get_bits(data[6], 5, 2)
            self._state.choose_port = get_bits(data[7], 4, 4)
            self._state.tmp = get_short(data, 8) / 100
            self._state.hum = get_short(data, 10) / 100
            self._state.vpd = get_short(data, 12) / 100
            offset = 18 + (self._port - 1) * 4 if self._port else 14
            if len(data) >= offset + 4:
                self._state.fan_type = get_short(data, offset)
                self._state.fan_state = get_bits(data[offset + 2], 0, 2)
                self._state.work_type = get_bits(data[offset + 3], 4, 4)
                level = get_bits(data[offset + 3], 0, 4)
                self._state.fan = level if data[offset] != 0xFF and level <= 10 else 0
            self._fire_callbacks(CallbackType.NOTIFICATION)
            return

        if self._notify_future and not self._notify_future.done():
            try:
                self._protocol.parse_response(
                    data, self._pending_sequence, self._pending_command
                )
            except ValueError:
                _LOGGER.debug("%s: Ignoring unrelated notification", self.name)
                return
            self._notify_future.set_result(data)

    def _reset_disconnect_timer(self) -> None:
        """Reset disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._expected_disconnect = False
        self._disconnect_timer = self.loop.call_later(
            DISCONNECT_DELAY, self._disconnect
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s", self.name, self.rssi
            )
            return
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.name,
            self.rssi,
        )
        if self._client is client:
            self._client = None
            self._read_char = None
            self._write_char = None

    def _disconnect(self) -> None:
        """Disconnect from device."""
        self._disconnect_timer = None
        asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting after timeout of %s",
            self.name,
            DISCONNECT_DELAY,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            read_char = self._read_char
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._read_char = None
            self._write_char = None
            if client and client.is_connected:
                await self._safe_disconnect_client(client, read_char)

    async def _safe_disconnect_client(
        self,
        client: BleakClientWithServiceCache,
        read_char: BleakGATTCharacteristic | None = None,
    ) -> None:
        """Release a client without allowing BlueZ teardown to hang HA setup."""
        if read_char and client.is_connected:
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.stop_notify(read_char)
            except Exception:  # noqa: BLE001 - cleanup must continue
                _LOGGER.debug("%s: stop-notify failed", self.name, exc_info=True)
        if client.is_connected:
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                _LOGGER.debug("%s: disconnect failed", self.name, exc_info=True)

    async def _send_command_locked(self, command: bytes) -> bytes:
        """Send command to device and read response."""
        try:
            return await self._execute_command_locked(command)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.name,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            await self._execute_disconnect()
            raise
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s", self.name, self.rssi, ex
            )
            await self._execute_disconnect()
            raise
        except (TimeoutError, EOFError) as ex:
            # A timed-out/dead D-Bus session can continue reporting
            # is_connected=True. Drop it and present a BleakError so the outer
            # whole-operation retry reconnects before trying again.
            _LOGGER.debug(
                "%s: stale BLE session; disconnecting before retry: %s",
                self.name,
                ex,
            )
            await self._execute_disconnect()
            raise BleakError(f"stale BLE session: {ex}") from ex

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command(self, command: bytes) -> bytes:
        """Send a command, reconnecting from scratch before every retry."""
        await self._ensure_connected()
        return await self._send_command_while_connected(command)

    async def _send_command_while_connected(self, command: bytes) -> bytes:
        """Send command to device and read response."""
        _LOGGER.debug(
            "%s: Sending command %s",
            self.name,
            command.hex(),
        )
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, waiting; RSSI: %s",
                self.name,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                return await self._send_command_locked(command)
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.name,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except CharacteristicMissingError as ex:
                _LOGGER.debug(
                    "%s: characteristic missing: %s; RSSI: %s",
                    self.name,
                    ex,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
                raise

        raise RuntimeError("Unreachable")

    async def _execute_command_locked(self, command: bytes) -> bytes:
        """Execute command and read response."""
        assert self._client is not None  # nosec
        if not self._read_char:
            raise CharacteristicMissingError("Read characteristic missing")
        if not self._write_char:
            raise CharacteristicMissingError("Write characteristic missing")

        self._notify_future = self.loop.create_future()
        self._pending_sequence = int.from_bytes(command[4:6], "big")
        self._pending_command = command[9]
        try:
            async with asyncio.timeout(GATT_TIMEOUT):
                await self._client.write_gatt_char(self._write_char, command, False)
            async with asyncio.timeout(5):
                return await self._notify_future
        finally:
            self._notify_future = None
            self._pending_sequence = None
            self._pending_command = None

    def _resolve_characteristics(self, services: BleakGATTServiceCollection) -> bool:
        """Resolve characteristics."""
        for characteristic in POSSIBLE_READ_CHARACTERISTIC_UUIDS:
            if char := services.get_characteristic(characteristic):
                self._read_char = char
                break
        for characteristic in POSSIBLE_WRITE_CHARACTERISTIC_UUIDS:
            if char := services.get_characteristic(characteristic):
                self._write_char = char
                break
        return bool(self._read_char and self._write_char)
