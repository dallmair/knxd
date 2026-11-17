"""A connected tunnelling channel as seen from the client side.

Hides the one real transport difference: on UDP every TUNNELLING_REQUEST and
DEVICE_CONFIGURATION_REQUEST is answered with an ACK and carries a sequence
counter, on TCP and Unix sockets there are no ACKs (03_08_04, 2.6.2).
"""

from __future__ import annotations

import anyio

from knxnet_transport import DEFAULT_WAIT, Endpoint, UdpEndpoint
from knxnet_wire import (
    CONNECTIONSTATE_REQUEST,
    CONNECTIONSTATE_RESPONSE,
    DEVICE_CONFIGURATION_ACK,
    DEVICE_CONFIGURATION_REQUEST,
    DISCONNECT_REQUEST,
    DISCONNECT_RESPONSE,
    E_NO_ERROR,
    HOST_UDP,
    TUNNEL_LINKLAYER,
    TUNNELLING_ACK,
    TUNNELLING_REQUEST,
    ConnectResponse,
    Frame,
    channel_request,
    config_ack,
    config_request,
    connect_request,
    management_cri,
    parse_channel_status,
    parse_connect_response,
    parse_sequenced,
    tunnel_ack,
    tunnel_cri,
    tunnel_request,
)


async def connect(endpoint: Endpoint, cri: bytes) -> ConnectResponse:
    """Send a CONNECT_REQUEST and parse the answer, without asserting on its status."""
    control = endpoint.own_hpai() if isinstance(endpoint, UdpEndpoint) else None
    return parse_connect_response(await endpoint.request(connect_request(endpoint.protocol, cri, control, control)))


class Session:
    """One open channel. `tx` and `rx` are the next sequence numbers sent and expected."""

    def __init__(self, endpoint: Endpoint, response: ConnectResponse, management: bool = False) -> None:
        self.endpoint = endpoint
        self.channel = response.channel
        self.response = response
        self.management = management
        self.tx = 0
        self.rx = 0
        self.pending: list[bytes] = []  # requests from the server that arrived while waiting for an ACK

    @classmethod
    async def connect(cls, endpoint: Endpoint, layer: int | None = TUNNEL_LINKLAYER) -> Session:
        """Open a tunnel. `layer=None` opens a management channel instead."""
        cri = management_cri() if layer is None else tunnel_cri(layer)
        response = await connect(endpoint, cri)
        assert response.status == E_NO_ERROR, f"connect failed with status {response.status:#04x}"
        return cls(endpoint, response, management=layer is None)

    @property
    def udp(self) -> bool:
        return self.endpoint.protocol == HOST_UDP

    @property
    def address(self) -> int:
        return self.response.address

    async def _wait_for(self, service: int, what: str) -> Frame:
        """Return the next frame of `service`.

        On UDP the server may send tunnelling requests of its own in between
        (for example an L_Data.con); those are acknowledged and kept for `next_cemi`.
        """
        with anyio.move_on_after(DEFAULT_WAIT):
            while (frame := await self.endpoint.recv()) is not None:
                if frame.service == service:
                    return frame
                self.pending.append(await self._accept(frame))
        raise AssertionError(what)

    # -- control endpoint services -------------------------------------------------------------

    def _control(self) -> bytes | None:
        return self.endpoint.own_hpai() if isinstance(self.endpoint, UdpEndpoint) else None

    async def _control_reply(self, request: Frame, expected_service: int) -> tuple[int, int]:
        """Send a control request and return (channel, status) of the response."""
        await self.endpoint.send(request)
        reply = await self._wait_for(expected_service, f"no reply to service {request.service:#06x}")
        return parse_channel_status(reply, expected_service)

    async def connection_state(self, channel: int | None = None) -> int:
        """Send CONNECTIONSTATE_REQUEST and return the status of the response."""
        number = self.channel if channel is None else channel
        frame = channel_request(CONNECTIONSTATE_REQUEST, self.endpoint.protocol, number, self._control())
        return (await self._control_reply(frame, CONNECTIONSTATE_RESPONSE))[1]

    async def disconnect(self, channel: int | None = None) -> int:
        """Send DISCONNECT_REQUEST and return the status of the response."""
        number = self.channel if channel is None else channel
        frame = channel_request(DISCONNECT_REQUEST, self.endpoint.protocol, number, self._control())
        return (await self._control_reply(frame, DISCONNECT_RESPONSE))[1]

    # -- tunnelling ------------------------------------------------------------------------------

    async def send_cemi(self, cemi: bytes, seq: int | None = None) -> None:
        """Send a TUNNELLING_REQUEST (or DEVICE_CONFIGURATION_REQUEST) and, on UDP, consume its ACK.

        On UDP the server may send its own requests (such as L_Data.con for an earlier
        telegram) before the ACK. Those are acknowledged and kept for `next_cemi`.
        Use `send_cemi_raw` to look at the ACK yourself.
        """
        number = self.tx if seq is None else seq
        await self.send_cemi_raw(cemi, number)
        if self.udp:
            ack_service = DEVICE_CONFIGURATION_ACK if self.management else TUNNELLING_ACK
            frame = await self._wait_for(ack_service, "no ACK from server")
            ack = parse_sequenced(frame, ack_service)
            assert (ack.channel, ack.seq, ack.status) == (self.channel, number, E_NO_ERROR), (
                f"bad ACK {frame.body.hex()}"
            )
        self.tx = (number + 1) & 0xFF

    async def send_cemi_raw(self, cemi: bytes, seq: int | None = None) -> None:
        number = self.tx if seq is None else seq
        build = config_request if self.management else tunnel_request
        await self.endpoint.send(build(self.channel, number, cemi))

    async def _accept(self, frame: Frame) -> bytes:
        """Check a request from the server and, on UDP, ACK it. Return its cEMI."""
        service = DEVICE_CONFIGURATION_REQUEST if self.management else TUNNELLING_REQUEST
        request = parse_sequenced(frame, service)
        assert request.channel == self.channel, f"request for channel {request.channel}"
        if self.udp:
            assert request.seq == self.rx, f"server used sequence {request.seq}, expected {self.rx}"
            build = config_ack if self.management else tunnel_ack
            await self.endpoint.send(build(self.channel, request.seq))
            self.rx = (self.rx + 1) & 0xFF
        return request.cemi

    async def next_cemi(self) -> bytes | None:
        """Wait up to DEFAULT_WAIT seconds for the next request from the server; ACK it on UDP. None on timeout."""
        if self.pending:
            return self.pending.pop(0)
        frame = None
        with anyio.move_on_after(DEFAULT_WAIT):
            frame = await self.endpoint.recv()
        return None if frame is None else await self._accept(frame)

    async def cemi_stream(self, duration: float) -> list[bytes]:
        """All requests that arrive until there are `duration` seconds of silence."""
        found, self.pending = self.pending, []
        while True:
            frame = None
            with anyio.move_on_after(duration):
                frame = await self.endpoint.recv()
            if frame is None:
                return found
            found.append(await self._accept(frame))
