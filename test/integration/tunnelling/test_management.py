"""Management connections (DEVICE_MGMT_CONNECTION): the M_PropRead.req handler on all three servers.

Only reading property 0 (the property count) of object 0 is answered with data.
Every other read gets a zero-element answer. The handler is duplicated in
`ConnState::config_request` (UDP) and `TunServiceConfig` (TCP/Unix).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from knxnet_wire import (
    DEVICE_CONFIGURATION_ACK,
    E_DATA_CONNECTION,
    E_NO_ERROR,
    L_DATA_REQ,
    M_PROPREAD_CON,
    M_PROPREAD_REQ,
    config_request,
    group_address,
    group_write_cemi,
    parse_sequenced,
)
from tunnel_session import Session

if TYPE_CHECKING:
    from server_config import NewEndpoint, Server


def prop_read(obj: int, objno: int, prop: int, count: int = 1, start: int = 1) -> bytes:
    return bytes([M_PROPREAD_REQ, obj >> 8, obj & 0xFF, objno, prop, (count << 4) | (start >> 8), start & 0xFF])


async def read(session: Session, request: bytes) -> bytes:
    await session.send_cemi(request)
    answer = await session.next_cemi()
    assert answer is not None, "no M_PropRead.con"
    return answer


async def management(server: Server, new_endpoint: NewEndpoint, transport: str) -> Session:
    return await Session.connect(await new_endpoint(server, transport), None)


async def test_property_zero_of_object_zero_returns_a_zero_value(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await management(knxd_server, new_endpoint, transport)
    answer = await read(session, prop_read(0, 0, 0, count=1, start=0))
    assert answer == bytes([M_PROPREAD_CON, 0, 0, 0, 0, 0x10, 0x00, 0, 0])


async def test_other_properties_return_zero_elements(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await management(knxd_server, new_endpoint, transport)
    answer = await read(session, prop_read(0, 0, 1, count=1, start=1))
    assert answer == bytes([M_PROPREAD_CON, 0, 0, 0, 1, 0x00, 0x01, 0])


async def test_other_objects_return_zero_elements(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    session = await management(knxd_server, new_endpoint, transport)
    answer = await read(session, prop_read(0x0102, 3, 0, count=1, start=0))
    assert answer == bytes([M_PROPREAD_CON, 0x01, 0x02, 3, 0, 0x00, 0x00, 0])


@pytest.mark.deviation
async def test_start_index_above_255_loses_its_high_bits(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    # The start index is computed as (b5 & 0x0f) | b6 instead of ((b5 & 0x0f) << 8) | b6,
    # so a request for index 0x105 is answered as index 5.
    session = await management(knxd_server, new_endpoint, transport)
    answer = await read(session, prop_read(0, 0, 5, count=1, start=0x105))
    assert answer == bytes([M_PROPREAD_CON, 0, 0, 0, 5, 0x00, 0x05, 0])


async def test_repeated_reads_keep_working(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    session = await management(knxd_server, new_endpoint, transport)
    for _ in range(5):
        assert (await read(session, prop_read(0, 0, 0, start=0)))[0] == M_PROPREAD_CON


@pytest.mark.parametrize(
    "request_bytes",
    [
        bytes([M_PROPREAD_REQ, 0, 0, 0, 0, 0x10]),  # too short
        bytes([M_PROPREAD_REQ, 0, 0, 0, 0, 0x10, 0, 0]),  # too long
        bytes([0xF6, 0, 0, 0, 0, 0x10, 0]),  # M_PropWrite.req is not handled
        bytes([L_DATA_REQ, 0, 0, 0, 0, 0, 0]),
    ],
    ids=["short", "long", "other-service", "tunnel-code"],
)
async def test_unsupported_management_requests_get_no_answer(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str, request_bytes: bytes
) -> None:
    session = await management(knxd_server, new_endpoint, transport)
    await session.send_cemi_raw(request_bytes, 0)
    if session.udp:
        ack = parse_sequenced(await session.endpoint.request_next(), DEVICE_CONFIGURATION_ACK)
        assert ack.status == E_DATA_CONNECTION
    assert await session.cemi_stream(0.7) == []


async def test_management_channel_does_not_see_bus_traffic(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    channel = await management(knxd_server, new_endpoint, transport)
    sender = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, group_address(1, 2, 3)))
    assert await channel.cemi_stream(0.7) == []


async def test_udp_acks_a_management_request_with_its_sequence(knxd_server: Server, new_endpoint: NewEndpoint) -> None:
    session = await management(knxd_server, new_endpoint, "udp")
    await session.endpoint.send(config_request(session.channel, 0, prop_read(0, 0, 0, start=0)))
    ack = parse_sequenced(await session.endpoint.request_next(), DEVICE_CONFIGURATION_ACK)
    assert (ack.channel, ack.seq, ack.status) == (session.channel, 0, E_NO_ERROR)
