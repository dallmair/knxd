"""UDP-only behaviour of the `ets_router` tunnelling server: sequence counters and ACKs.

03_08_04 clause 2.6.1. Retransmission is in `test_udp_retransmission.py`, refused
requests in `test_udp_refusals.py`. TCP and Unix connections have no ACKs; see
`test_stream_protocol.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from knxnet_transport import UdpEndpoint
from knxnet_wire import (
    E_NO_ERROR,
    L_DATA_CON,
    L_DATA_IND,
    L_DATA_REQ,
    TUNNEL_LINKLAYER,
    TUNNELLING_ACK,
    TUNNELLING_REQUEST,
    config_request,
    group_address,
    group_write_cemi,
    parse_sequenced,
    tunnel_ack,
    tunnel_request,
)
from tunnel_session import Session

if TYPE_CHECKING:
    from server_config import NewEndpoint, Server

GROUP = group_address(1, 2, 3)
REQUEST = group_write_cemi(L_DATA_REQ, GROUP)
PROP_READ = bytes([0xFC, 0, 0, 0, 0, 0x10, 0])


async def udp_session(
    server: Server, new_endpoint: NewEndpoint, layer: int | None = TUNNEL_LINKLAYER
) -> tuple[Session, UdpEndpoint]:
    endpoint = await new_endpoint(server, "udp")
    assert isinstance(endpoint, UdpEndpoint)
    return await Session.connect(endpoint, layer), endpoint


async def test_request_with_expected_sequence_is_acked(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(session.channel, 0, REQUEST))
    ack = parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK)
    assert (ack.channel, ack.seq, ack.status) == (session.channel, 0, E_NO_ERROR)


async def test_server_requests_start_at_sequence_zero_and_count_up(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, _ = await udp_session(knxd_server, new_endpoint)
    receiver, endpoint = await udp_session(knxd_server, new_endpoint)
    for _ in range(3):
        await sender.send_cemi(REQUEST)
    seqs: list[int] = []
    for _ in range(3):
        request = parse_sequenced(await endpoint.request_next(), TUNNELLING_REQUEST)
        seqs.append(request.seq)
        await endpoint.send(tunnel_ack(receiver.channel, request.seq))
    assert seqs == [0, 1, 2]


async def test_repeated_request_is_acked_again_but_not_processed_twice(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, endpoint = await udp_session(knxd_server, new_endpoint)
    receiver, _ = await udp_session(knxd_server, new_endpoint)
    await sender.send_cemi(REQUEST)  # sequence 0
    assert [c[0] for c in await sender.cemi_stream(0.5)] == [L_DATA_CON]
    await endpoint.send(tunnel_request(sender.channel, 0, REQUEST))  # the same sequence again: a lost ACK
    ack = parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK)
    assert (ack.seq, ack.status) == (0, E_NO_ERROR)
    assert [c[0] for c in await receiver.cemi_stream(0.7)] == [L_DATA_IND], (
        "the repeated telegram must not reach the bus again"
    )
    assert await sender.cemi_stream(0.5) == [], "and must not be confirmed again"


async def test_sequence_before_the_first_is_acked_and_discarded(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    # 0xFF is "expected - 1" when 0 is expected, so it is treated as a repetition.
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    receiver, _ = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(session.channel, 0xFF, REQUEST))
    ack = parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK)
    assert (ack.seq, ack.status) == (0xFF, E_NO_ERROR)
    assert await receiver.cemi_stream(0.7) == []


@pytest.mark.parametrize("seq", [2, 5, 0x80, 0xFE])
async def test_request_with_unexpected_sequence_is_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, seq: int
) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(session.channel, seq, REQUEST))
    await endpoint.expect_silence()
    await session.send_cemi(REQUEST, seq=0)  # the expected number did not move


async def test_sequence_counter_wraps_around(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    session, _ = await udp_session(knxd_server, new_endpoint)
    for seq in range(258):
        await session.send_cemi(REQUEST, seq=seq & 0xFF)
        await session.cemi_stream(0.0)  # keep the server's own requests acknowledged
    assert await session.connection_state() == E_NO_ERROR


async def test_unsolicited_ack_is_ignored(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_ack(session.channel, 0))
    await endpoint.send(tunnel_ack(99, 0))
    await endpoint.expect_silence()
    assert await session.connection_state() == E_NO_ERROR


async def test_unknown_channel_gets_no_reply(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    _, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(99, 0, REQUEST))
    await endpoint.send(config_request(99, 0, PROP_READ))
    await endpoint.expect_silence()
