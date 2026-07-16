from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from config import settings
from core.exceptions import InvalidStateTransitionError
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)


class VerificationWorker:

    def __init__(self, coordinator: OrderCoordinator) -> None:
        self._coordinator = coordinator

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic_sublot_delivered,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_verification_worker_group_id,
            auto_offset_reset=settings.kafka_auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()
        logger.info("VerificationWorker consumer started")
        try:
            async for message in consumer:
                try:
                    await self._handle_message(message.value)
                    await consumer.commit()
                except Exception as exc:
                    logger.error(
                        "VerificationWorker: unhandled error on %s — offset not "
                        "committed, message will redeliver: %s",
                        message.topic, exc, exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.info("VerificationWorker consumer cancelled")
        except KafkaError as exc:
            logger.critical("VerificationWorker Kafka consumer error: %s", exc)
            raise
        finally:
            await consumer.stop()

    async def _handle_message(self, payload: dict) -> None:
        try:
            await self._coordinator.on_sublot_delivered(
                payload["sublot_id"],
                payload["order_id"],
                payload["delivered_qty"],
            )
        except InvalidStateTransitionError:
            logger.info("VerificationWorker: skipping replay for payload=%s", payload)
