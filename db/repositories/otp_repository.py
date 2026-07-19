from __future__ import annotations

from datetime import datetime

import asyncpg


class OtpRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, phone_number: str, code_hash: str, expires_at: datetime) -> int:
        return await self._pool.fetchval(
            """
            INSERT INTO login_otps (phone_number, code_hash, expires_at)
            VALUES ($1, $2, $3)
            RETURNING otp_id
            """,
            phone_number, code_hash, expires_at,
        )

    async def get_latest_active(self, phone_number: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            """
            SELECT otp_id, phone_number, code_hash, expires_at, attempts
              FROM login_otps
             WHERE phone_number = $1 AND consumed_at IS NULL
             ORDER BY created_at DESC
             LIMIT 1
            """,
            phone_number,
        )

    async def increment_attempts(self, otp_id: int) -> int:
        return await self._pool.fetchval(
            "UPDATE login_otps SET attempts = attempts + 1 WHERE otp_id = $1 RETURNING attempts",
            otp_id,
        )

    async def consume(self, otp_id: int) -> None:
        await self._pool.execute(
            "UPDATE login_otps SET consumed_at = now() WHERE otp_id = $1",
            otp_id,
        )
