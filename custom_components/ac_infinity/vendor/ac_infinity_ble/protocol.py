from .models import DeviceInfo
from .util import crc16, get_bit, get_bits, get_short

CONTROLLER_67_TYPE = 1
CONTROLLER_67_RESPONSE_MARKER = 0x10
CONTROLLER_69_RESPONSE_MARKER = 0x13


def get_type(type: int) -> str:
    if type == 2:
        return "B"
    if type in [3, 4, 5, 14, 15]:
        return "C"
    if type == 6:
        return "D"
    if type in [7, 8]:
        return "E"
    if type in [9, 12]:
        return "F"
    if type == 11:
        return "G"
    return "A"


def get_mode(mode: int) -> str:
    if mode == 1:
        return "OFF"
    if mode == 2:
        return "ON"
    if mode == 3:
        return "AUTO"
    if mode == 4:
        return "TIMER ON"
    if mode == 5:
        return "TIMER OFF"
    if mode == 6:
        return "CYCLE"
    if mode == 7:
        return "SCHEDULE"
    if mode == 8:
        return "VPD"
    if mode == 9:
        return "TEMPERATURE PARAM"
    if mode == 10:
        return "HUMIDITY PARAM"
    if mode == 11:
        return "ADVANCE"
    if mode == 12:
        return "AI"
    return ""


def parse_manufacturer_data(data: bytes) -> DeviceInfo:
    if len(data) < 19:
        raise ValueError(
            f"AC Infinity manufacturer data must be at least 19 bytes, got {len(data)}"
        )
    device = DeviceInfo(
        type=data[12],
        version=data[11],
        name=f"{get_type(data[12])}-{data[6:11].decode('ascii')}",
        is_degree=True ^ get_bit(data[13], 1),
        fan_state=get_bits(data[13], 2, 2),
        tmp_state=get_bits(data[13], 4, 2),
        hum_state=get_bits(data[13], 6, 2),
        tmp=get_short(data, 14) / 100,
        hum=get_short(data, 16) / 100,
        fan=data[18],
    )
    if device.version >= 3 and device.type in [7, 9, 11, 12]:
        if len(data) < 23:
            raise ValueError(
                "multi-port AC Infinity manufacturer data must be at least 23 bytes"
            )
        device.choose_port = data[19]
        device.vpd_state = get_bits(data[20], 0, 2)
        device.vpd = get_short(data, 21) / 100
    return device


class Protocol:
    """Protocol for AC Infinity Controllers."""

    def __init__(self) -> None:
        self._head = [165, 0]
        self._scan_record_length = 27

    def _add_init(self, bytes: list[int], i: int, i2: int) -> None:
        bytes[i] = (i2 >> 8) & 255
        bytes[i + 1] = i2 & 255

    def _add_head(self, data: list[int], b: int, i: int) -> bytes:
        result = [0] * (len(data) + 12)
        result[0 : len(self._head)] = self._head  # noqa: E203
        self._add_init(result, 2, len(data))
        self._add_init(result, 4, i)
        result[6:8] = crc16(result, 0, 6)
        result[8] = 0
        result[9] = b
        result[10 : 10 + len(data)] = data  # noqa: E203
        result[len(data) + 10 : len(data) + 12] = crc16(  # noqa: E203
            result, 8, len(data) + 2
        )
        return bytes(result)

    def parse_response(
        self,
        data: bytes | bytearray,
        expected_sequence: int | None = None,
        expected_command: int | None = None,
        expected_marker: int = CONTROLLER_69_RESPONSE_MARKER,
    ) -> bytes:
        """Validate a response frame and return its payload."""
        packet = bytes(data)
        if len(packet) < 12 or packet[0:2] != bytes((0xA5, expected_marker)):
            raise ValueError("not an AC Infinity response frame")
        payload_length = int.from_bytes(packet[2:4], "big")
        if len(packet) != payload_length + 12:
            raise ValueError("response length does not match its header")
        if packet[6:8] != bytes(crc16(list(packet), 0, 6)):
            raise ValueError("invalid response header CRC")
        if packet[-2:] != bytes(crc16(list(packet), 8, payload_length + 2)):
            raise ValueError("invalid response body CRC")
        sequence = int.from_bytes(packet[4:6], "big")
        if expected_sequence is not None and sequence != expected_sequence:
            raise ValueError("response sequence does not match request")
        if expected_command is not None and packet[9] != expected_command:
            raise ValueError("response command does not match request")
        return packet[10:-2]

    def parse_model_response(
        self,
        data: bytes | bytearray,
        expected_sequence: int | None = None,
        controller_type: int | None = None,
    ) -> dict[int, bytes]:
        """Parse parameter TLVs from a validated model response."""
        payload = self.parse_response(
            data, expected_sequence, 1, self.response_marker(controller_type)
        )
        values: dict[int, bytes] = {}
        offset = 0
        while offset + 2 <= len(payload):
            parameter = payload[offset]
            length = payload[offset + 1]
            end = offset + 2 + length
            if end > len(payload):
                raise ValueError("truncated model response parameter")
            values[parameter] = payload[offset + 2 : end]
            offset = end
            if parameter == 0xFF:
                break
        required_parameters = (
            (0x11, 0x12)
            if controller_type == CONTROLLER_67_TYPE
            else (0x10, 0x11, 0x12)
        )
        if any(not values.get(parameter) for parameter in required_parameters):
            raise ValueError("model response is missing mode or level parameters")
        return values

    def parse_set_response(
        self,
        data: bytes | bytearray,
        expected_sequence: int,
        controller_type: int | None = None,
    ) -> None:
        """Validate that every parameter in a SET acknowledgement succeeded."""
        payload = self.parse_response(
            data, expected_sequence, 3, self.response_marker(controller_type)
        )
        if not payload or len(payload) % 2:
            raise ValueError("malformed AC Infinity SET acknowledgement")
        failures = [
            (payload[index], payload[index + 1])
            for index in range(0, len(payload), 2)
            if payload[index + 1] != 0
        ]
        if failures:
            raise ValueError(f"AC Infinity rejected SET parameters: {failures}")

    def get_model_data(self, type: int, b: int, sequence: int) -> bytes:
        # Controller 67 (advertisement type 1/version 0) uses the older
        # parameter set captured from the vendor app. It does not answer the
        # Controller 69 query that begins with parameter 0x10.
        command = (
            [0x11, 0x12, 0x15, 0x18]
            if type == CONTROLLER_67_TYPE
            else [16, 17, 18, 19, 20, 21, 22, 23]
        )
        if type in [7, 9, 11, 12]:
            command += [255, b]
        return self._add_head(command, 1, sequence)

    @staticmethod
    def response_marker(controller_type: int | None) -> int:
        """Return the response-frame marker used by a controller family."""
        if controller_type == CONTROLLER_67_TYPE:
            return CONTROLLER_67_RESPONSE_MARKER
        return CONTROLLER_69_RESPONSE_MARKER

    def set_level(
        self, type: int, work_type: int, level: int, b: int, sequence: int
    ) -> bytes:
        if work_type not in [1, 2]:
            raise ValueError("Work type must be 1 (off) or 2 (on)")
        if level not in range(0, 11):
            raise ValueError("Level must be between 0 and 10")

        command = [16, 1, work_type, work_type + 16, 1, level]
        if type in [7, 9, 11, 12]:
            command += [255, b]
        return self._add_head(command, 3, sequence)

    def set_mode(self, type: int, work_type: int, b: int, sequence: int) -> bytes:
        """Select OFF or ON without changing either stored speed preset."""
        if work_type not in [1, 2]:
            raise ValueError("Work type must be 1 (off) or 2 (on)")
        command = [16, 1, work_type]
        if type in [7, 9, 11, 12]:
            command += [255, b]
        return self._add_head(command, 3, sequence)
