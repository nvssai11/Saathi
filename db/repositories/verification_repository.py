from __future__ import annotations

import asyncpg

from core.domain import VerificationOutput, VerificationRecord


class VerificationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(
        self,
        sublot_id: int,
        output: VerificationOutput,
        photo_path: str | None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO verification_results
                (sublot_id, verdict, fault_party, confidence, explanation, photo_path)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (sublot_id) DO NOTHING
            """,
            sublot_id,
            output.verdict,
            output.fault_party,
            output.confidence,
            output.explanation,
            photo_path,
        )

    async def get(self, sublot_id: int) -> VerificationRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM verification_results WHERE sublot_id = $1", sublot_id
        )
        if row is None:
            return None
        return VerificationRecord(
            sublot_id=row["sublot_id"],
            verdict=row["verdict"],
            fault_party=row["fault_party"],
            confidence=float(row["confidence"]),
        )

    async def get_recent_explanations(self, workshop_id: int, limit: int = 5) -> list[str]:
        rows = await self._pool.fetch(
            """
            SELECT vr.explanation
              FROM verification_results vr
              JOIN sublots s USING (sublot_id)
             WHERE s.workshop_id = $1 AND vr.verdict = 'DEFECT'
             ORDER BY vr.created_at DESC
             LIMIT $2
            """,
            workshop_id, limit,
        )
        return [r["explanation"] for r in rows]

    async def get_for_order(self, order_id: int) -> dict[int, VerificationRecord]:
        rows = await self._pool.fetch(
            """
            SELECT vr.*
              FROM verification_results vr
              JOIN sublots s USING (sublot_id)
             WHERE s.order_id = $1
            """,
            order_id,
        )
        return {
            r["sublot_id"]: VerificationRecord(
                sublot_id=r["sublot_id"],
                verdict=r["verdict"],
                fault_party=r["fault_party"],
                confidence=float(r["confidence"]),
            )
            for r in rows
        }
