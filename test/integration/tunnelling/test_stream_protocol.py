"""TCP and Unix socket tunnelling (`tcptunsrv`, `unixtunsrv`): no ACKs, no sequence checks.

03_08_04 clause 2.6.2: no TUNNELLING_ACK is sent or evaluated and the sequence
counter is not checked. Several channels share one connection.
"""

from __future__ import annotations

import pytest

from knxnet_wire import (
    DEVICE_CONFIGURATION_ACK,
    E_CONNECTION_ID,
    E_NO_ERROR,
    L_DATA_CON,
    L_DATA_IND,
    TUNNELLING_ACK,
    TUNNELLING_REQUEST,
    Frame,
    config_request,
    group_write_cemi,
    parse_sequenced,
    tunnel_ack,
    tunnel_request,
)
from server_config import PROP_READ, REQUEST, NewEndpoint, Server, stream
from tunnel_session import Session


async def test_requests_are_not_acknowledged(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    await endpoint.send(tunnel_request(session.channel, 0, REQUEST))
    frames = await endpoint.drain(0.7)
    assert [f.service for f in frames] == [TUNNELLING_REQUEST], "only the L_Data.con, no TUNNELLING_ACK"
    assert parse_sequenced(frames[0], TUNNELLING_REQUEST).cemi[0] == L_DATA_CON


async def test_sequence_counter_of_the_client_is_not_checked(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    session = await Session.connect(await stream(knxd_server, new_endpoint, stream_transport))
    receiver = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    for seq in (0, 0, 7, 200, 1):
        await session.send_cemi_raw(REQUEST, seq)
    assert len(await receiver.cemi_stream(0.7)) == 5


async def test_server_counts_its_own_sequence_without_waiting_for_acks(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    receiver = await Session.connect(await stream(knxd_server, new_endpoint, stream_transport))
    for _ in range(3):
        await sender.send_cemi(REQUEST)
    seqs = [parse_sequenced(f, TUNNELLING_REQUEST).seq for f in await receiver.endpoint.drain(0.7)]
    assert seqs == [0, 1, 2]


async def test_acks_from_the_client_are_ignored(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    session = await Session.connect(endpoint)
    await endpoint.send(tunnel_ack(session.channel, 0))
    await endpoint.send(Frame(DEVICE_CONFIGURATION_ACK, bytes([4, session.channel, 0, 0])))
    await endpoint.expect_silence()
    assert await session.connection_state() == E_NO_ERROR


@pytest.mark.deviation
async def test_request_on_an_unknown_channel_is_answered_with_an_ack(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    # 03_08_04 2.6.2 says no ACK is sent on TCP, and 3.5 says traffic on an unknown channel is ignored.
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    await endpoint.send(tunnel_request(77, 0, REQUEST))
    ack = parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK)
    assert (ack.channel, ack.status) == (0, E_CONNECTION_ID)  # the channel field is 0, not 77
    await endpoint.send(config_request(77, 0, PROP_READ))
    reply = await endpoint.request_next()
    assert reply.service == DEVICE_CONFIGURATION_ACK
    assert reply.body == bytes([4, 0, 0, E_CONNECTION_ID])


async def test_refused_requests_are_dropped_silently(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    tunnel = await Session.connect(endpoint)
    management = await Session.connect(endpoint, None)
    await endpoint.send(tunnel_request(tunnel.channel, 0, bytes([0x11, 0x00])))  # unparseable cEMI
    await endpoint.send(tunnel_request(tunnel.channel, 0, group_write_cemi(L_DATA_CON, 5)))  # wrong message code
    await endpoint.send(config_request(tunnel.channel, 0, PROP_READ))  # management request on a tunnel
    await endpoint.send(tunnel_request(management.channel, 0, REQUEST))  # tunnel request on a management channel
    await endpoint.expect_silence()
    assert await tunnel.connection_state() == E_NO_ERROR


async def test_two_channels_on_one_connection_each_get_the_telegram(
    knxd_server: Server, new_endpoint: NewEndpoint, stream_transport: str
) -> None:
    endpoint = await stream(knxd_server, new_endpoint, stream_transport)
    first = await Session.connect(endpoint)
    second = await Session.connect(endpoint)
    other = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    await other.send_cemi(REQUEST)
    requests = [parse_sequenced(f, TUNNELLING_REQUEST) for f in await endpoint.drain(0.7)]
    assert sorted(r.channel for r in requests) == sorted([first.channel, second.channel])
    assert [r.cemi[0] for r in requests] == [L_DATA_IND, L_DATA_IND]
