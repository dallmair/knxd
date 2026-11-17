"""UDP-only: requests the `ets_router` server refuses, as visible in the status of its ACK."""

from __future__ import annotations

from typing import TYPE_CHECKING

from knxnet_transport import UdpEndpoint
from knxnet_wire import (
    DEVICE_CONFIGURATION_ACK,
    E_NO_ERROR,
    E_TUNNELLING_LAYER,
    L_DATA_CON,
    L_DATA_REQ,
    TUNNEL_BUSMONITOR,
    TUNNEL_LINKLAYER,
    TUNNELLING_ACK,
    config_request,
    group_address,
    group_write_cemi,
    parse_sequenced,
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


async def test_tunnel_request_on_a_management_channel_is_refused_in_the_ack(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint, None)
    await endpoint.send(tunnel_request(session.channel, 0, REQUEST))
    assert parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK).status == E_TUNNELLING_LAYER


async def test_config_request_on_a_tunnel_channel_is_refused_in_the_ack(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(config_request(session.channel, 0, PROP_READ))
    assert parse_sequenced(await endpoint.request_next(), DEVICE_CONFIGURATION_ACK).status == E_TUNNELLING_LAYER


async def test_tunnel_request_on_a_busmonitor_channel_is_refused_in_the_ack(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint, TUNNEL_BUSMONITOR)
    await endpoint.send(tunnel_request(session.channel, 0, REQUEST))
    assert parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK).status == E_TUNNELLING_LAYER


async def test_unparseable_cemi_is_refused_in_the_ack(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    session, endpoint = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(session.channel, 0, bytes([0x11, 0x00])))
    ack = parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK)
    assert (ack.seq, ack.status) == (0, E_TUNNELLING_LAYER)
    # A refused request does not consume its sequence number: 0 is still expected.
    await endpoint.send(tunnel_request(session.channel, 0, REQUEST))
    assert parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK).status == E_NO_ERROR


async def test_unexpected_cemi_message_code_is_acked_but_dropped(
    knxd_server: Server, new_endpoint: NewEndpoint
) -> None:
    sender, endpoint = await udp_session(knxd_server, new_endpoint)
    receiver, _ = await udp_session(knxd_server, new_endpoint)
    await endpoint.send(tunnel_request(sender.channel, 0, group_write_cemi(L_DATA_CON, GROUP)))
    assert parse_sequenced(await endpoint.request_next(), TUNNELLING_ACK).status == E_NO_ERROR
    assert await receiver.cemi_stream(0.7) == []
