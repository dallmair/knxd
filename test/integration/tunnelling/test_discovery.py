"""SEARCH and DESCRIPTION on the UDP server, and what it advertises about tunnelling.

Discovery only exists on the UDP `ets_router` server (03_08_02). The TCP and Unix
servers do not answer it.
"""

from __future__ import annotations

import pytest

from knxnet_transport import StreamEndpoint, UdpEndpoint
from knxnet_wire import (
    DESCRIPTION_REQUEST,
    DESCRIPTION_RESPONSE,
    SEARCH_REQUEST,
    SEARCH_RESPONSE,
    TUNNEL_LINKLAYER,
    Frame,
    connect_request,
    parse_connect_response,
    tunnel_cri,
)
from server_config import E_CONNECTION_TYPE, NewEndpoint, Server, StartServer

FAMILY_CORE = 2
FAMILY_DEVICE_MANAGEMENT = 3
FAMILY_TUNNELLING = 4
FAMILY_ROUTING = 5


@pytest.fixture
async def discovery_server(start_server: StartServer) -> Server:
    # Discovery makes knxd join its multicast group, so tests using this are marked multicast.
    return await start_server(discover=True)


async def udp(server: Server, new_endpoint: NewEndpoint) -> UdpEndpoint:
    endpoint = await new_endpoint(server, "udp")
    assert isinstance(endpoint, UdpEndpoint)
    return endpoint


def supported_families(body: bytes, device_info_offset: int) -> dict[int, int]:
    """Parse the DIBs that start at `device_info_offset`: device info (54 bytes), then supported families."""
    start = device_info_offset + body[device_info_offset]
    assert body[start + 1] == 0x02, "second DIB must be the supported service families"
    length = body[start]
    pairs = body[start + 2 : start + length]
    return {pairs[i]: pairs[i + 1] for i in range(0, len(pairs), 2)}


@pytest.mark.multicast
async def test_search_response_describes_the_server(discovery_server: Server, new_endpoint: NewEndpoint) -> None:
    endpoint = await udp(discovery_server, new_endpoint)
    reply = await endpoint.request(Frame(SEARCH_REQUEST, endpoint.own_hpai()))
    assert reply.service == SEARCH_RESPONSE
    body = reply.body
    assert body[:8] == endpoint.own_hpai()[:2] + bytes([127, 0, 0, 1]) + discovery_server.udp_port.to_bytes(2, "big")
    device = body[8:]
    assert device[0] == 54, "DIB_DEVICE_INFO length"
    assert device[1] == 1, "DIB_DEVICE_INFO"
    assert device[2] == 2, "KNX medium TP1"
    assert device[4:6] == bytes([0x11, 0x01]), "the router's own address 1.1.1"
    assert device[24:54].rstrip(b"\0") == b"tunnel-test"


@pytest.mark.multicast
async def test_search_response_lists_core_and_tunnelling(discovery_server: Server, new_endpoint: NewEndpoint) -> None:
    endpoint = await udp(discovery_server, new_endpoint)
    reply = await endpoint.request(Frame(SEARCH_REQUEST, endpoint.own_hpai()))
    assert supported_families(reply.body, 8) == {FAMILY_CORE: 1, FAMILY_TUNNELLING: 1}


@pytest.mark.multicast
async def test_description_response_lists_core_management_and_tunnelling(
    discovery_server: Server, new_endpoint: NewEndpoint
) -> None:
    endpoint = await udp(discovery_server, new_endpoint)
    reply = await endpoint.request(Frame(DESCRIPTION_REQUEST, endpoint.own_hpai()))
    assert reply.service == DESCRIPTION_RESPONSE
    assert supported_families(reply.body, 0) == {FAMILY_CORE: 1, FAMILY_DEVICE_MANAGEMENT: 1, FAMILY_TUNNELLING: 1}


async def test_discovery_is_silent_when_disabled(start_server: StartServer, new_endpoint: NewEndpoint) -> None:
    server = await start_server(discover=False)
    endpoint = await udp(server, new_endpoint)
    await endpoint.send(Frame(SEARCH_REQUEST, endpoint.own_hpai()))
    await endpoint.send(Frame(DESCRIPTION_REQUEST, endpoint.own_hpai()))
    await endpoint.expect_silence()


@pytest.mark.multicast
async def test_tunnelling_is_not_advertised_when_it_is_disabled(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    server = await start_server(tunnel=False, discover=True)
    endpoint = await udp(server, new_endpoint)
    reply = await endpoint.request(Frame(DESCRIPTION_REQUEST, endpoint.own_hpai()))
    assert FAMILY_TUNNELLING not in supported_families(reply.body, 0)


@pytest.mark.multicast
async def test_description_has_no_tunnelling_info_dib(discovery_server: Server, new_endpoint: NewEndpoint) -> None:
    # Tunnelling v2 (03_08_04, 03_08_02) wants a DIB_TUNNELLING_INFO listing the slots; knxd sends only the families.
    endpoint = await udp(discovery_server, new_endpoint)
    reply = await endpoint.request(Frame(DESCRIPTION_REQUEST, endpoint.own_hpai()))
    start = reply.body[0] + reply.body[reply.body[0]]
    assert len(reply.body) == start, "nothing follows the supported-families DIB"


async def test_tunnelling_requests_are_refused_when_disabled(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    server = await start_server(tunnel=False)
    endpoint = await udp(server, new_endpoint)
    frame = connect_request(endpoint.protocol, tunnel_cri(TUNNEL_LINKLAYER), endpoint.own_hpai(), endpoint.own_hpai())
    response = parse_connect_response(await endpoint.request(frame))
    assert response.status == E_CONNECTION_TYPE


@pytest.mark.multicast
async def test_stream_servers_do_not_answer_discovery(
    discovery_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await new_endpoint(discovery_server, stream_transport)
    assert isinstance(endpoint, StreamEndpoint)
    await endpoint.send(Frame(SEARCH_REQUEST, endpoint.own_hpai()))
    await endpoint.send(Frame(DESCRIPTION_REQUEST, endpoint.own_hpai()))
    await endpoint.expect_silence()
