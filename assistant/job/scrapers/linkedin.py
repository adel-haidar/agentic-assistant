import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# LinkedIn's guest pagination API — accessible without authentication
_GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_GUEST_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
_ENTITY_URN_RE = re.compile(r":(\d+)$")
_MIN_DESC_LEN = 100


class LinkedInScraper(BaseScraper):
    """LinkedIn jobs via the unauthenticated guest API (no Playwright, no API key)."""

    name = "LinkedIn"

    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]:
        location = city or country
        params = {"keywords": query, "location": location, "start": 0}

        listings: list[JobListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, follow_redirects=True, timeout=30.0
            ) as client:
                r = await client.get(_GUEST_SEARCH, params=params)
                content = r.text
                logger.debug("LinkedIn guest API first 500: %.500s", content)

                if r.status_code != 200:
                    logger.warning(
                        "LinkedIn guest API returned HTTP %d for %r", r.status_code, query
                    )
                    return []

                soup = BeautifulSoup(content, "lxml")
                cards = soup.find_all("div", attrs={"data-entity-urn": True})
                if not cards:
                    logger.warning(
                        "LinkedIn: 0 job cards for %r. HTTP %d. First 200: %.200s",
                        query, r.status_code, content,
                    )
                    return []

                for card in cards[: self._max]:
                    m = _ENTITY_URN_RE.search(card.get("data-entity-urn", ""))
                    if not m:
                        continue
                    job_id = m.group(1)

                    title_el = card.find(class_=re.compile(r"base-search-card__title"))
                    company_el = card.find(class_=re.compile(r"base-search-card__subtitle"))
                    location_el = card.find(class_=re.compile(r"job-search-card__location"))
                    link_el = card.find("a", class_=re.compile(r"base-card__full-link"))

                    title = title_el.get_text(strip=True) if title_el else ""
                    company = company_el.get_text(strip=True) if company_el else "Unknown"
                    loc = (
                        location_el.get_text(strip=True)
                        if location_el
                        else (city or country)
                    )
                    job_url = (
                        link_el.get("href", "").split("?")[0]
                        if link_el
                        else f"https://www.linkedin.com/jobs/view/{job_id}/"
                    )

                    if not title:
                        continue

                    await asyncio.sleep(self._delay)
                    description = await _fetch_detail(client, job_id)
                    if not description or len(description) < _MIN_DESC_LEN:
                        continue

                    listings.append(
                        JobListing(
                            platform="LinkedIn",
                            title=title,
                            company=company,
                            location=loc,
                            country=country,
                            job_url=job_url,
                            description=description,
                        )
                    )
        except Exception:
            logger.exception("LinkedIn scrape failed for query=%r", query)

        logger.info("LinkedIn: %d results for %r in %s", len(listings), query, country)
        return listings


async def _fetch_detail(client: httpx.AsyncClient, job_id: str) -> str:
    url = _GUEST_DETAIL.format(job_id)
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        for sel in [
            ".show-more-less-html__markup",
            "div.description__text",
            "section.description",
            "div.core-section-container__content",
        ]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator="\n", strip=True)
        return ""
    except Exception:
        logger.warning("LinkedIn detail fetch failed: job_id=%s", job_id)
        return ""
