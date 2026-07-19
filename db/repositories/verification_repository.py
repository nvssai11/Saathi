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
        explanations: dict[str, str] | None = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO verification_results
                (sublot_id, verdict, fault_party, confidence, explanation, explanations, photo_path)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            sublot_id,
            output.verdict,
            output.fault_party,
            output.confidence,
            output.explanation,
            explanations or {},
            photo_path,
        )

    async def get(self, sublot_id: int) -> VerificationRecord | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM verification_results
             WHERE sublot_id = $1
             ORDER BY created_at DESC
             LIMIT 1
            """,
            sublot_id,
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

    async def get_latest_photo_path_for_order(self, order_id: int) -> str | None:
        row = await self._pool.fetchrow(
            """
            SELECT vr.photo_path
              FROM verification_results vr
              JOIN sublots s USING (sublot_id)
             WHERE s.order_id = $1 AND vr.photo_path IS NOT NULL
             ORDER BY vr.created_at DESC
             LIMIT 1
            """,
            order_id,
        )
        return row["photo_path"] if row else None

    async def get_for_order(self, order_id: int) -> dict[int, VerificationRecord]:
        rows = await self._pool.fetch(
            """
            SELECT DISTINCT ON (vr.sublot_id) vr.*
              FROM verification_results vr
              JOIN sublots s USING (sublot_id)
             WHERE s.order_id = $1
             ORDER BY vr.sublot_id, vr.created_at DESC
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
