"""Types and constants shared by the tunnelling fixtures and tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from knxnet_transport import Endpoint, StreamEndpoint, TcpEndpoint, UdpEndpoint, UnixEndpoint
from knxnet_wire import L_DATA_REQ, M_PROPREAD_REQ, TUNNEL_LINKLAYER, group_address, group_write_cemi
from tunnel_session import Session

TRANSPORTS = ["udp", "tcp", "unix"]
STREAM_TRANSPORTS = ["tcp", "unix"]

# The fixture configures client-addrs = 1.1.200:N, so the first tunnel gets 1.1.200.
FIRST_ADDRESS = 0x11C8
CLIENT_ADDRESSES = 4

GROUP = group_address(1, 2, 3)
REQUEST = group_write_cemi(L_DATA_REQ, GROUP)
PROP_READ = bytes([M_PROPREAD_REQ, 0, 0, 0, 0, 0x10, 0])


@dataclass
class Server:
    udp_port: int
    tcp_port: int
    unix_path: str

    async def open_endpoint(self, transport: str) -> Endpoint:
        if transport == "udp":
            return await UdpEndpoint.open(self.udp_port)
        if transport == "tcp":
            return await TcpEndpoint.open(self.tcp_port)
        return await UnixEndpoint.open(self.unix_path)


StartServer = Callable[..., Awaitable[Server]]
NewEndpoint = Callable[[Server, str], Awaitable[Endpoint]]


async def udp_session(
    server: Server, new_endpoint: NewEndpoint, layer: int | None = TUNNEL_LINKLAYER
) -> tuple[Session, UdpEndpoint]:
    endpoint = await new_endpoint(server, "udp")
    assert isinstance(endpoint, UdpEndpoint)
    return await Session.connect(endpoint, layer), endpoint


async def stream(server: Server, new_endpoint: NewEndpoint, kind: str) -> StreamEndpoint:
    endpoint = await new_endpoint(server, kind)
    assert isinstance(endpoint, StreamEndpoint)
    return endpoint
