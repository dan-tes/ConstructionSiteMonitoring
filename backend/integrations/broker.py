"""RabbitMQ transport for the analysis pipeline.

One topic exchange (`EXCHANGE_NAME`), one queue per consumer. See
`README.md` for the topology diagram and the reasoning behind it. This
module only knows how to move bytes over the exchange — it does not know
what a "vision result" means; that belongs to `analysis.py` (backend side)
and to each service's own worker.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractRobustConnection

from config import settings

log = logging.getLogger("csm.broker")

EXCHANGE_NAME = "csm.analysis"

# Routing keys.
VISION_COMMAND = "vision.command"
VISION_RESULT = "vision.result"
PHASE_COMMAND = "phase.command"
PHASE_RESULT = "phase.result"
DELAY_COMMAND = "delay.command"
DELAY_RESULT = "delay.result"

# Queue a given routing key is delivered to. Command queues are consumed by
# the matching service; result queues are consumed by the backend.
QUEUE_NAMES: dict[str, str] = {
    VISION_COMMAND: "vision.command.q",
    VISION_RESULT: "backend.vision.result.q",
    PHASE_COMMAND: "phase.command.q",
    PHASE_RESULT: "backend.phase.result.q",
    DELAY_COMMAND: "delay.command.q",
    DELAY_RESULT: "backend.delay.result.q",
}

_connection: AbstractRobustConnection | None = None
_channel: aio_pika.abc.AbstractChannel | None = None
_exchange: aio_pika.abc.AbstractExchange | None = None


async def _get_exchange() -> aio_pika.abc.AbstractExchange:
    global _connection, _channel, _exchange
    if _connection is None or _connection.is_closed:
        _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    if _channel is None or _channel.is_closed:
        _channel = await _connection.channel()
        await _channel.set_qos(prefetch_count=10)
    if _exchange is None:
        _exchange = await _channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
    return _exchange


async def publish(routing_key: str, body: bytes) -> None:
    """Publish a persistent message under `routing_key` on the shared exchange."""
    exchange = await _get_exchange()
    await exchange.publish(
        aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=routing_key,
    )


async def start_consumer(
    routing_key: str, handler: Callable[[bytes], Awaitable[None]]
) -> None:
    """Bind `QUEUE_NAMES[routing_key]` to the exchange and start consuming it.

    `handler` receives the raw message body. It acks the message on success;
    an exception nacks it without requeue (no dead-letter routing is set up
    yet — see the Open items in README.md — so a message a handler keeps
    failing on is currently just dropped, and logged loudly).
    """
    exchange = await _get_exchange()
    assert _channel is not None  # set by _get_exchange()
    channel = _channel
    queue_name = QUEUE_NAMES[routing_key]
    queue = await channel.declare_queue(queue_name, durable=True)
    await queue.bind(exchange, routing_key)

    async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(ignore_processed=True):
            try:
                await handler(message.body)
            except Exception:
                log.exception(
                    "handler failed for routing_key=%s queue=%s — message dropped",
                    routing_key,
                    queue_name,
                )
                raise

    await queue.consume(_on_message)
    log.info("consuming %s from %s", routing_key, queue_name)


async def close() -> None:
    global _connection, _channel, _exchange
    if _connection is not None and not _connection.is_closed:
        await _connection.close()
    _connection = _channel = _exchange = None
