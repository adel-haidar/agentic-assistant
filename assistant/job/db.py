import logging
from typing import Optional

import asyncpg

from assistant.job.models import JobListing, MatchResult

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS job_matches (
    id                SERIAL PRIMARY KEY,
    run_timestamp     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    platform          VARCHAR(50)     NOT NULL,
    title             TEXT            NOT NULL,
    company           TEXT            NOT NULL,
    location          TEXT            NOT NULL,
    country           VARCHAR(50)     NOT NULL,
    job_url           TEXT            NOT NULL UNIQUE,
    posted_date       DATE,
    salary_raw        TEXT,
    salary_min_local  NUMERIC(12,2),
    salary_max_local  NUMERIC(12,2),
    currency          VARCHAR(10),
    remote_type       VARCHAR(20),
    match_score       SMALLINT        NOT NULL CHECK (match_score BETWEEN 0 AND 100),
    match_tier        VARCHAR(20)     NOT NULL,
    tech_flags        TEXT[],
    domain_flags      TEXT[],
    positive_flags    TEXT[],
    disqualifier_flag TEXT,
    rejection_reason  TEXT,
    ai_summary        TEXT,
    status            VARCHAR(30)     NOT NULL DEFAULT 'new',
    applied_at        TIMESTAMPTZ,
    notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_jm_score   ON job_matches (match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jm_country ON job_matches (country);
CREATE INDEX IF NOT EXISTS idx_jm_tier    ON job_matches (match_tier);
CREATE INDEX IF NOT EXISTS idx_jm_run     ON job_matches (run_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_jm_status  ON job_matches (status);
"""

_pool: Optional[asyncpg.Pool] = None


async def init_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(database_url)
        async with _pool.acquire() as conn:
            await conn.execute(_DDL)
        logger.info("PostgreSQL pool initialized and schema verified")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


async def upsert_match(
    pool: asyncpg.Pool, listing: JobListing, result: MatchResult
) -> tuple[Optional[int], bool]:
    """Insert or conditionally update a job match.

    Returns (row_id, was_saved) where was_saved is True only for a fresh insert
    or a score-bump update.  False means the row already existed and was not
    changed (conflict guard fired or status was protected).
    """
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO job_matches (
                    platform, title, company, location, country, job_url,
                    posted_date, salary_raw, salary_min_local, salary_max_local,
                    currency, remote_type, match_score, match_tier,
                    tech_flags, domain_flags, positive_flags,
                    disqualifier_flag, rejection_reason, ai_summary
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                ON CONFLICT (job_url) DO UPDATE SET
                    match_score   = EXCLUDED.match_score,
                    ai_summary    = EXCLUDED.ai_summary,
                    run_timestamp = NOW()
                WHERE
                    EXCLUDED.match_score > job_matches.match_score + 5
                    AND job_matches.status NOT IN ('applied','interviewing','withdrawn','rejected')
                RETURNING id
                """,
                listing.platform, listing.title, listing.company,
                listing.location, listing.country, listing.job_url,
                listing.posted_date, listing.salary_raw,
                result.salary_min_local, result.salary_max_local,
                result.currency, result.remote_type,
                result.score, result.match_tier,
                result.tech_flags or [], result.domain_flags or [],
                result.positive_flags or [],
                result.disqualifier_code, result.rejection_reason,
                result.ai_summary,
            )
            if row:
                return row["id"], True
            # Conflict resolved without update — already exists, not worth updating
            existing = await conn.fetchrow(
                "SELECT id FROM job_matches WHERE job_url = $1", listing.job_url
            )
            return (existing["id"] if existing else None), False
        except Exception:
            logger.exception("DB upsert failed for %s", listing.job_url)
            return None, False


async def list_unknown_companies(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, job_url, platform FROM job_matches "
            "WHERE company IN ('Explore companies', 'Unknown')"
        )
        return [dict(r) for r in rows]


async def update_company(pool: asyncpg.Pool, job_url: str, company: str) -> bool:
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE job_matches SET company = $1 WHERE job_url = $2",
            company, job_url,
        )
        return tag == "UPDATE 1"


async def count_all(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM job_matches") or 0


_VALID_STATUSES = frozenset(
    {"new", "reviewing", "applied", "interviewing", "rejected", "withdrawn", "expired"}
)


async def list_matches(
    pool: asyncpg.Pool,
    tier: Optional[str] = None,
    country: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []

    if tier:
        params.append(tier)
        conditions.append(f"match_tier = ${len(params)}")
    if country:
        params.append(country)
        conditions.append(f"country = ${len(params)}")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM job_matches {where} "
            f"ORDER BY match_score DESC, run_timestamp DESC LIMIT ${len(params)}",
            *params,
        )
        return [dict(r) for r in rows]


async def set_status(pool: asyncpg.Pool, match_id: int, status: str) -> bool:
    if status not in _VALID_STATUSES:
        return False
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE job_matches SET status = $1 WHERE id = $2", status, match_id
        )
        return tag == "UPDATE 1"
