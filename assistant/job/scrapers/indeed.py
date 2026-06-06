import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_DOMAINS = {
    "Switzerland": "ch.indeed.com",
    "Canada": "ca.indeed.com",
    "Norway": "no.indeed.com",
    "Singapore": "sg.indeed.com",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MIN_DESC_LEN = 100


class IndeedScraper(BaseScraper):
    """Indeed jobs via the public RSS feed (avoids Cloudflare bot detection)."""

    name = "Indeed"

    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]:
        domain = _DOMAINS.get(country, "www.indeed.com")
        location = city or country
        url = (
            f"https://{domain}/rss"
            f"?q={quote_plus(query)}"
            f"&l={quote_plus(location)}"
            "&sort=date&limit=25"
        )

        listings: list[JobListing] = []
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, follow_redirects=True, timeout=30.0
            ) as client:
                r = await client.get(url)
                content = r.text
                logger.debug("Indeed RSS first 500: %.500s", content)

                if r.status_code != 200:
                    logger.warning(
                        "Indeed RSS returned HTTP %d for %r in %s", r.status_code, query, country
                    )
                    return []

                items = _parse_rss(content)
                if not items:
                    logger.warning("Indeed: 0 RSS items for %r in %s", query, country)
                    return []

                for item in items[: self._max]:
                    listing = await _item_to_listing(
                        client, item, country, city, self._delay
                    )
                    if listing:
                        listings.append(listing)
        except Exception:
            logger.exception(
                "Indeed scrape failed for query=%r country=%s", query, country
            )

        logger.info("Indeed: %d results for %r in %s", len(listings), query, country)
        return listings


def _elem_text(el, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _get_link(item) -> str:
    el = item.find("link")
    if el is not None and el.text and el.text.strip().startswith("http"):
        return el.text.strip()
    # RSS quirk: <link> text sometimes appears as the tail of a sibling
    for child in item:
        if child.tail and child.tail.strip().startswith("http"):
            return child.tail.strip()
    # Fallback: guid often holds the URL
    el = item.find("guid")
    if el is not None and el.text and el.text.strip().startswith("http"):
        return el.text.strip()
    return ""


def _parse_rss(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Indeed RSS XML parse error: %s", exc)
        return []

    result = []
    for item in root.findall(".//item"):
        raw_title = _elem_text(item, "title")
        link = _get_link(item)
        desc_html = _elem_text(item, "description")
        company = _elem_text(item, "source")

        # RSS titles often arrive as "Job Title - Company Name"
        if not company and " - " in raw_title:
            parts = raw_title.rsplit(" - ", 1)
            clean_title, company = parts[0].strip(), parts[1].strip()
        else:
            clean_title = raw_title

        desc = unescape(_HTML_TAG_RE.sub(" ", desc_html)).strip()

        if link:
            result.append(
                {
                    "title": clean_title,
                    "company": company or "Unknown",
                    "link": link,
                    "description": desc,
                }
            )
    return result


async def _item_to_listing(
    client: httpx.AsyncClient,
    item: dict,
    country: str,
    city: Optional[str],
    delay: float,
) -> Optional[JobListing]:
    job_url = item["link"]
    description = item["description"]

    await asyncio.sleep(delay)
    full_desc = await _fetch_full_description(client, job_url)
    if full_desc and len(full_desc) >= _MIN_DESC_LEN:
        description = full_desc

    if not description or len(description) < _MIN_DESC_LEN:
        return None

    return JobListing(
        platform="Indeed",
        title=item["title"],
        company=item["company"],
        location=city or country,
        country=country,
        job_url=job_url,
        description=description,
    )


async def _fetch_full_description(client: httpx.AsyncClient, url: str) -> str:
    """Try to retrieve the full job description page. Returns empty string on failure."""
    try:
        r = await client.get(url, timeout=15.0)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        for sel in [
            "#jobDescriptionText",
            ".jobsearch-jobDescriptionText",
            "div.job_description",
        ]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator="\n", strip=True)
        return ""
    except Exception:
        return ""
