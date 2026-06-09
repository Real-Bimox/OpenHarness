"""Async message queue for decoupled channel-agent communication."""

import asyncio

from openharness.channels.bus.events import InboundMessage, OutboundMessage

DEFAULT_QUEUE_MAXSIZE = 1000
QUEUE_PUT_TIMEOUT_SECONDS = 5.0


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self, *, maxsize: int = DEFAULT_QUEUE_MAXSIZE):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=maxsize)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=maxsize)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        try:
            await asyncio.wait_for(self.inbound.put(msg), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Inbound channel queue is full") from exc

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        try:
            await asyncio.wait_for(self.outbound.put(msg), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Outbound channel queue is full") from exc

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
