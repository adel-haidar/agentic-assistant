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

_SEARCH_URL = "https://www.linkedin.com/jobs/search/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}
_MIN_DESC_LEN = 100


class LinkedInScraper(BaseScraper):
    """LinkedIn jobs via public HTML search (no API key required)."""

    name = "LinkedIn"

    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]:
        location = city or country
        url = (
            f"{_SEARCH_URL}"
            f"?keywords={quote_plus(query)}"
            f"&location={quote_plus(location)}"
            "&f_TPR=r604800"
        )

        listings: list[JobListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, follow_redirects=True, timeout=30.0
            ) as client:
                r = await client.get(url)
                content = r.text
                logger.debug("LinkedIn search first 500 chars: %.500s", content)

                if r.status_code != 200:
                    logger.warning(
                        "LinkedIn returned HTTP %d for %r", r.status_code, query
                    )
                    return []

                soup = BeautifulSoup(content, "lxml")
                cards = soup.find_all("div", class_=re.compile(r"base-search-card"))

                if not cards:
                    title_el = soup.find("title")
                    logger.warning(
                        "LinkedIn: 0 job cards found for %r. Page title: %r",
                        query,
                        title_el.text if title_el else "unknown",
                    )
                    return []

                for card in cards[: self._max]:
                    listing = await _parse_card(client, card, country, city, self._delay)
                    if listing:
                        listings.append(listing)
        except Exception:
            logger.exception("LinkedIn scrape failed for query=%r", query)

        logger.info("LinkedIn: %d results for %r in %s", len(listings), query, country)
        return listings


async def _parse_card(
    client: httpx.AsyncClient,
    card,
    country: str,
    city: Optional[str],
    delay: float,
) -> Optional[JobListing]:
    title_el = card.find(class_=re.compile(r"base-search-card__title"))
    company_el = card.find(class_=re.compile(r"base-search-card__subtitle"))
    location_el = card.find(class_=re.compile(r"job-search-card__location"))
    link_el = card.find("a", class_=re.compile(r"base-card__full-link"))

    title = title_el.get_text(strip=True) if title_el else ""
    company = company_el.get_text(strip=True) if company_el else ""
    location = location_el.get_text(strip=True) if location_el else (city or country)
    job_url = link_el.get("href", "").split("?")[0] if link_el else ""

    if not title or not job_url:
        return None

    await asyncio.sleep(delay)
    description = await _fetch_description(client, job_url)
    if not description or len(description) < _MIN_DESC_LEN:
        return None

    return JobListing(
        platform="LinkedIn",
        title=title,
        company=company or "Unknown",
        location=location,
        country=country,
        job_url=job_url,
        description=description,
    )


async def _fetch_description(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        desc_el = soup.find(class_=re.compile(r"description__text"))
        if desc_el:
            return desc_el.get_text(separator="\n", strip=True)
        main = soup.find("main") or soup.find("article")
        if main:
            return main.get_text(separator="\n", strip=True)
        return ""
    except Exception:
        logger.warning("LinkedIn detail fetch failed: %s", url)
        return ""
