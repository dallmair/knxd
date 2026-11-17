"""KNX telegrams moving through tunnel connections on the UDP, TCP and Unix tunnelling servers.

Several tunnel clients are connected to one knxd (which has only a dummy
backend), so a telegram sent by one client must show up at the others.
"""

from __future__ import annotations

import struct

from knxnet_wire import (
    L_BUSMON_IND,
    L_DATA_CON,
    L_DATA_IND,
    L_DATA_REQ,
    TUNNEL_BUSMONITOR,
    format_individual,
    group_address,
    group_write_cemi,
)
from server_config import FIRST_ADDRESS, NewEndpoint, Server
from tunnel_session import Session

GROUP = group_address(1, 2, 3)
QUIET = 0.7


def fields(cemi: bytes) -> tuple[int, int, int, bytes]:
    """(message code, source, destination, APDU incl. length byte) of a cEMI L_Data frame."""
    assert cemi[1] == 0, "additional info is not expected"
    source, destination = struct.unpack(">HH", cemi[4:8])
    return cemi[0], source, destination, cemi[8:]


async def test_request_is_confirmed_with_the_clients_own_address(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, transport))
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP, source=0))
    confirmation = await sender.next_cemi()
    assert confirmation is not None, "no L_Data.con"
    assert fields(confirmation) == (L_DATA_CON, FIRST_ADDRESS, GROUP, bytes([1, 0x00, 0x81]))
    assert await sender.cemi_stream(QUIET) == [], "the sender must not get its own telegram back as L_Data.ind"


async def test_request_reaches_the_other_tunnel_clients(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, transport))
    receivers = [await Session.connect(await new_endpoint(knxd_server, kind)) for kind in ("udp", "tcp", "unix")]
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP))
    for receiver in receivers:
        indication = await receiver.next_cemi()
        assert indication is not None, f"no L_Data.ind on {receiver.endpoint.name}"
        assert fields(indication) == (L_DATA_IND, FIRST_ADDRESS, GROUP, bytes([1, 0x00, 0x81]))
        assert await receiver.cemi_stream(QUIET) == []


async def test_nonzero_source_address_is_kept(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, transport))
    receiver = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    other = 0x2345
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP, source=other))
    indication = await receiver.next_cemi()
    assert indication is not None
    assert fields(indication)[1] == other, format_individual(fields(indication)[1])
    confirmation = await sender.next_cemi()
    assert confirmation is not None
    assert fields(confirmation)[1] == other


async def test_indication_carries_the_value(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, transport))
    receiver = await Session.connect(await new_endpoint(knxd_server, "unix"))
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP, value=0x85))
    indication = await receiver.next_cemi()
    assert indication is not None
    assert fields(indication)[3] == bytes([1, 0x00, 0x85])


async def test_telegrams_arrive_in_the_order_they_were_sent(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    sender = await Session.connect(await new_endpoint(knxd_server, transport))
    receiver = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    for value in (0x80, 0x81, 0x82, 0x83, 0x84):
        await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP, value=value))
    received = [fields(c)[3][2] for c in await receiver.cemi_stream(QUIET)]
    assert received == [0x80, 0x81, 0x82, 0x83, 0x84]


async def test_busmonitor_sees_bus_traffic(knxd_server: Server, new_endpoint: NewEndpoint, transport: str) -> None:
    monitor = await Session.connect(await new_endpoint(knxd_server, transport), TUNNEL_BUSMONITOR)
    sender = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP))
    frame = await monitor.next_cemi()
    assert frame is not None, "no L_Busmon.ind"
    assert frame[0] == L_BUSMON_IND
    # cEMI additional info: 7 bytes, starting with type 03 (busmonitor status, length 1).
    assert frame[1] == 7, "additional info length"
    assert frame[2:5] == bytes([0x03, 0x01, 0x01]), "additional info starts with the busmonitor status"
    assert struct.pack(">H", GROUP) in frame


async def test_busmonitor_gets_no_data_frames_and_normal_clients_get_no_busmon_frames(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    monitor = await Session.connect(await new_endpoint(knxd_server, transport), TUNNEL_BUSMONITOR)
    sender = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    receiver = await Session.connect(await new_endpoint(knxd_server, "unix"))
    await sender.send_cemi(group_write_cemi(L_DATA_REQ, GROUP))
    assert [c[0] for c in await monitor.cemi_stream(QUIET)] == [L_BUSMON_IND]
    assert [c[0] for c in await receiver.cemi_stream(QUIET)] == [L_DATA_IND]


async def test_busmonitor_connection_is_not_confirmed_or_addressed(
    knxd_server: Server, new_endpoint: NewEndpoint, transport: str
) -> None:
    monitor = await Session.connect(await new_endpoint(knxd_server, transport), TUNNEL_BUSMONITOR)
    receiver = await Session.connect(await new_endpoint(knxd_server, "tcp"))
    await receiver.send_cemi(group_write_cemi(L_DATA_REQ, GROUP))
    await monitor.cemi_stream(QUIET)
    assert await monitor.connection_state() == 0
