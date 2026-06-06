import asyncio
import logging
from typing import Optional
from urllib.parse import quote_plus

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.jobs.ch/en/vacancies/"
_MIN_DESC_LEN = 100


class JobsChScraper(BaseScraper):
    name = "jobs.ch"

    async def search(
        self, query: str, country: str = "Switzerland", city: Optional[str] = None
    ) -> list[JobListing]:
        from playwright.async_api import async_playwright

        url = f"{_SEARCH_URL}?term={quote_plus(query)}"
        if city:
            url += f"&location={quote_plus(city)}"

        listings: list[JobListing] = []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                await _dismiss_cookie_banner(page)

                detail_urls = await _collect_detail_urls(page)
                for detail_url in detail_urls[: self._max]:
                    listing = await _scrape_detail(page, detail_url, country, city)
                    if listing:
                        listings.append(listing)
                    await asyncio.sleep(self._delay)

                await browser.close()
        except Exception:
            logger.exception("jobs.ch scrape failed for query=%r", query)

        logger.info("jobs.ch: %d results for %r", len(listings), query)
        return listings


async def _dismiss_cookie_banner(page) -> None:
    for selector in [
        "button[id*='accept']",
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
    ]:
        try:
            await page.click(selector, timeout=2_000)
            return
        except Exception:
            pass


async def _collect_detail_urls(page) -> list[str]:
    try:
        await page.wait_for_selector(
            "a[href*='/en/vacancies/']", timeout=15_000
        )
    except Exception:
        content = await page.content()
        title = await page.title()
        logger.warning(
            "jobs.ch: no job links found. Title: %r. Content[:500]: %.500s",
            title, content,
        )
        return []

    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href*='/en/vacancies/detail/']",
        "els => els.map(el => el.href)",
    )
    seen: set[str] = set()
    result: list[str] = []
    for href in hrefs:
        if href and href not in seen:
            seen.add(href)
            result.append(href)
    if not result:
        title = await page.title()
        logger.warning("jobs.ch: 0 detail URLs found. Page title: %r", title)
    return result


async def _scrape_detail(
    page, url: str, country: str, city: Optional[str]
) -> Optional[JobListing]:
    try:
        await page.goto(url, wait_until="networkidle", timeout=20_000)

        title = await _first_text(
            page,
            ["h1[data-cy='job-title']", "h1.job-title", "h1"],
        )
        if not title:
            page_title = await page.title()
            title = page_title.split("|")[0].strip() if page_title else ""

        company = await _extract_company(page)

        location_str = await _first_text(
            page,
            ["[data-cy='job-location']", ".location", "[class*='location']"],
        ) or city or "Switzerland"

        description = await _first_text(
            page,
            [
                "[data-cy='job-description']",
                ".job-description",
                "main article",
                "main",
            ],
        )

        if not title or not description or len(description) < _MIN_DESC_LEN:
            return None

        return JobListing(
            platform="jobs.ch",
            title=title,
            company=company or "Unknown",
            location=location_str,
            country=country,
            job_url=url,
            description=description,
        )
    except Exception:
        logger.warning("jobs.ch detail scrape failed: %s", url)
        return None


_COMPANY_SELECTORS = [
    "h2.company-name",
    "[data-cy='company-name']",
    ".job-header__company",
    "a.company-link",
]


async def _extract_company(page) -> str:
    for selector in _COMPANY_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if text and text.lower() not in ("explore companies", ""):
                    return text
        except Exception:
            continue
    try:
        el = await page.query_selector("meta[property='og:site_name']")
        if el:
            text = (await el.get_attribute("content") or "").strip()
            if text and text.lower() not in ("explore companies", "jobs.ch", ""):
                return text
    except Exception:
        pass
    return "Unknown"


async def extract_company_from_url(url: str) -> Optional[str]:
    """Re-fetch a jobs.ch detail page and extract the correct company name."""
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20_000)
            company = await _extract_company(page)
            await browser.close()
            return company if company != "Unknown" else None
    except Exception:
        logger.warning("Company re-fetch failed for %s", url)
        return None


async def _first_text(page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text:
                    return text
        except Exception:
            pass
    return ""
