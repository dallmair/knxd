"""Types and constants shared by the tunnelling fixtures and tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from knxnet_transport import Endpoint, TcpEndpoint, UdpEndpoint, UnixEndpoint

TRANSPORTS = ["udp", "tcp", "unix"]
STREAM_TRANSPORTS = ["tcp", "unix"]

# The fixture configures client-addrs = 1.1.200:N, so the first tunnel gets 1.1.200.
FIRST_ADDRESS = 0x11C8
CLIENT_ADDRESSES = 4

# Status codes that are not (yet) in knxnet_wire because only some tests need them.
E_CONNECTION_TYPE = 0x22


@dataclass
class Server:
    udp_port: int
    tcp_port: int
    unix_path: str
    discover: bool

    async def open_endpoint(self, transport: str) -> Endpoint:
        if transport == "udp":
            return await UdpEndpoint.open(self.udp_port)
        if transport == "tcp":
            return await TcpEndpoint.open(self.tcp_port)
        return await UnixEndpoint.open(self.unix_path)


StartServer = Callable[..., Awaitable[Server]]
NewEndpoint = Callable[[Server, str], Awaitable[Endpoint]]
