from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaProducer

from config import settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def start_producer() -> None:
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode(),
    )
    await _producer.start()


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


def get_producer() -> AIOKafkaProducer:
    if _producer is None:
        raise RuntimeError("Kafka producer not started — call start_producer() first")
    return _producer


async def publish_order_placed(order_id: int, correlation_id: str) -> None:
    await get_producer().send_and_wait(
        settings.kafka_topic_order_placed,
        value={"order_id": order_id, "correlation_id": correlation_id},
        key=str(order_id).encode(),
    )
    logger.info("Published order.placed order_id=%d", order_id)


async def publish_sublot_delivered(
    sublot_id: int, order_id: int, delivered_qty: int
) -> None:
    await get_producer().send_and_wait(
        settings.kafka_topic_sublot_delivered,
        value={
            "sublot_id": sublot_id,
            "order_id": order_id,
            "delivered_qty": delivered_qty,
        },
        key=str(order_id).encode(),
    )
    logger.info("Published sublot.delivered sublot_id=%d order_id=%d", sublot_id, order_id)


async def publish_sublot_assigned(
    sublot_id: int, order_id: int, workshop_id: int, product_type: str, qty_assigned: int
) -> None:
    await get_producer().send_and_wait(
        settings.kafka_topic_sublot_assigned,
        value={
            "sublot_id": sublot_id,
            "order_id": order_id,
            "workshop_id": workshop_id,
            "product_type": product_type,
            "qty_assigned": qty_assigned,
        },
        key=str(workshop_id).encode(),
    )
    logger.info(
        "Published sublot.assigned sublot_id=%d workshop_id=%d", sublot_id, workshop_id
    )
