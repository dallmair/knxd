"""KNXnet/IP wire format helpers for black-box tests of knxd's tunnelling servers.

Written from 03_08_02 (Core) and 03_08_04 (Tunnelling). It shares no code with
knxd. Frames are `Frame` objects; the builders and parsers convert service
bodies from and to Python values.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

HEADER_SIZE = 6
VERSION = 0x10

# Service type identifiers.
SEARCH_REQUEST = 0x0201
SEARCH_RESPONSE = 0x0202
DESCRIPTION_REQUEST = 0x0203
DESCRIPTION_RESPONSE = 0x0204
CONNECT_REQUEST = 0x0205
CONNECT_RESPONSE = 0x0206
CONNECTIONSTATE_REQUEST = 0x0207
CONNECTIONSTATE_RESPONSE = 0x0208
DISCONNECT_REQUEST = 0x0209
DISCONNECT_RESPONSE = 0x020A
DEVICE_CONFIGURATION_REQUEST = 0x0310
DEVICE_CONFIGURATION_ACK = 0x0311
TUNNELLING_REQUEST = 0x0420
TUNNELLING_ACK = 0x0421
ROUTING_INDICATION = 0x0530

# Host protocol codes (HPAI).
HOST_UDP = 0x01
HOST_TCP = 0x02

# Connection types.
DEVICE_MGMT_CONNECTION = 0x03
TUNNEL_CONNECTION = 0x04

# Tunnelling layers.
TUNNEL_LINKLAYER = 0x02
TUNNEL_RAW = 0x04
TUNNEL_BUSMONITOR = 0x80

# Status codes.
E_NO_ERROR = 0x00
E_CONNECTION_ID = 0x21
E_CONNECTION_TYPE = 0x22
E_NO_MORE_CONNECTIONS = 0x24
E_DATA_CONNECTION = 0x26
E_TUNNELLING_LAYER = 0x29

# cEMI message codes.
L_DATA_REQ = 0x11
L_DATA_IND = 0x29
L_DATA_CON = 0x2E
L_BUSMON_IND = 0x2B
M_PROPREAD_REQ = 0xFC
M_PROPREAD_CON = 0xFB


@dataclass(frozen=True)
class Frame:
    """One KNXnet/IP frame: service type and body without the 6-byte header."""

    service: int
    body: bytes

    def encode(self) -> bytes:
        return struct.pack(">BBHH", HEADER_SIZE, VERSION, self.service, HEADER_SIZE + len(self.body)) + self.body


def decode(data: bytes) -> Frame:
    """Decode exactly one frame, checking header and length."""
    assert len(data) >= HEADER_SIZE, f"short frame {data.hex()}"
    assert data[:2] == bytes([HEADER_SIZE, VERSION]), f"bad header {data.hex()}"
    assert struct.unpack(">H", data[4:6])[0] == len(data), f"bad length in {data.hex()}"
    return Frame(struct.unpack(">H", data[2:4])[0], data[6:])


def hpai(protocol: int, host: str = "0.0.0.0", port: int = 0) -> bytes:
    """Host Protocol Address Information. 0.0.0.0:0 is the NAT / route-back form."""
    return bytes([8, protocol]) + socket.inet_aton(host) + struct.pack(">H", port)


def parse_hpai(data: bytes) -> tuple[int, str, int]:
    assert len(data) == 8, f"bad HPAI {data.hex()}"
    assert data[0] == 8, f"bad HPAI {data.hex()}"
    return data[1], socket.inet_ntoa(data[2:6]), struct.unpack(">H", data[6:8])[0]


def tunnel_cri(layer: int) -> bytes:
    return bytes([4, TUNNEL_CONNECTION, layer, 0])


def management_cri() -> bytes:
    return bytes([2, DEVICE_MGMT_CONNECTION])


def extended_tunnel_cri(layer: int, address: int) -> bytes:
    return bytes([6, TUNNEL_CONNECTION, layer, 0]) + struct.pack(">H", address)


def connect_request(protocol: int, cri: bytes, control: bytes | None = None, data: bytes | None = None) -> Frame:
    control = control if control is not None else hpai(protocol)
    data = data if data is not None else hpai(protocol)
    return Frame(CONNECT_REQUEST, control + data + cri)


def channel_request(service: int, protocol: int, channel: int, control: bytes | None = None) -> Frame:
    """CONNECTIONSTATE_REQUEST or DISCONNECT_REQUEST."""
    control = control if control is not None else hpai(protocol)
    return Frame(service, bytes([channel, 0]) + control)


def tunnel_request(channel: int, seq: int, cemi: bytes) -> Frame:
    return Frame(TUNNELLING_REQUEST, bytes([4, channel, seq, 0]) + cemi)


def tunnel_ack(channel: int, seq: int, status: int = E_NO_ERROR) -> Frame:
    return Frame(TUNNELLING_ACK, bytes([4, channel, seq, status]))


def config_request(channel: int, seq: int, cemi: bytes) -> Frame:
    return Frame(DEVICE_CONFIGURATION_REQUEST, bytes([4, channel, seq, 0]) + cemi)


def config_ack(channel: int, seq: int, status: int = E_NO_ERROR) -> Frame:
    return Frame(DEVICE_CONFIGURATION_ACK, bytes([4, channel, seq, status]))


@dataclass(frozen=True)
class ConnectResponse:
    channel: int
    status: int
    data_endpoint: bytes  # raw HPAI, empty on error
    crd: bytes  # connection response data block without its length byte, empty on error

    @property
    def address(self) -> int:
        """The KNX individual address from a tunnel CRD."""
        assert len(self.crd) == 3, f"no tunnel CRD: {self.crd.hex()}"
        assert self.crd[0] == TUNNEL_CONNECTION, f"no tunnel CRD: {self.crd.hex()}"
        return (self.crd[1] << 8) | self.crd[2]


def parse_connect_response(frame: Frame) -> ConnectResponse:
    assert frame.service == CONNECT_RESPONSE, f"expected CONNECT_RESPONSE, got {frame.service:#06x}"
    body = frame.body
    channel, status = body[0], body[1]
    if status != E_NO_ERROR:
        assert len(body) == 2, f"error response carries extra data: {body.hex()}"
        return ConnectResponse(channel, status, b"", b"")
    assert body[10] == len(body) - 10, f"bad CRD length in {body.hex()}"
    return ConnectResponse(channel, status, body[2:10], body[11:])


def parse_channel_status(frame: Frame, expected_service: int) -> tuple[int, int]:
    """CONNECTIONSTATE_RESPONSE and DISCONNECT_RESPONSE: (channel, status)."""
    assert frame.service == expected_service, f"expected {expected_service:#06x}, got {frame.service:#06x}"
    assert len(frame.body) == 2, f"unexpected body {frame.body.hex()}"
    return frame.body[0], frame.body[1]


@dataclass(frozen=True)
class Sequenced:
    """TUNNELLING_REQUEST/ACK and DEVICE_CONFIGURATION_REQUEST/ACK: channel, sequence, status, cEMI."""

    channel: int
    seq: int
    status: int
    cemi: bytes


def parse_sequenced(frame: Frame, expected_service: int) -> Sequenced:
    assert frame.service == expected_service, f"expected {expected_service:#06x}, got {frame.service:#06x}"
    body = frame.body
    assert body[0] == 4, f"bad connection header in {body.hex()}"
    return Sequenced(body[1], body[2], body[3], body[4:])


def group_write_cemi(message_code: int, group: int, value: int = 0x81, source: int = 0) -> bytes:
    """A cEMI L_Data frame carrying a small A_GroupValue_Write (6-bit value) to `group`."""
    return bytes([message_code, 0, 0xBC, 0xE0]) + struct.pack(">HH", source, group) + bytes([1, 0x00, value])


def group_address(main: int, middle: int, sub: int) -> int:
    return (main << 11) | (middle << 8) | sub


def individual_address(area: int, line: int, device: int) -> int:
    return (area << 12) | (line << 8) | device


def format_individual(address: int) -> str:
    return f"{address >> 12}.{(address >> 8) & 0xF}.{address & 0xFF}"
