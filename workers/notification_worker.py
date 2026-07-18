from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from config import settings
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)


class NotificationWorker:

    def __init__(self, coordinator: OrderCoordinator) -> None:
        self._coordinator = coordinator

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic_sublot_assigned,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_notification_worker_group_id,
            auto_offset_reset=settings.kafka_auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            **settings.kafka_client_kwargs,
        )
        await consumer.start()
        logger.info("NotificationWorker consumer started")
        try:
            async for message in consumer:
                try:
                    await self._handle_message(message.value)
                    await consumer.commit()
                except Exception as exc:
                    logger.error(
                        "NotificationWorker: unhandled error on %s — offset not "
                        "committed, message will redeliver: %s",
                        message.topic, exc, exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.info("NotificationWorker consumer cancelled")
        except KafkaError as exc:
            logger.critical("NotificationWorker Kafka consumer error: %s", exc)
            raise
        finally:
            await consumer.stop()

    async def _handle_message(self, payload: dict) -> None:
        await self._coordinator.on_sublot_assigned(
            workshop_id=payload["workshop_id"],
            order_id=payload["order_id"],
            sublot_id=payload["sublot_id"],
            product_type=payload["product_type"],
            qty_assigned=payload["qty_assigned"],
        )
