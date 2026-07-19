from __future__ import annotations

from decimal import Decimal

import asyncpg


class BuyerPaymentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_advance(self, order_id: int, amount: Decimal) -> None:
        await self._pool.execute(
            """
            INSERT INTO buyer_payments (order_id, kind, amount)
            VALUES ($1, 'ADVANCE', $2)
            ON CONFLICT (order_id, kind) DO NOTHING
            """,
            order_id, amount,
        )

    async def create_balance(self, order_id: int, amount: Decimal) -> None:
        await self._pool.execute(
            """
            INSERT INTO buyer_payments (order_id, kind, amount)
            VALUES ($1, 'BALANCE', $2)
            ON CONFLICT (order_id, kind) DO NOTHING
            """,
            order_id, amount,
        )

    async def get_for_order(self, order_id: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            "SELECT * FROM buyer_payments WHERE order_id = $1 ORDER BY created_at",
            order_id,
        )

    async def mark_paid(self, order_id: int, buyer_payment_id: int) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            UPDATE buyer_payments
               SET status = 'PAID', paid_at = now()
             WHERE buyer_payment_id = $1 AND order_id = $2 AND status = 'PENDING'
            RETURNING *
            """,
            buyer_payment_id, order_id,
        )
