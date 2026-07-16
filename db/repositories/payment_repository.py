from __future__ import annotations

import asyncpg

from core.domain import PaymentDraft, SettlementResult


class PaymentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_settlement(
        self,
        order_id: int,
        sublot_ids: list[int],
        result: SettlementResult,
    ) -> None:
        if len(sublot_ids) != len(result.payments):
            raise ValueError("sublot_ids count must match payments count")

        await self._pool.executemany(
            """
            INSERT INTO payments
                (order_id, workshop_id, sublot_id, base_amount, penalty, net_amount)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (sublot_id) DO NOTHING
            """,
            [
                (
                    order_id,
                    payment.workshop_id,
                    sublot_id,
                    payment.base_amount,
                    payment.penalty,
                    payment.net_amount,
                )
                for sublot_id, payment in zip(sublot_ids, result.payments)
            ],
        )

    async def get_for_order(self, order_id: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            "SELECT * FROM payments WHERE order_id = $1 ORDER BY workshop_id",
            order_id,
        )
