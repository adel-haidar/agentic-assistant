import asyncio
import logging
from typing import Optional
from urllib.parse import quote_plus

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_DOMAINS = {
    "Switzerland": "ch.indeed.com",
    "Canada": "ca.indeed.com",
    "Norway": "no.indeed.com",
    "Singapore": "sg.indeed.com",
}
_MIN_DESC_LEN = 100


class IndeedScraper(BaseScraper):
    name = "Indeed"

    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]:
        from playwright.async_api import async_playwright

        domain = _DOMAINS.get(country, "www.indeed.com")
        location = city or country
        url = (
            f"https://{domain}/jobs"
            f"?q={quote_plus(query)}&l={quote_plus(location)}&sort=date"
        )

        listings: list[JobListing] = []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await _dismiss_cookie_banner(page)

                detail_urls = await _collect_detail_urls(page, domain)
                for detail_url in detail_urls[: self._max]:
                    listing = await _scrape_detail(page, detail_url, country, city)
                    if listing:
                        listings.append(listing)
                    await asyncio.sleep(self._delay)

                await browser.close()
        except Exception:
            logger.exception(
                "Indeed scrape failed for query=%r country=%s", query, country
            )

        logger.info("Indeed: %d results for %r in %s", len(listings), query, country)
        return listings


async def _dismiss_cookie_banner(page) -> None:
    for selector in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
    ]:
        try:
            await page.click(selector, timeout=2_500)
            await asyncio.sleep(0.5)
            return
        except Exception:
            pass


async def _collect_detail_urls(page, domain: str) -> list[str]:
    try:
        await page.wait_for_selector(
            ".job_seen_beacon, .tapItem, [data-testid='job-title']",
            timeout=15_000,
        )
    except Exception:
        return []

    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href*='/viewjob'], a[href*='/rc/clk'], a[id^='job_']",
        "els => els.map(el => el.href).filter(Boolean)",
    )
    seen: set[str] = set()
    result: list[str] = []
    for href in hrefs:
        if href and href not in seen:
            seen.add(href)
            result.append(href)
    return result


async def _scrape_detail(
    page, url: str, country: str, city: Optional[str]
) -> Optional[JobListing]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)

        title = await _first_text(
            page,
            [
                "h1.jobsearch-JobInfoHeader-title",
                "h1[data-testid='jobsearch-JobInfoHeader-title']",
                "h1",
            ],
        )

        company = await _first_text(
            page,
            [
                "[data-testid='inlineHeader-companyName'] a",
                "[data-testid='inlineHeader-companyName']",
                ".icl-u-lg-mr--sm a",
            ],
        )

        location_str = await _first_text(
            page,
            [
                "[data-testid='inlineHeader-companyLocation']",
                ".jobsearch-JobInfoHeader-subtitle div",
            ],
        ) or city or country

        salary_raw = await _first_text(
            page,
            [
                "[data-testid='attribute_snippet_testid']",
                ".salary-snippet-container",
                ".salaryOnly",
            ],
        ) or None

        description = await _first_text(
            page,
            ["#jobDescriptionText", ".jobsearch-jobDescriptionText"],
        )

        if not title or not description or len(description) < _MIN_DESC_LEN:
            return None

        return JobListing(
            platform="Indeed",
            title=title,
            company=company or "Unknown",
            location=location_str,
            country=country,
            job_url=url,
            salary_raw=salary_raw,
            description=description,
        )
    except Exception:
        logger.warning("Indeed detail scrape failed: %s", url)
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
