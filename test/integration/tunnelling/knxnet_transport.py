"""UDP, TCP and Unix socket transports for talking to knxd's KNXnet/IP tunnelling servers."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import anyio
from anyio.abc import SocketAttribute

from knxnet_wire import HEADER_SIZE, HOST_TCP, HOST_UDP, VERSION, Frame, decode, hpai

if TYPE_CHECKING:
    from anyio.abc import SocketStream, UDPSocket

LOOPBACK = "127.0.0.1"
DEFAULT_WAIT = 3.0
QUIET_WAIT = 0.7


class Endpoint:
    """Common interface of the transports."""

    protocol: int
    name: str

    async def send(self, frame: Frame) -> None:
        await self.send_raw(frame.encode())

    async def send_raw(self, data: bytes) -> None:
        raise NotImplementedError

    def own_hpai(self) -> bytes:
        """The HPAI to put into requests: our address on UDP, the route-back HPAI on streams."""
        return hpai(self.protocol)

    async def recv(self) -> Frame | None:
        """Wait for the next frame. None means a stream was closed by the peer."""
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError

    async def request(self, frame: Frame) -> Frame:
        await self.send(frame)
        return await self.request_next(f"no reply to service {frame.service:#06x}")

    async def request_next(self, what: str = "no frame received") -> Frame:
        """Return the next frame and fail if none arrives within DEFAULT_WAIT seconds."""
        with anyio.move_on_after(DEFAULT_WAIT):
            frame = await self.recv()
            assert frame is not None, "connection closed by the peer"
            return frame
        raise AssertionError(what)

    async def expect_silence(self, duration: float = QUIET_WAIT) -> None:
        with anyio.move_on_after(duration):
            extra = await self.recv()
            assert extra is None, f"unexpected frame: service {extra.service:#06x} body {extra.body.hex()}"

    async def drain(self, duration: float = QUIET_WAIT) -> list[Frame]:
        """Collect frames until none arrives for `duration` seconds."""
        frames: list[Frame] = []
        while True:
            frame = None
            with anyio.move_on_after(duration):
                frame = await self.recv()
            if frame is None:
                return frames
            frames.append(frame)


class UdpEndpoint(Endpoint):
    """One UDP socket used as control and data endpoint, talking to the server's control port."""

    protocol = HOST_UDP
    name = "udp"

    def __init__(self, sock: UDPSocket, server_port: int) -> None:
        self.sock = sock
        self.server = (LOOPBACK, server_port)
        self.local_port: int = sock.extra(SocketAttribute.local_port)
        self.source: tuple[str, int] | None = None  # sender of the last datagram received

    @classmethod
    async def open(cls, server_port: int) -> UdpEndpoint:
        return cls(await anyio.create_udp_socket(local_host=LOOPBACK), server_port)

    def own_hpai(self) -> bytes:
        return hpai(HOST_UDP, LOOPBACK, self.local_port)

    async def send_raw(self, data: bytes) -> None:
        await self.sock.sendto(data, *self.server)

    async def send_to(self, frame: Frame, address: tuple[str, int]) -> None:
        await self.sock.sendto(frame.encode(), *address)

    async def recv(self) -> Frame:
        data, self.source = await self.sock.receive()
        return decode(data)

    async def aclose(self) -> None:
        await self.sock.aclose()


class StreamEndpoint(Endpoint):
    """Frames over a byte stream. Uses route-back HPAIs (0.0.0.0:0), as TCP clients do."""

    protocol = HOST_TCP

    def __init__(self, stream: SocketStream) -> None:
        self.stream = stream
        self.buffer = b""
        self.eof = False

    async def send_raw(self, data: bytes) -> None:
        await self.stream.send(data)

    async def _fill(self) -> bool:
        """Read more data. Return False on EOF."""
        if self.eof:
            return False
        try:
            chunk = await self.stream.receive(4096)
        except (anyio.EndOfStream, anyio.BrokenResourceError, ConnectionResetError):
            self.eof = True
            return False
        self.buffer += chunk
        return True

    def _take(self) -> Frame | None:
        if len(self.buffer) < HEADER_SIZE:
            return None
        length = struct.unpack(">H", self.buffer[4:6])[0]
        if len(self.buffer) < length:
            return None
        data, self.buffer = self.buffer[:length], self.buffer[length:]
        assert data[:2] == bytes([HEADER_SIZE, VERSION]), f"bad header {data.hex()}"
        return decode(data)

    async def recv(self) -> Frame | None:
        while (frame := self._take()) is None:
            if not await self._fill():
                return None
        return frame

    async def wait_closed(self) -> None:
        """Discard incoming data until the server closes the connection."""
        while await self._fill():
            self.buffer = b""

    async def closed_by_peer(self) -> bool:
        """True if the server closes the connection within DEFAULT_WAIT seconds."""
        with anyio.move_on_after(DEFAULT_WAIT):
            await self.wait_closed()
        return self.eof

    async def aclose(self) -> None:
        await self.stream.aclose()


class TcpEndpoint(StreamEndpoint):
    """A TCP connection to the `tcptunsrv` server."""

    name = "tcp"

    @classmethod
    async def open(cls, server_port: int) -> TcpEndpoint:
        with anyio.fail_after(DEFAULT_WAIT):
            return cls(await anyio.connect_tcp(LOOPBACK, server_port))


class UnixEndpoint(StreamEndpoint):
    """A connection to the `unixtunsrv` server. Same protocol as TCP, over a Unix domain socket."""

    name = "unix"

    @classmethod
    async def open(cls, path: str) -> UnixEndpoint:
        with anyio.fail_after(DEFAULT_WAIT):
            return cls(await anyio.connect_unix(path))
