import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from assistant.job.db import count_all, init_pool, upsert_match
from assistant.job.models import JobListing, RunReport, ScoredListing
from assistant.job.report import format_report
from assistant.job.scorer import JobScorer
from assistant.job.scrapers.jobs_ch import JobsChScraper
from assistant.job.scrapers.rapidapi import RapidApiScraper
from assistant.job.scrapers.stepstone import StepStoneScraper

logger = logging.getLogger(__name__)

# (query, optional city) per target country
_QUERIES: dict[str, list[tuple[str, Optional[str]]]] = {
    "Switzerland": [
        ("Senior Java Engineer", "Zurich"),
        ("Spring Boot Entwickler", None),
        ("AI Engineer Banking", "Zurich"),
        ("Senior Backend Developer", "Geneva"),
        ("Software Engineer fintech", None),
    ],
    "Canada": [
        ("Senior Java Developer", "Toronto"),
        ("Backend Engineer remote", None),
        ("AI Engineer fintech", None),
        ("Java Spring Boot remote", None),
    ],
    "Norway": [
        ("Senior Java Developer", "Oslo"),
        ("Backend Engineer Norway", None),
        ("AI Engineer fintech", "Oslo"),
    ],
    "Singapore": [
        ("Java Backend Engineer", "Singapore"),
        ("Senior Software Engineer banking", "Singapore"),
        ("AI Engineer fintech", "Singapore"),
    ],
}


def _dedup_key(listing: JobListing) -> str:
    return (
        f"{listing.company.lower().strip()}"
        f"|{listing.title.lower().strip()}"
        f"|{listing.location.lower().strip()}"
    )


_SEARCH_PAGE_TITLE_PATTERNS = [
    re.compile(r"^\d+\s+job", re.I),
    re.compile(r"^\d[\d\s,]+job", re.I),
    re.compile(r"job offers?\s*-\s*", re.I),
    re.compile(r"job offer[s]? in\s+", re.I),
]

_HARD_REJECT_DOMAINS = [
    "quantitative finance", "portfolio construction", "factor models",
    "backtesting", "covariance estimation", "cvxpy", "mosek",
]


def is_search_results_page(listing: JobListing) -> bool:
    if "jobs.ch" in listing.job_url and "/detail/" not in listing.job_url:
        return True
    title_lower = listing.title.lower().strip()
    return any(p.search(title_lower) for p in _SEARCH_PAGE_TITLE_PATTERNS)


def pre_filter_technology_mismatch(listing: JobListing) -> bool:
    text = (listing.description + " " + listing.title).lower()
    if "java" not in text:
        return True
    java_idx = text.index("java")
    python_primary = sum([
        "python" in text and text.index("python") < java_idx,
        "python developer" in text,
        "python engineer" in text,
        "quantitative developer" in text,
        "quant developer" in text,
    ])
    if python_primary >= 2:
        return True
    return any(domain in text for domain in _HARD_REJECT_DOMAINS)


async def run_agent(
    database_url: str,
    bedrock_client,
    model_id: str,
    rapidapi_key: Optional[str],
    rapidapi_host: str,
    memory_client,
    graph_client,
    notification_email: str,
    delay_seconds: float,
    max_per_query: int,
) -> RunReport:
    timestamp = datetime.utcnow()
    pool = await init_pool(database_url)
    scorer = JobScorer(bedrock_client=bedrock_client, model_id=model_id)

    # LinkedIn and Indeed block AWS EC2 IPs at the network level — standalone scrapers
    # for those platforms will never succeed from this host. JSearch (RAPIDAPI_KEY)
    # routes through its own proxy infrastructure and is the only reliable source.
    scrapers = []
    if rapidapi_key:
        scrapers.append(
            RapidApiScraper(rapidapi_key, rapidapi_host, delay_seconds, max_per_query)
        )
    else:
        logger.warning(
            "RAPIDAPI_KEY not set — LinkedIn and Indeed results unavailable. "
            "Set RAPIDAPI_KEY to enable JSearch coverage for both platforms."
        )
    scrapers += [
        JobsChScraper(delay_seconds, max_per_query),
        StepStoneScraper(delay_seconds, max_per_query),
    ]

    all_raw: list[JobListing] = []
    platforms_used: set[str] = set()

    for country, queries in _QUERIES.items():
        for query, city in queries:
            for scraper in scrapers:
                if scraper.name == "jobs.ch" and country != "Switzerland":
                    continue
                if scraper.name == "StepStone" and country != "Switzerland":
                    continue
                try:
                    results = await scraper.search(query, country, city)
                    all_raw.extend(results)
                    if results:
                        platforms_used.add(scraper.name)
                except Exception:
                    logger.exception(
                        "Scraper %s failed for %r in %s", scraper.name, query, country
                    )

    raw_count = len(all_raw)

    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    deduped: list[JobListing] = []
    for listing in all_raw:
        if listing.job_url in seen_urls:
            continue
        key = _dedup_key(listing)
        if key in seen_keys:
            continue
        seen_urls.add(listing.job_url)
        seen_keys.add(key)
        deduped.append(listing)

    dedup_count = len(deduped)
    logger.info("Collected %d raw, %d after dedup", raw_count, dedup_count)

    strong_matches: list[ScoredListing] = []
    good_matches: list[ScoredListing] = []
    hard_rejected_count = 0
    hard_rejected_by_reason: dict[str, int] = defaultdict(int)
    rejection_log: dict[str, list[str]] = defaultdict(list)
    db_saved = 0

    loop = asyncio.get_event_loop()
    for listing in deduped:
        if is_search_results_page(listing):
            logger.info(
                "SEARCH_RESULTS_PAGE rejected: %r | %s", listing.title, listing.job_url
            )
            hard_rejected_count += 1
            hard_rejected_by_reason["SEARCH_RESULTS_PAGE"] += 1
            rejection_log["SEARCH_RESULTS_PAGE"].append(
                f"{listing.company} — {listing.title}"
            )
            continue

        if pre_filter_technology_mismatch(listing):
            hard_rejected_count += 1
            hard_rejected_by_reason["TECHNOLOGY_MISMATCH"] += 1
            rejection_log["TECHNOLOGY_MISMATCH"].append(
                f"{listing.company} — {listing.title}"
            )
            continue

        result = await loop.run_in_executor(None, scorer.score, listing)
        if result is None:
            continue

        if result.disqualified:
            hard_rejected_count += 1
            code = result.disqualifier_code or "UNKNOWN"
            hard_rejected_by_reason[code] += 1
            rejection_log[code].append(f"{listing.company} — {listing.title}")
            continue

        tier = result.match_tier
        if tier in ("STRONG_MATCH", "GOOD_MATCH"):
            row_id, is_new = await upsert_match(pool, listing, result)
            scored = ScoredListing(listing=listing, result=result, db_id=row_id)
            if is_new:
                db_saved += 1
            if tier == "STRONG_MATCH":
                strong_matches.append(scored)
            else:
                good_matches.append(scored)
        elif tier == "WEAK_MATCH":
            logger.debug(
                "WEAK_MATCH score=%s: %s @ %s",
                result.score,
                listing.title,
                listing.company,
            )

    if strong_matches and graph_client:
        try:
            _send_notification_email(graph_client, strong_matches, notification_email)
        except Exception:
            logger.exception("Failed to send job match notification email")

    db_total = await count_all(pool)

    if memory_client:
        try:
            top = strong_matches[0] if strong_matches else (good_matches[0] if good_matches else None)
            top_summary = ""
            if top:
                top_summary = (
                    f" Top match: {top.listing.title} at {top.listing.company},"
                    f" {top.listing.country} — score {top.result.score}/100."
                    f" {top.result.ai_summary or ''}"
                )
            await memory_client.save_job_run(
                date=timestamp.strftime("%Y-%m-%d"),
                strong_count=len(strong_matches),
                good_count=len(good_matches),
                top_summary=top_summary,
            )
        except Exception:
            logger.exception("Failed to save run summary to MCP memory")

    report = RunReport(
        timestamp=timestamp,
        platforms=sorted(platforms_used),
        countries=list(_QUERIES.keys()),
        raw_count=raw_count,
        dedup_count=dedup_count,
        hard_rejected_count=hard_rejected_count,
        hard_rejected_by_reason=dict(hard_rejected_by_reason),
        scored_count=dedup_count - hard_rejected_count,
        strong_matches=strong_matches,
        good_matches=good_matches,
        rejection_log=dict(rejection_log),
        db_saved_this_run=db_saved,
        db_cumulative=db_total,
    )

    logger.info(
        "Run complete — strong: %d | good: %d | saved: %d",
        len(strong_matches),
        len(good_matches),
        db_saved,
    )
    print(format_report(report))
    return report


def _send_notification_email(
    graph_client, strong_matches: list[ScoredListing], recipient: str
) -> None:
    lines = [f"Job Hunt Agent — {len(strong_matches)} Strong Match(es) Found\n"]
    for i, sm in enumerate(strong_matches, 1):
        flags = ", ".join(sm.result.positive_flags) if sm.result.positive_flags else "—"
        lines.append(
            f"[{i}] {sm.listing.title} — {sm.listing.company}\n"
            f"    Location: {sm.listing.location}, {sm.listing.country} | {sm.result.remote_type}\n"
            f"    Salary:   {sm.listing.salary_raw or 'not disclosed'}\n"
            f"    Score:    {sm.result.score}/100 | Flags: {flags}\n"
            f"    URL:      {sm.listing.job_url}\n"
            f"    Summary:  {sm.result.ai_summary or 'N/A'}\n"
        )
    graph_client.send_mail(
        subject=f"[Job Hunt] {len(strong_matches)} Strong Match(es) — {strong_matches[0].listing.country}",
        body="\n".join(lines),
        recipient=recipient,
    )
