from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class JobListing(BaseModel):
    platform: str
    title: str
    company: str
    location: str
    country: str
    job_url: str
    posted_date: Optional[date] = None
    salary_raw: Optional[str] = None
    description: str = ""
    remote_type: str = "unknown"


class MatchResult(BaseModel):
    disqualified: bool
    disqualifier_code: Optional[str] = None
    rejection_reason: Optional[str] = None
    score: Optional[int] = None
    match_tier: Optional[str] = None
    tech_flags: list[str] = []
    domain_flags: list[str] = []
    positive_flags: list[str] = []
    soft_flags: list[str] = []
    ai_summary: Optional[str] = None
    salary_min_local: Optional[float] = None
    salary_max_local: Optional[float] = None
    currency: Optional[str] = None
    remote_type: str = "unknown"


class ScoredListing(BaseModel):
    listing: JobListing
    result: MatchResult
    db_id: Optional[int] = None


class RunReport(BaseModel):
    timestamp: datetime
    platforms: list[str]
    countries: list[str]
    raw_count: int
    dedup_count: int
    hard_rejected_count: int
    hard_rejected_by_reason: dict[str, int]
    scored_count: int
    strong_matches: list[ScoredListing]
    good_matches: list[ScoredListing]
    rejection_log: dict[str, list[str]]
    db_saved_this_run: int
    db_cumulative: int
