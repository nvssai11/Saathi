from __future__ import annotations

from datetime import datetime

import asyncpg

from core.domain import TrustEvent
from core.trust.scorer import TrustScorer


class TrustRepository:
    def __init__(self, pool: asyncpg.Pool, scorer: TrustScorer) -> None:
        self._pool = pool
        self._scorer = scorer

    @property
    def scorer(self) -> TrustScorer:
        return self._scorer

    async def append_event(self, event: TrustEvent) -> None:
        await self._pool.execute(
            """
            INSERT INTO trust_events
                (workshop_id, sublot_id, on_time, defect_found, fault_party,
                 event_type, created_at)
            VALUES ($1, $2, $3, $4, $5::fault_party,
                    CASE
                        WHEN $3 AND NOT $4 THEN 'DELIVERY_ON_TIME'
                        WHEN NOT $3 AND NOT $4 THEN 'DELIVERY_LATE'
                        WHEN $4 AND $5::text = 'workshop' THEN 'DEFECT_WORKSHOP'
                        WHEN $4 AND $5::text = 'buyer'    THEN 'DEFECT_BUYER'
                        ELSE 'SPEC_AMBIGUITY'
                    END::trust_event_type,
                    $6)
            """,
            event.workshop_id, event.sublot_id,
            event.on_time, event.defect_found, event.fault_party,
            event.created_at,
        )
        await self._refresh_cache(event.workshop_id)

    async def get_recent_events(
        self, workshop_id: int, limit: int
    ) -> list[TrustEvent]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM trust_events
             WHERE workshop_id = $1
             ORDER BY created_at DESC
             LIMIT $2
            """,
            workshop_id, limit,
        )
        return [
            TrustEvent(
                workshop_id=r["workshop_id"],
                sublot_id=r["sublot_id"],
                on_time=r["on_time"],
                defect_found=r["defect_found"],
                fault_party=r["fault_party"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def get_recent_explanations(
        self, workshop_id: int, window_size: int
    ) -> list[str]:
        events = await self.get_recent_events(workshop_id, limit=window_size)
        return self._scorer.score_explanation(events)

    async def get_recent_events_with_explanations(
        self, workshop_id: int, limit: int
    ) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT te.sublot_id, te.on_time, te.defect_found, te.fault_party,
                   te.created_at, vr.explanation, vr.explanations
              FROM trust_events te
              LEFT JOIN verification_results vr USING (sublot_id)
             WHERE te.workshop_id = $1
             ORDER BY te.created_at DESC
             LIMIT $2
            """,
            workshop_id, limit,
        )

    async def _refresh_cache(self, workshop_id: int) -> None:
        window_size = self._scorer.window_size
        rows = await self._pool.fetch(
            """
            SELECT * FROM (
                SELECT * FROM trust_events
                 WHERE workshop_id = $1
                 ORDER BY created_at DESC
                 LIMIT $2
            ) recent
            ORDER BY created_at ASC
            """,
            workshop_id, window_size,
        )
        events = [
            TrustEvent(
                workshop_id=r["workshop_id"],
                sublot_id=r["sublot_id"],
                on_time=r["on_time"],
                defect_found=r["defect_found"],
                fault_party=r["fault_party"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
        score = self._scorer.compute_score(events)
        grade = self._scorer.grade(score)

        await self._pool.execute(
            """
            INSERT INTO trust_score_cache (workshop_id, score, grade, computed_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (workshop_id)
            DO UPDATE SET score = EXCLUDED.score,
                          grade = EXCLUDED.grade,
                          computed_at = EXCLUDED.computed_at
            """,
            workshop_id, score, grade,
        )
