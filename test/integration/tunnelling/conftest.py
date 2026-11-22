"""Fixtures for the KNXnet/IP tunnelling tests.

`knxd_server` starts one knxd with the `ets_router` server (UDP), the
`tcptunsrv` server (TCP) and the `unixtunsrv` server (TCP framing over a Unix
socket). All three share one pool of tunnel client addresses, like in a real
setup. Tests then talk to them from outside with the endpoints in
`knxnet_transport`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from knxnet_wire import TUNNEL_LINKLAYER
from server_config import CLIENT_ADDRESSES, STREAM_TRANSPORTS, TRANSPORTS, NewEndpoint, Server, StartServer
from tunnel_session import Session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from knxnet_transport import Endpoint

    from ..conftest import Processes


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "deviation: pins current behaviour that deviates from the KNX specification or is a known bug; "
        "expect these tests to change when the behaviour is fixed",
    )


@pytest.fixture
def start_server(procs: Processes, tmp_path: Path, tool: Callable[[str], str]) -> StartServer:
    """Return a function that starts a knxd and waits until all its servers answer."""

    async def start(
        *, heartbeat: int = 120, discover: bool = False, tunnel: bool = True, addresses: int = CLIENT_ADDRESSES
    ) -> Server:
        unix_path = str(tmp_path / "tun.sock")
        opt = ["tunnel = tunnel"] if tunnel else []
        lines = [
            "[main]",
            "name = tunnel-test",
            "addr = 1.1.1",
            f"client-addrs = 1.1.200:{addresses}",
            "connections = server,tcp,dummy,unix",
            "debug = debug",
            "",
            "[debug]",
            "error-level = info",
            "",
            "[dummy]",
            "driver = dummy",
            "",
            "[server]",
            "server = ets_router",
            f"discover = {'true' if discover else 'false'}",
            "port = 0",
            "multicast-address = 224.99.98.95",
            f"heartbeat-timeout = {heartbeat}",
            *opt,
            "",
            "[tcp]",
            "server = tcptunsrv",
            "port = 0",
            f"heartbeat-timeout = {heartbeat}",
            *opt,
            "",
            "[unix]",
            "server = unixtunsrv",
            f"path = {unix_path}",
            f"heartbeat-timeout = {heartbeat}",
            *opt,
        ]
        if tunnel:
            lines += ["", "[tunnel]"]
        config = tmp_path / "knxd.ini"
        config.write_text("\n".join(lines) + "\n")
        await procs.spawn("knxd", [tool("knxd"), str(config)])
        # The unix server is started last, so its socket shows that the others are up too.
        await procs.wait_for_socket(tmp_path / "tun.sock")
        ports = await procs.wait_for_ports("knxd", ["server", "tcp"])
        return Server(ports["server"], ports["tcp"], unix_path)

    return start


@pytest.fixture
async def knxd_server(start_server: StartServer) -> Server:
    return await start_server()


@pytest.fixture
async def new_endpoint() -> AsyncIterator[NewEndpoint]:
    opened: list[Endpoint] = []

    async def make(server: Server, transport: str) -> Endpoint:
        endpoint = await server.open_endpoint(transport)
        opened.append(endpoint)
        return endpoint

    try:
        yield make
    finally:
        for endpoint in opened:
            await endpoint.aclose()


@pytest.fixture(params=TRANSPORTS)
def transport(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(params=STREAM_TRANSPORTS)
def stream_transport(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def make_session(knxd_server: Server, new_endpoint: NewEndpoint) -> Callable[..., Awaitable[Session]]:
    """Open an endpoint of the given transport and connect a tunnel on it. `layer=None` means management."""

    async def make(kind: str, layer: int | None = TUNNEL_LINKLAYER, server: Server | None = None) -> Session:
        endpoint = await new_endpoint(server or knxd_server, kind)
        return await Session.connect(endpoint, layer)

    return make
