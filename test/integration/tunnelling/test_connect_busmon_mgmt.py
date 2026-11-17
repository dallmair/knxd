"""Busmonitor and management CONNECT_REQUESTs, and channel handling, on the three tunnelling servers."""

from __future__ import annotations

import pytest

from knxnet_wire import (
    E_NO_ERROR,
    E_NO_MORE_CONNECTIONS,
    TUNNEL_BUSMONITOR,
    TUNNEL_LINKLAYER,
    management_cri,
    tunnel_cri,
)
from server_config import FIRST_ADDRESS, NewEndpoint, Server, StartServer
from tunnel_session import Session, connect


async def test_busmonitor_connect_over_streams_has_no_address(
    start_server: StartServer, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    server = await start_server(addresses=1)
    response = await connect(await new_endpoint(server, stream_transport), tunnel_cri(TUNNEL_BUSMONITOR))
    assert response.status == E_NO_ERROR
    assert response.crd == bytes([0x04, 0x00, 0x00])
    # No address was used up.
    again = await connect(await new_endpoint(server, stream_transport), tunnel_cri(TUNNEL_LINKLAYER))
    assert again.status == E_NO_ERROR


@pytest.mark.deviation
async def test_udp_busmonitor_connect_takes_a_client_address(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    server = await start_server(addresses=1)
    response = await connect(await new_endpoint(server, "udp"), tunnel_cri(TUNNEL_BUSMONITOR))
    assert response.status == E_NO_ERROR
    assert response.address == FIRST_ADDRESS
    again = await connect(await new_endpoint(server, "udp"), tunnel_cri(TUNNEL_LINKLAYER))
    assert again.status == E_NO_MORE_CONNECTIONS


@pytest.mark.deviation
async def test_several_busmonitors_are_accepted(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    # 03_08_04 2.2.4 allows only one busmonitor connection per subnetwork.
    first = await connect(await new_endpoint(knxd_server, transport), tunnel_cri(TUNNEL_BUSMONITOR))
    second = await connect(await new_endpoint(knxd_server, transport), tunnel_cri(TUNNEL_BUSMONITOR))
    assert first.status == second.status == E_NO_ERROR


async def test_management_connect_has_no_address(
    start_server: StartServer, new_endpoint: NewEndpoint, transport: str
) -> None:
    server = await start_server(addresses=1)
    for _ in range(3):
        response = await connect(await new_endpoint(server, transport), management_cri())
        assert response.status == E_NO_ERROR
        assert response.crd == bytes([0x03])
    again = await connect(await new_endpoint(server, transport), tunnel_cri(TUNNEL_LINKLAYER))
    assert again.status == E_NO_ERROR


async def test_udp_channel_ids_are_distinct_across_clients(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    channels = [(await Session.connect(await new_endpoint(knxd_server, "udp"), None)).channel for _ in range(4)]
    assert channels == [1, 2, 3, 4]


async def test_stream_channel_ids_are_per_connection(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    # Every TCP/Unix connection has its own channel ID space, so each starts at 1.
    channels = [
        (await Session.connect(await new_endpoint(knxd_server, stream_transport), None)).channel for _ in range(3)
    ]
    assert channels == [1, 1, 1]


async def test_stream_connection_can_hold_several_channels(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await new_endpoint(knxd_server, stream_transport)
    first = await Session.connect(endpoint)
    second = await Session.connect(endpoint)
    assert first.channel != second.channel
    assert first.address != second.address
    assert await first.disconnect() == E_NO_ERROR
    assert await second.connection_state() == E_NO_ERROR
