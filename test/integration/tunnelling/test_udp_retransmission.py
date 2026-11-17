"""UDP-only: retransmission of unacknowledged server requests (03_08_04 clause 2.6.1)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
import pytest

from knxnet_transport import UdpEndpoint
from knxnet_wire import (
    DISCONNECT_REQUEST,
    E_CONNECTION_ID,
    E_TUNNELLING_LAYER,
    L_DATA_REQ,
    TUNNEL_LINKLAYER,
    TUNNELLING_REQUEST,
    group_address,
    group_write_cemi,
    parse_sequenced,
    tunnel_ack,
)
from tunnel_session import Session

if TYPE_CHECKING:
    from server_config import NewEndpoint, Server

REQUEST = group_write_cemi(L_DATA_REQ, group_address(1, 2, 3))
RETRY_AFTER = 1.0  # TUNNELING_REQUEST_TIMEOUT


async def sender_and_receiver(server: Server, new_endpoint: NewEndpoint) -> tuple[Session, Session, UdpEndpoint]:
    """Two tunnels; `receiver`'s endpoint is used raw, so its requests stay unacknowledged."""
    endpoints = [await new_endpoint(server, "udp"), await new_endpoint(server, "udp")]
    sender = await Session.connect(endpoints[0], TUNNEL_LINKLAYER)
    receiver = await Session.connect(endpoints[1], TUNNEL_LINKLAYER)
    assert isinstance(endpoints[1], UdpEndpoint)
    return sender, receiver, endpoints[1]


async def test_unanswered_server_request_is_repeated_once_with_the_same_sequence(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, _, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    first = await endpoint.request_next()
    started = time.monotonic()
    with anyio.fail_after(RETRY_AFTER + 1):
        second = await endpoint.recv()
    assert second == first, "the repetition must be identical (same sequence counter)"
    assert RETRY_AFTER - 0.2 <= time.monotonic() - started <= RETRY_AFTER + 0.8
    await endpoint.expect_silence(RETRY_AFTER + 1.5)  # a request is repeated only once


@pytest.mark.deviation
async def test_connection_is_dropped_silently_when_the_repetition_is_not_acked(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    # 03_08_04 2.6.1 says the server shall then send a DISCONNECT_REQUEST; it just forgets the channel.
    sender, receiver, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    services = [f.service for f in await endpoint.drain(3.5)]
    assert services == [TUNNELLING_REQUEST, TUNNELLING_REQUEST]
    assert DISCONNECT_REQUEST not in services
    assert await receiver.connection_state() == E_CONNECTION_ID


@pytest.mark.deviation
async def test_ack_with_error_status_is_ignored_like_a_missing_ack(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, receiver, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    request = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
    await endpoint.send(tunnel_ack(receiver.channel, request.seq, E_TUNNELLING_LAYER))
    with anyio.fail_after(RETRY_AFTER + 1):
        repeat = await endpoint.recv()
    assert repeat.service == TUNNELLING_REQUEST, "the request is still outstanding"
    await endpoint.expect_silence(RETRY_AFTER + 1.5)


async def test_ack_with_wrong_sequence_does_not_complete_the_request(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, receiver, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    first = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
    await endpoint.send(tunnel_ack(receiver.channel, (first.seq + 1) & 0xFF))
    with anyio.fail_after(RETRY_AFTER + 1):
        repeat = await endpoint.recv()
    assert parse_sequenced(repeat, TUNNELLING_REQUEST).seq == first.seq


async def test_second_server_request_waits_for_the_ack_of_the_first(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, receiver, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    await sender.send_cemi(REQUEST)
    first = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
    await endpoint.expect_silence(0.5)  # only one request is outstanding
    await endpoint.send(tunnel_ack(receiver.channel, first.seq))
    second = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
    assert second.seq == (first.seq + 1) & 0xFF


async def test_acknowledged_request_is_not_repeated(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    sender, receiver, endpoint = await sender_and_receiver(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)
    request = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
    await endpoint.send(tunnel_ack(receiver.channel, request.seq))
    await endpoint.expect_silence(RETRY_AFTER + 1)
    assert await receiver.connection_state() == 0
