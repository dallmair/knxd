"""Many tunnel clients on all three servers sending at the same time.

Every telegram must reach every other client exactly once, telegrams from one
sender must keep their order, and each sender must get exactly one L_Data.con
per telegram.
"""

from __future__ import annotations

import anyio

from knxnet_wire import L_DATA_CON, L_DATA_IND, L_DATA_REQ, group_address, group_write_cemi
from server_config import TRANSPORTS, NewEndpoint, StartServer
from tunnel_session import Session

CLIENTS_PER_TRANSPORT = 3
TELEGRAMS = 20  # per client; the value is 6 bits wide
QUIET = 1.0


def summarize(frames: list[bytes]) -> dict[tuple[int, int], list[int]]:
    """Values per (message code, destination group), in arrival order."""
    found: dict[tuple[int, int], list[int]] = {}
    for cemi in frames:
        found.setdefault((cemi[0], int.from_bytes(cemi[6:8], "big")), []).append(cemi[10])
    return found


async def test_concurrent_senders_reach_every_client_exactly_once_in_order(
    start_server: StartServer, new_endpoint: NewEndpoint
) -> None:
    kinds = [kind for kind in TRANSPORTS for _ in range(CLIENTS_PER_TRANSPORT)]
    server = await start_server(addresses=len(kinds))
    sessions = [await Session.connect(await new_endpoint(server, kind)) for kind in kinds]
    received: dict[int, list[bytes]] = {}

    async def client(index: int, session: Session) -> None:
        group = group_address(1, 2, index)
        for value in range(TELEGRAMS):
            await session.send_cemi(group_write_cemi(L_DATA_REQ, group, value=0x80 | value))
        received[index] = await session.cemi_stream(QUIET)

    with anyio.fail_after(60):
        async with anyio.create_task_group() as tasks:
            for index, session in enumerate(sessions):
                tasks.start_soon(client, index, session)

    values = [0x80 | value for value in range(TELEGRAMS)]
    for index, session in enumerate(sessions):
        expected = {
            (L_DATA_CON if other == index else L_DATA_IND, group_address(1, 2, other)): values
            for other in range(len(sessions))
        }
        assert summarize(received[index]) == expected, f"client {index} on {session.endpoint.name}"
