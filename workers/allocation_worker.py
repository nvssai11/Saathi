from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from config import settings
from core.exceptions import InvalidStateTransitionError
from events.producer import publish_sublot_assigned
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)


class AllocationWorker:

    def __init__(self, coordinator: OrderCoordinator) -> None:
        self._coordinator = coordinator

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic_order_placed,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_allocation_worker_group_id,
            auto_offset_reset=settings.kafka_auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            **settings.kafka_client_kwargs,
        )
        await consumer.start()
        logger.info("AllocationWorker consumer started")
        try:
            async for message in consumer:
                try:
                    await self._handle_message(message.value)
                    await consumer.commit()
                except Exception as exc:
                    logger.error(
                        "AllocationWorker: unhandled error on %s — offset not "
                        "committed, message will redeliver: %s",
                        message.topic, exc, exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.info("AllocationWorker consumer cancelled")
        except KafkaError as exc:
            logger.critical("AllocationWorker Kafka consumer error: %s", exc)
            raise
        finally:
            await consumer.stop()

    async def _handle_message(self, payload: dict) -> None:
        try:
            assignments = await self._coordinator.on_order_placed(payload["order_id"])
        except InvalidStateTransitionError:
            logger.info("AllocationWorker: skipping replay for payload=%s", payload)
            return

        for assignment in assignments:
            try:
                await publish_sublot_assigned(
                    sublot_id=assignment.sublot_id,
                    order_id=assignment.order_id,
                    workshop_id=assignment.workshop_id,
                    product_type=assignment.product_type,
                    qty_assigned=assignment.qty_assigned,
                )
            except Exception:
                logger.error(
                    "AllocationWorker: failed to publish sublot.assigned for "
                    "sublot_id=%s order_id=%s — the sub-lot is allocated but its "
                    "notification did not fire; recover with "
                    "POST /admin/orders/%s/republish-notifications",
                    assignment.sublot_id, assignment.order_id, assignment.order_id,
                    exc_info=True,
                )
