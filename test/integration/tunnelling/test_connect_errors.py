"""Refused and malformed CONNECT_REQUESTs on the UDP, TCP and Unix tunnelling servers."""

from __future__ import annotations

import pytest

from knxnet_wire import (
    E_NO_ERROR,
    E_NO_MORE_CONNECTIONS,
    E_TUNNELLING_LAYER,
    HOST_UDP,
    TUNNEL_LINKLAYER,
    TUNNEL_RAW,
    Frame,
    connect_request,
    extended_tunnel_cri,
    hpai,
    parse_connect_response,
    tunnel_cri,
)
from server_config import E_CONNECTION_TYPE, FIRST_ADDRESS, NewEndpoint, Server, StartServer
from tunnel_session import Session, connect


@pytest.mark.parametrize("layer", [TUNNEL_RAW, 0x01, 0x03, 0x00, 0xFF])
async def test_unsupported_layer_is_refused(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str, layer: int
) -> None:
    response = await connect(await new_endpoint(knxd_server, transport), tunnel_cri(layer))
    assert response.status == E_TUNNELLING_LAYER


async def test_refused_layer_does_not_use_up_an_address_on_streams(
    start_server: StartServer, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    server = await start_server(addresses=1)
    endpoint = await new_endpoint(server, stream_transport)
    for _ in range(3):
        assert (await connect(endpoint, tunnel_cri(TUNNEL_RAW))).status == E_TUNNELLING_LAYER
    assert (await connect(endpoint, tunnel_cri(TUNNEL_LINKLAYER))).status == E_NO_ERROR


@pytest.mark.deviation
async def test_udp_refused_layer_leaks_the_client_address(start_server: StartServer, new_endpoint: NewEndpoint) -> None:
    # The UDP server takes an address before it looks at the layer and never gives it back.
    server = await start_server(addresses=2)
    endpoint = await new_endpoint(server, "udp")
    assert (await connect(endpoint, tunnel_cri(TUNNEL_RAW))).status == E_TUNNELLING_LAYER
    assert (await connect(endpoint, tunnel_cri(TUNNEL_RAW))).status == E_TUNNELLING_LAYER
    assert (await connect(endpoint, tunnel_cri(TUNNEL_LINKLAYER))).status == E_NO_MORE_CONNECTIONS


@pytest.mark.deviation
async def test_extended_cri_is_not_supported(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    # 03_08_04 5.4.3.2: a Tunnelling v2 server accepts a requested individual address.
    endpoint = await new_endpoint(knxd_server, transport)
    response = await connect(endpoint, extended_tunnel_cri(TUNNEL_LINKLAYER, FIRST_ADDRESS + 1))
    assert response.status == E_CONNECTION_TYPE


@pytest.mark.parametrize(
    "cri", [bytes([2, 0x07]), bytes([2, 0x00]), bytes([3, 0x04, 0x02]), bytes([5, 0x04, 0x02, 0, 0])]
)
async def test_unknown_connection_type_or_bad_cri_is_refused(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str, cri: bytes
) -> None:
    assert (await connect(await new_endpoint(knxd_server, transport), cri)).status == E_CONNECTION_TYPE


async def test_connect_with_foreign_host_protocol_is_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    endpoint = await new_endpoint(knxd_server, transport)
    unknown = hpai(0x07)
    await endpoint.send(connect_request(endpoint.protocol, tunnel_cri(TUNNEL_LINKLAYER), unknown, unknown))
    await endpoint.expect_silence()
    assert (await Session.connect(endpoint)).address == FIRST_ADDRESS  # nothing was allocated


async def test_udp_connect_for_tcp_host_protocol_is_ignored(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    endpoint = await new_endpoint(knxd_server, "udp")
    await endpoint.send(connect_request(HOST_UDP, tunnel_cri(TUNNEL_LINKLAYER), hpai(0x02), hpai(0x02)))
    await endpoint.expect_silence()


async def test_connect_requests_with_broken_bodies_are_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    endpoint = await new_endpoint(knxd_server, transport)
    both = hpai(endpoint.protocol) * 2
    await endpoint.send(Frame(0x0205, b"\x08\x01"))  # truncated
    await endpoint.send(Frame(0x0205, both))  # no CRI at all
    await endpoint.send(Frame(0x0205, both + bytes([9, 4, 2, 0])))  # CRI length byte does not match
    await endpoint.expect_silence()
    # The server is still usable afterwards, and nothing was allocated.
    assert (await Session.connect(endpoint)).address == FIRST_ADDRESS


async def test_response_to_refused_connect_has_only_channel_and_status(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    endpoint = await new_endpoint(knxd_server, transport)
    reply = await endpoint.request(connect_request(endpoint.protocol, tunnel_cri(TUNNEL_RAW)))
    assert len(reply.body) == 2
    assert parse_connect_response(reply).status == E_TUNNELLING_LAYER
