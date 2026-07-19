from __future__ import annotations

from decimal import Decimal

import asyncpg

from core.domain import WorkshopBid


class WorkshopRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_bids(self, product_type: str) -> list[WorkshopBid]:
        rows = await self._pool.fetch(
            """
            SELECT
                w.workshop_id,
                wc.available_qty,
                wc.reserved_qty,
                wc.cost_per_unit,
                w.quality_tier,
                wc.lead_time_days,
                COALESCE(ts.score, 0.500) AS trust_score,
                w.spec_disputes
            FROM workshop_capacity wc
            JOIN workshops w USING (workshop_id)
            LEFT JOIN trust_score_cache ts USING (workshop_id)
            WHERE wc.product_type = $1
              AND w.is_factory = FALSE
            """,
            product_type,
        )
        return [
            WorkshopBid(
                workshop_id=r["workshop_id"],
                available_qty=r["available_qty"],
                reserved_qty=r["reserved_qty"],
                cost_per_unit=Decimal(str(r["cost_per_unit"])),
                quality_tier=r["quality_tier"],
                lead_time_days=r["lead_time_days"],
                trust_score=float(r["trust_score"]),
                spec_disputes=r["spec_disputes"],
            )
            for r in rows
        ]

    async def list_capacity(self, workshop_id: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT product_type, available_qty, reserved_qty, cost_per_unit,
                   lead_time_days, updated_at
              FROM workshop_capacity
             WHERE workshop_id = $1
             ORDER BY product_type
            """,
            workshop_id,
        )

    async def get_factory(self, product_type: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            SELECT w.workshop_id, wc.cost_per_unit
              FROM workshops w
              JOIN workshop_capacity wc ON wc.workshop_id = w.workshop_id
                                        AND wc.product_type = $1
             WHERE w.is_factory = TRUE
             LIMIT 1
            """,
            product_type,
        )

    async def reserve_capacity(
        self, workshop_id: int, product_type: str, qty: int
    ) -> None:
        await self._pool.execute(
            """
            UPDATE workshop_capacity
               SET reserved_qty = reserved_qty + $1, updated_at = now()
             WHERE workshop_id = $2 AND product_type = $3
            """,
            qty, workshop_id, product_type,
        )

    async def release_capacity(
        self, workshop_id: int, product_type: str, qty: int
    ) -> None:
        await self._pool.execute(
            """
            UPDATE workshop_capacity
               SET reserved_qty = GREATEST(reserved_qty - $1, 0), updated_at = now()
             WHERE workshop_id = $2 AND product_type = $3
            """,
            qty, workshop_id, product_type,
        )

    async def upsert_capacity(
        self,
        workshop_id: int,
        product_type: str,
        available_qty: int,
        cost_per_unit: Decimal,
        lead_time_days: int,
    ) -> asyncpg.Record:
        return await self._pool.fetchrow(
            """
            INSERT INTO workshop_capacity
                (workshop_id, product_type, available_qty, cost_per_unit, lead_time_days, reserved_qty, updated_at)
            VALUES ($1, $2, $3, $4, $5, 0, now())
            ON CONFLICT (workshop_id, product_type)
            DO UPDATE SET available_qty = EXCLUDED.available_qty,
                          cost_per_unit = EXCLUDED.cost_per_unit,
                          lead_time_days = EXCLUDED.lead_time_days,
                          updated_at = now()
            RETURNING workshop_id, product_type, available_qty, cost_per_unit, lead_time_days, updated_at
            """,
            workshop_id, product_type, available_qty, cost_per_unit, lead_time_days,
        )

    async def get_by_phone(self, phone_number: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "SELECT workshop_id, name FROM workshops WHERE phone_number = $1",
            phone_number,
        )

    async def get_phone(self, workshop_id: int) -> str | None:
        return await self._pool.fetchval(
            "SELECT phone_number FROM workshops WHERE workshop_id = $1",
            workshop_id,
        )

    async def increment_spec_disputes(self, workshop_id: int) -> None:
        await self._pool.execute(
            "UPDATE workshops SET spec_disputes = spec_disputes + 1 WHERE workshop_id = $1",
            workshop_id,
        )
