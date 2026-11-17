"""CONNECTIONSTATE, DISCONNECT and heartbeat timeout on the UDP, TCP and Unix tunnelling servers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio

from knxnet_transport import StreamEndpoint, UdpEndpoint
from knxnet_wire import (
    CONNECTIONSTATE_REQUEST,
    DISCONNECT_REQUEST,
    E_CONNECTION_ID,
    E_NO_ERROR,
    TUNNEL_LINKLAYER,
    channel_request,
    hpai,
    parse_hpai,
    tunnel_cri,
)
from tunnel_session import Session, connect

if TYPE_CHECKING:
    from server_config import NewEndpoint, Server, StartServer

HEARTBEAT = 2  # seconds; the server default is 120


async def test_connectionstate_of_an_open_channel_is_ok(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await Session.connect(await new_endpoint(knxd_server, transport))
    assert await session.connection_state() == E_NO_ERROR


async def test_connectionstate_works_on_a_management_channel(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await Session.connect(await new_endpoint(knxd_server, transport), None)
    assert await session.connection_state() == E_NO_ERROR


async def test_connectionstate_of_an_unknown_channel_is_refused(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await Session.connect(await new_endpoint(knxd_server, transport))
    assert await session.connection_state(session.channel + 7) == E_CONNECTION_ID


async def test_disconnect_closes_the_channel(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    session = await Session.connect(await new_endpoint(knxd_server, transport))
    assert await session.disconnect() == E_NO_ERROR
    assert await session.connection_state() == E_CONNECTION_ID
    assert await session.disconnect() == E_CONNECTION_ID


async def test_disconnect_of_an_unknown_channel_is_refused(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await Session.connect(await new_endpoint(knxd_server, transport))
    assert await session.disconnect(session.channel + 7) == E_CONNECTION_ID
    assert await session.connection_state() == E_NO_ERROR  # the real channel is untouched


async def test_stream_connection_survives_disconnect_of_its_channel(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await new_endpoint(knxd_server, stream_transport)
    first = await Session.connect(endpoint)
    assert await first.disconnect() == E_NO_ERROR
    second = await connect(endpoint, tunnel_cri(TUNNEL_LINKLAYER))
    assert second.status == E_NO_ERROR


async def test_udp_replies_go_to_the_control_endpoint_in_the_request(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender = await new_endpoint(knxd_server, "udp")
    other = await new_endpoint(knxd_server, "udp")
    assert isinstance(sender, UdpEndpoint)
    assert isinstance(other, UdpEndpoint)
    session = await Session.connect(sender)
    await sender.send(channel_request(CONNECTIONSTATE_REQUEST, sender.protocol, session.channel, other.own_hpai()))
    reply = await other.request_next()
    assert reply.body == bytes([session.channel, E_NO_ERROR])
    await sender.expect_silence()


async def test_udp_nat_control_endpoint_answers_the_sender(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    sender = await new_endpoint(knxd_server, "udp")
    session = await Session.connect(sender)
    reply = await sender.request(
        channel_request(CONNECTIONSTATE_REQUEST, sender.protocol, session.channel, hpai(sender.protocol))
    )
    assert reply.body == bytes([session.channel, E_NO_ERROR])


async def test_idle_udp_client_is_sent_a_disconnect_request(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    server = await start_server(heartbeat=HEARTBEAT)
    endpoint = await new_endpoint(server, "udp")
    assert isinstance(endpoint, UdpEndpoint)
    session = await Session.connect(endpoint)
    with anyio.fail_after(HEARTBEAT + 2):
        request = await endpoint.recv()
    assert request.service == DISCONNECT_REQUEST
    assert request.body[0] == session.channel
    assert parse_hpai(request.body[2:10]) == (endpoint.protocol, "127.0.0.1", server.udp_port)
    assert await session.connection_state() == E_CONNECTION_ID


async def test_idle_stream_client_is_disconnected(
    start_server: StartServer, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    server = await start_server(heartbeat=HEARTBEAT)
    endpoint = await new_endpoint(server, stream_transport)
    assert isinstance(endpoint, StreamEndpoint)
    await Session.connect(endpoint)
    with anyio.fail_after(HEARTBEAT + 2):
        await endpoint.wait_closed()


async def test_connectionstate_requests_keep_a_connection_alive(
    start_server: StartServer, new_endpoint: NewEndpoint, transport: str
) -> None:
    server = await start_server(heartbeat=HEARTBEAT)
    session = await Session.connect(await new_endpoint(server, transport))
    deadline = time.monotonic() + 2 * HEARTBEAT + 1
    while time.monotonic() < deadline:
        await anyio.sleep(0.5)
        assert await session.connection_state() == E_NO_ERROR


async def test_closing_a_stream_connection_releases_its_address(
    start_server: StartServer, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    server = await start_server(addresses=1)
    endpoint = await new_endpoint(server, stream_transport)
    first = await Session.connect(endpoint)
    await endpoint.aclose()
    # The server notices the close asynchronously; ask until the address is back.
    deadline = time.monotonic() + 3
    while True:
        response = await connect(await new_endpoint(server, stream_transport), tunnel_cri(TUNNEL_LINKLAYER))
        if response.status == E_NO_ERROR or time.monotonic() > deadline:
            break
        await anyio.sleep(0.1)
    assert response.status == E_NO_ERROR
    assert response.address == first.address


async def test_udp_channel_without_disconnect_keeps_its_address(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    server = await start_server(addresses=1)
    await Session.connect(await new_endpoint(server, "udp"))
    other = await connect(await new_endpoint(server, "udp"), tunnel_cri(TUNNEL_LINKLAYER))
    assert other.status != E_NO_ERROR
