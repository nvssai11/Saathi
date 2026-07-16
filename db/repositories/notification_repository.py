from __future__ import annotations

import asyncpg


class NotificationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        workshop_id: int,
        order_id: int,
        sublot_id: int,
        product_type: str,
        qty_assigned: int,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO notifications (workshop_id, order_id, sublot_id, product_type, qty_assigned)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (sublot_id) DO NOTHING
            """,
            workshop_id, order_id, sublot_id, product_type, qty_assigned,
        )

    async def list_for_workshop(self, workshop_id: int, limit: int = 20) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT notification_id, order_id, sublot_id, product_type, qty_assigned, created_at
              FROM notifications
             WHERE workshop_id = $1
             ORDER BY created_at DESC
             LIMIT $2
            """,
            workshop_id, limit,
        )
