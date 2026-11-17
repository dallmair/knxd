"""Successful CONNECT_REQUEST handling of the UDP (`ets_router`), TCP (`tcptunsrv`) and Unix (`unixtunsrv`) servers.

Covers what a client sees when it opens a tunnel or management connection:
the assigned KNX individual address, the data endpoint and channel handling.
Refusals are in `test_connect_errors.py`. Tests marked `deviation` pin
behaviour that differs from 03_08_04 or from the other transport; they are
meant to change when that behaviour is fixed.
"""

from __future__ import annotations

from knxnet_wire import (
    E_NO_ERROR,
    E_NO_MORE_CONNECTIONS,
    HOST_TCP,
    HOST_UDP,
    TUNNEL_LINKLAYER,
    connect_request,
    hpai,
    parse_connect_response,
    parse_hpai,
    tunnel_cri,
)
from server_config import FIRST_ADDRESS, NewEndpoint, Server, StartServer
from tunnel_session import Session, connect


async def test_link_layer_connect_returns_first_client_address(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    response = await connect(await new_endpoint(knxd_server, transport), tunnel_cri(TUNNEL_LINKLAYER))
    assert response.status == E_NO_ERROR
    assert response.channel == 1
    assert response.address == FIRST_ADDRESS


async def test_udp_data_endpoint_is_the_servers_udp_port(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    response = await connect(await new_endpoint(knxd_server, "udp"), tunnel_cri(TUNNEL_LINKLAYER))
    assert parse_hpai(response.data_endpoint) == (HOST_UDP, "127.0.0.1", knxd_server.udp_port)


async def test_stream_data_endpoint_is_route_back(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    # 03_08_02: inside a TCP connection the data endpoint is the route-back HPAI.
    response = await connect(await new_endpoint(knxd_server, stream_transport), tunnel_cri(TUNNEL_LINKLAYER))
    assert response.data_endpoint == hpai(HOST_TCP)


async def test_nat_connect_over_udp_gets_a_route_back_data_endpoint(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    endpoint = await new_endpoint(knxd_server, "udp")
    reply = await endpoint.request(connect_request(HOST_UDP, tunnel_cri(TUNNEL_LINKLAYER)))  # 0.0.0.0:0 HPAIs
    response = parse_connect_response(reply)
    assert response.status == E_NO_ERROR
    assert response.data_endpoint == hpai(HOST_UDP)
    assert response.address == FIRST_ADDRESS


async def test_addresses_are_unique_across_all_servers(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    sessions = [await Session.connect(await new_endpoint(knxd_server, kind)) for kind in ("udp", "tcp", "unix", "tcp")]
    assert [s.address for s in sessions] == [FIRST_ADDRESS + i for i in range(4)]


async def test_connection_is_refused_when_all_addresses_are_in_use(
    start_server: StartServer, new_endpoint: NewEndpoint, transport: str
) -> None:
    server = await start_server(addresses=2)
    await Session.connect(await new_endpoint(server, "udp"))
    await Session.connect(await new_endpoint(server, "tcp"))
    response = await connect(await new_endpoint(server, transport), tunnel_cri(TUNNEL_LINKLAYER))
    assert response.status == E_NO_MORE_CONNECTIONS


async def test_address_is_reused_after_disconnect(
    start_server: StartServer, new_endpoint: NewEndpoint, transport: str
) -> None:
    server = await start_server(addresses=1)
    first = await Session.connect(await new_endpoint(server, transport))
    assert await first.disconnect() == E_NO_ERROR
    second = await Session.connect(await new_endpoint(server, transport))
    assert second.address == first.address
