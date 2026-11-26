"""TCP and Unix socket tunnelling (`tcptunsrv`, `unixtunsrv`): how the byte stream is cut into frames."""

from __future__ import annotations

import anyio
import pytest

from knxnet_wire import (
    CONNECT_RESPONSE,
    CONNECTIONSTATE_REQUEST,
    CONNECTIONSTATE_RESPONSE,
    DISCONNECT_REQUEST,
    E_NO_ERROR,
    TUNNEL_LINKLAYER,
    Frame,
    channel_request,
    connect_request,
    tunnel_cri,
)
from server_config import NewEndpoint, Server, StartServer, stream
from tunnel_session import Session


async def test_frame_split_into_single_bytes_is_reassembled(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    for byte in connect_request(endpoint.protocol, tunnel_cri(TUNNEL_LINKLAYER)).encode():
        await endpoint.send_raw(bytes([byte]))
    assert (await endpoint.request_next()).service == CONNECT_RESPONSE


async def test_several_frames_in_one_write_are_all_handled(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    one = channel_request(CONNECTIONSTATE_REQUEST, endpoint.protocol, session.channel).encode()
    await endpoint.send_raw(one * 3)
    assert [f.service for f in await endpoint.drain(0.7)] == [CONNECTIONSTATE_RESPONSE] * 3


async def test_incomplete_frame_is_held_back_until_it_is_complete(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    raw = channel_request(CONNECTIONSTATE_REQUEST, endpoint.protocol, session.channel).encode()
    await endpoint.send_raw(raw[:9])
    await endpoint.expect_silence()
    await endpoint.send_raw(raw[9:])
    assert (await endpoint.request_next()).service == CONNECTIONSTATE_RESPONSE


async def test_unknown_service_is_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    await endpoint.send(Frame(0x0999, b"\x00\x01"))
    await endpoint.send(Frame(0x0999, b""))
    await endpoint.expect_silence()
    assert await session.connection_state() == E_NO_ERROR


async def test_unparseable_known_service_is_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    await endpoint.send(Frame(CONNECTIONSTATE_REQUEST, b"\x01"))
    await endpoint.send(Frame(DISCONNECT_REQUEST, b"\x01\x00"))  # no HPAI
    await endpoint.expect_silence()
    assert await session.connection_state() == E_NO_ERROR


@pytest.mark.parametrize(
    "header",
    [
        bytes([6, 0x11, 2, 7, 0, 10, 1, 0, 0, 0]),  # protocol version 1.1
        bytes([5, 0x10, 2, 7, 0, 10, 1, 0, 0, 0]),  # header length 5
        bytes([6, 0x10, 9, 0x99, 0, 4]),  # total length shorter than the header
    ],
    ids=["version", "header-size", "short-length"],
)
async def test_malformed_header_closes_the_connection(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str, header: bytes
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    await endpoint.send_raw(header)
    assert await endpoint.closed_by_peer()


async def test_malformed_header_releases_the_channels_of_the_connection(
    start_server: StartServer, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    server = await start_server(addresses=1)
    endpoint = await stream(server, new_endpoint, stream_transport)
    first = await Session.connect(endpoint)
    await endpoint.send_raw(bytes([6, 0x11, 0, 0, 0, 6]))
    assert await endpoint.closed_by_peer()
    # Only possible if the address was released.
    again = await Session.connect(await new_endpoint(server, stream_transport))
    assert again.address == first.address


async def test_oversized_length_just_waits_for_more_data(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    await endpoint.send_raw(bytes([6, 0x10, 9, 0x99, 0xFF, 0xFF]))
    with anyio.move_on_after(0.7) as scope:
        await endpoint.wait_closed()
    assert scope.cancelled_caught, "the server closed the connection"
