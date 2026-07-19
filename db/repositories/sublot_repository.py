from __future__ import annotations

import logging
from decimal import Decimal

import asyncpg

from core.domain import SubLotDraft, SubLotRecord

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = frozenset({"VERIFIED", "FAILED", "NEEDS_HUMAN_REVIEW"})


class SublotRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_batch(
        self, drafts: list[SubLotDraft], conn: asyncpg.Connection | None = None
    ) -> list[int]:
        executor = conn or self._pool
        rows = await executor.fetch(
            """
            INSERT INTO sublots (order_id, workshop_id, qty_assigned, cost_per_unit)
            SELECT
                d.order_id, d.workshop_id, d.qty_assigned, d.cost_per_unit
            FROM UNNEST($1::int[], $2::int[], $3::int[], $4::numeric[])
                AS d(order_id, workshop_id, qty_assigned, cost_per_unit)
            RETURNING sublot_id
            """,
            [d.order_id for d in drafts],
            [d.workshop_id for d in drafts],
            [d.qty_assigned for d in drafts],
            [d.cost_per_unit for d in drafts],
        )
        return [r["sublot_id"] for r in rows]

    async def list_for_order(self, order_id: int) -> list[SubLotRecord]:
        rows = await self._pool.fetch(
            "SELECT * FROM sublots WHERE order_id = $1 ORDER BY sublot_id",
            order_id,
        )
        return [self._to_record(r) for r in rows]

    async def get(self, sublot_id: int) -> SubLotRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM sublots WHERE sublot_id = $1", sublot_id
        )
        return self._to_record(row) if row else None

    async def list_for_workshop(self, workshop_id: int, limit: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT s.sublot_id, s.order_id, s.qty_assigned, s.delivered_qty, s.status,
                   o.product_type, o.deadline, vr.explanation, vr.explanations
              FROM sublots s
              JOIN orders o USING (order_id)
              LEFT JOIN verification_results vr USING (sublot_id)
             WHERE s.workshop_id = $1
             ORDER BY s.sublot_id DESC
             LIMIT $2
            """,
            workshop_id, limit,
        )

    async def mark_delivered(self, sublot_id: int, delivered_qty: int) -> None:
        result = await self._pool.execute(
            """
            UPDATE sublots
               SET delivered_qty = $1, status = 'DELIVERED', updated_at = now(),
                   delivered_at = now()
             WHERE sublot_id = $2
               AND status IN ('ASSIGNED', 'IN_PRODUCTION')
            """,
            delivered_qty, sublot_id,
        )
        if result == "UPDATE 0":
            logger.debug(
                "mark_delivered sublot_id=%d: already past delivery state — skipped",
                sublot_id,
            )

    async def mark_capacity_released(self, sublot_id: int) -> bool:
        result = await self._pool.execute(
            """
            UPDATE sublots
               SET capacity_released_at = now()
             WHERE sublot_id = $1
               AND capacity_released_at IS NULL
            """,
            sublot_id,
        )
        return result == "UPDATE 1"

    async def start_production(self, sublot_id: int) -> bool:
        result = await self._pool.execute(
            """
            UPDATE sublots SET status = 'IN_PRODUCTION', updated_at = now()
             WHERE sublot_id = $1 AND status = 'ASSIGNED'
            """,
            sublot_id,
        )
        return result == "UPDATE 1"

    async def list_delivered_past_grace(self, grace_seconds: int) -> list[int]:
        rows = await self._pool.fetch(
            """
            SELECT sublot_id FROM sublots
             WHERE status = 'DELIVERED'
               AND updated_at <= now() - make_interval(secs => $1::int)
            """,
            grace_seconds,
        )
        return [r["sublot_id"] for r in rows]

    async def list_for_order_admin(self, order_id: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT s.sublot_id, s.workshop_id, w.name AS workshop_name, w.is_factory,
                   s.qty_assigned, s.delivered_qty, s.cost_per_unit, s.status
              FROM sublots s
              JOIN workshops w USING (workshop_id)
             WHERE s.order_id = $1
             ORDER BY s.sublot_id
            """,
            order_id,
        )

    async def list_needing_review(self) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT s.sublot_id, s.order_id, s.workshop_id, s.qty_assigned, s.status, s.updated_at,
                   o.product_type,
                   vr.verdict, vr.fault_party, vr.confidence, vr.explanation, vr.explanations
              FROM sublots s
              JOIN orders o USING (order_id)
              LEFT JOIN verification_results vr USING (sublot_id)
             WHERE s.status IN ('VERIFYING', 'NEEDS_HUMAN_REVIEW')
             ORDER BY s.updated_at ASC
            """
        )

    async def cancel_for_order(self, order_id: int) -> None:
        await self._pool.execute(
            """
            UPDATE sublots SET status = 'CANCELLED', updated_at = now()
             WHERE order_id = $1 AND status = 'ASSIGNED'
            """,
            order_id,
        )

    async def transition_status(self, sublot_id: int, new_status: str) -> None:
        await self._pool.execute(
            "UPDATE sublots SET status = $1, updated_at = now() WHERE sublot_id = $2",
            new_status, sublot_id,
        )

    async def all_terminal(self, order_id: int) -> bool:
        row = await self._pool.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status = ANY($1::sublot_status[])) AS terminal
              FROM sublots
             WHERE order_id = $2
            """,
            list(_TERMINAL_STATUSES), order_id,
        )
        return row["total"] > 0 and row["total"] == row["terminal"]

    async def all_delivered_or_further(self, order_id: int) -> bool:
        row = await self._pool.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status NOT IN ('ASSIGNED', 'IN_PRODUCTION')) AS reached
              FROM sublots
             WHERE order_id = $1
            """,
            order_id,
        )
        return row["total"] > 0 and row["total"] == row["reached"]

    @staticmethod
    def _to_record(row: asyncpg.Record) -> SubLotRecord:
        return SubLotRecord(
            sublot_id=row["sublot_id"],
            order_id=row["order_id"],
            workshop_id=row["workshop_id"],
            qty_assigned=row["qty_assigned"],
            delivered_qty=row["delivered_qty"],
            cost_per_unit=Decimal(str(row["cost_per_unit"])),
            status=row["status"],
            delivered_at=row["delivered_at"],
        )
