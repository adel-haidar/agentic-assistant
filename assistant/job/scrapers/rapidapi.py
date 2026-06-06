import logging
from datetime import datetime
from typing import Optional

import httpx

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_COUNTRY_CODES = {
    "Switzerland": "CH",
    "Canada": "CA",
    "Norway": "NO",
    "Singapore": "SG",
}


class RapidApiScraper(BaseScraper):
    """Searches LinkedIn jobs via the JSearch API on RapidAPI.

    Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
    Host header: jsearch.p.rapidapi.com (default) or configure via RAPIDAPI_HOST.
    """

    name = "LinkedIn"

    def __init__(
        self,
        api_key: str,
        api_host: str = "jsearch.p.rapidapi.com",
        delay_seconds: float = 2.0,
        max_per_query: int = 20,
    ):
        super().__init__(delay_seconds, max_per_query)
        self._headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": api_host}
        self._host = api_host

    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]:
        country_code = _COUNTRY_CODES.get(country, country[:2].upper())
        search_query = f"{query} {city}" if city else query

        params = {
            "query": search_query,
            "num_pages": "1",
            "country": country_code,
            "employment_types": "FULLTIME",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(
                    f"https://{self._host}/search",
                    headers=self._headers,
                    params=params,
                )
                r.raise_for_status()
            data = r.json()
        except Exception:
            logger.exception(
                "RapidAPI request failed for query=%r country=%s", query, country
            )
            return []

        listings: list[JobListing] = []
        for item in data.get("data") or []:
            job_url = item.get("job_apply_link") or item.get("job_google_link") or ""
            if not job_url:
                continue

            salary_raw = _extract_salary(item)
            posted_date = _parse_date(item.get("job_posted_at_datetime_utc"))
            remote_type = "remote" if item.get("job_is_remote") else "unknown"
            city_part = item.get("job_city") or ""
            state_part = item.get("job_state") or ""
            location = ", ".join(p for p in [city_part, state_part] if p) or country

            listings.append(
                JobListing(
                    platform="LinkedIn",
                    title=item.get("job_title") or "",
                    company=item.get("employer_name") or "",
                    location=location,
                    country=country,
                    job_url=job_url,
                    posted_date=posted_date,
                    salary_raw=salary_raw,
                    description=item.get("job_description") or "",
                    remote_type=remote_type,
                )
            )
            if len(listings) >= self._max:
                break

        await self._sleep()
        logger.info(
            "RapidAPI: %d results for %r in %s", len(listings), query, country
        )
        return listings


def _extract_salary(item: dict) -> Optional[str]:
    lo = item.get("job_min_salary")
    hi = item.get("job_max_salary")
    currency = item.get("job_salary_currency") or ""
    period = item.get("job_salary_period") or ""
    if not lo and not hi:
        return None
    parts = []
    if lo:
        parts.append(str(int(lo)))
    if hi:
        parts.append(str(int(hi)))
    salary = " – ".join(parts)
    if currency:
        salary = f"{currency} {salary}"
    if period:
        salary = f"{salary} / {period}"
    return salary


def _parse_date(raw: Optional[str]):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        return None
