from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg

from core.exceptions import InvalidStateTransitionError

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "PENDING":         {"ALLOCATING", "CANCELLED"},
    "ALLOCATING":      {"ALLOCATED", "FAILED"},
    "ALLOCATED":       {"IN_PRODUCTION", "FAILED", "CANCELLED"},
    "IN_PRODUCTION":   {"VERIFYING", "FACTORY_FALLBACK", "FAILED"},
    "VERIFYING":       {"SETTLING", "IN_PRODUCTION"},
    "FACTORY_FALLBACK":{"SETTLING"},
    "SETTLING":        {"CLOSED"},
    "CLOSED":          set(),
    "FAILED":          set(),
    "CANCELLED":       set(),
}


class OrderRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        buyer_ref: str,
        product_type: str,
        total_qty: int,
        quality_min: int,
        deadline: date,
        factory_fallback_cost: Decimal,
        factory_workshop_id: int,
        payment_terms: str = "PAY_ON_DELIVERY",
    ) -> int:
        row = await self._pool.fetchrow(
            """
            INSERT INTO orders
                (buyer_ref, product_type, total_qty, quality_min, deadline,
                 factory_fallback_cost, factory_workshop_id, payment_terms)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING order_id
            """,
            buyer_ref, product_type, total_qty, quality_min, deadline,
            factory_fallback_cost, factory_workshop_id, payment_terms,
        )
        return row["order_id"]

    async def get(self, order_id: int) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "SELECT * FROM orders WHERE order_id = $1", order_id
        )

    async def get_by_correlation_id(self, correlation_id: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "SELECT * FROM orders WHERE correlation_id = $1", correlation_id
        )

    async def list_paginated(
        self, status_filter: str | None, page: int, page_size: int
    ) -> tuple[list[asyncpg.Record], int]:
        rows = await self._pool.fetch(
            """
            SELECT order_id, status, product_type, total_qty, deadline, created_at,
                   COUNT(*) OVER() AS total_count
              FROM orders
             WHERE $1::order_status IS NULL OR status = $1::order_status
             ORDER BY created_at DESC
             LIMIT $2 OFFSET $3
            """,
            status_filter, page_size, (page - 1) * page_size,
        )
        total = rows[0]["total_count"] if rows else 0
        return rows, total

    async def list_stuck_pending(self, threshold_seconds: int) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT order_id, correlation_id
              FROM orders
             WHERE status = 'PENDING'
               AND created_at < now() - ($1 * INTERVAL '1 second')
            """,
            threshold_seconds,
        )

    async def transition_status(
        self, order_id: int, from_status: str, to_status: str
    ) -> None:
        if to_status not in _VALID_TRANSITIONS.get(from_status, set()):
            raise InvalidStateTransitionError(
                f"Order {order_id}: {from_status} → {to_status} is not a valid transition"
            )
        result = await self._pool.execute(
            """
            UPDATE orders
               SET status = $1, updated_at = now()
             WHERE order_id = $2 AND status = $3
            """,
            to_status, order_id, from_status,
        )
        if result == "UPDATE 0":
            raise InvalidStateTransitionError(
                f"Order {order_id} was not in state {from_status}"
            )

    async def cancel(self, order_id: int) -> None:
        row = await self.get(order_id)
        if row is None:
            raise InvalidStateTransitionError(f"Order {order_id} not found")

        current = row["status"]
        cancellable = {"PENDING", "ALLOCATED"}
        if current not in cancellable:
            raise InvalidStateTransitionError(
                f"Order {order_id} cannot be cancelled in state {current}"
            )
        await self.transition_status(order_id, current, "CANCELLED")
