import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

from assistant.job.models import JobListing
from assistant.job.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_MIN_DESC_LEN = 100

# Job detail pages contain a 7+ digit numeric ID separated by / or - in the path.
# Search/category pages like /jobs/Java-Developer/in-Zurich do not.
_JOB_URL_RE = re.compile(
    r"stepstone\.de/(?:stellenangebote--|.*[-/]\d{7,})",
    re.IGNORECASE,
)

# Selectors tried in order; StepStone updates class names/data attrs periodically
_CARD_SELECTORS = [
    "article[data-at='job-item']",
    "[data-at='job-item']",
    "[data-genesis-element='VERTICAL_JOB_CARD']",
    "article[class*='JobCard']",
    "div[data-at='job-item']",
    "[data-testid='job-item']",
]


class StepStoneScraper(BaseScraper):
    """StepStone Germany — used for Swiss cross-border roles and German-market postings."""

    name = "StepStone"

    async def search(
        self, query: str, country: str = "Switzerland", city: Optional[str] = None
    ) -> list[JobListing]:
        from playwright.async_api import async_playwright

        q_slug = quote_plus(query.replace(" ", "-"))
        if city:
            url = f"https://www.stepstone.de/jobs/{q_slug}/in-{quote_plus(city)}"
        else:
            url = f"https://www.stepstone.de/jobs/{q_slug}"

        listings: list[JobListing] = []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="de-CH",
                    extra_http_headers={"Accept-Language": "de-DE,de;q=0.9"},
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await _dismiss_cookie_banner(page)
                # Give React time to render job cards after DOMContentLoaded
                await asyncio.sleep(3)

                detail_urls = await _collect_detail_urls(page)
                for detail_url in detail_urls[: self._max]:
                    listing = await _scrape_detail(page, detail_url, country, city)
                    if listing:
                        listings.append(listing)
                    await asyncio.sleep(self._delay)

                await browser.close()
        except Exception:
            logger.exception("StepStone scrape failed for query=%r", query)

        logger.info("StepStone: %d results for %r", len(listings), query)
        return listings


async def _dismiss_cookie_banner(page) -> None:
    for selector in [
        "#ccmgt_explicit_accept",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Accept all')",
    ]:
        try:
            await page.click(selector, timeout=2_500)
            await asyncio.sleep(0.5)
            return
        except Exception:
            pass


async def _collect_detail_urls(page) -> list[str]:
    # Try known card selectors first (fastest path)
    for sel in _CARD_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=4_000)
            hrefs: list[str] = await page.eval_on_selector_all(
                f"{sel} a",
                "els => [...new Set(els.map(el => el.href).filter(Boolean))]",
            )
            job_urls = [h for h in hrefs if _is_job_detail_url(h)]
            if job_urls:
                logger.debug("StepStone: found %d URLs via selector %r", len(job_urls), sel)
                return job_urls
        except Exception:
            continue

    # Fallback: scan every link on the page for job-like URLs
    try:
        all_hrefs: list[str] = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => el.href).filter(Boolean)",
        )
        job_urls = list(dict.fromkeys(h for h in all_hrefs if _is_job_detail_url(h)))
        if job_urls:
            logger.info("StepStone: found %d URLs via link scan fallback", len(job_urls))
            return job_urls
    except Exception:
        pass

    title = await page.title()
    logger.warning("StepStone: 0 job URLs found. Page title: %r", title)
    return []


def _is_job_detail_url(href: str) -> bool:
    return bool(_JOB_URL_RE.search(href))


async def _scrape_detail(
    page, url: str, country: str, city: Optional[str]
) -> Optional[JobListing]:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        title = await _first_text(
            page,
            [
                "h1[data-at='header-job-title']",
                "h1[data-genesis-element='HEADING']",
                "h1.listing-content-provider--heading",
                "h1",
            ],
        )

        company = await _first_text(
            page,
            [
                "[data-at='header-company-name']",
                "[data-genesis-element='COMPANY_NAME']",
                ".listing-content-provider--company",
                "a[href*='/company/']",
                "a[href*='/unternehmen/']",
            ],
        )

        location_str = await _first_text(
            page,
            [
                "[data-at='job-header-location']",
                "[data-genesis-element='LOCATION']",
                ".listing-content-provider--location",
                "[class*='Location']",
            ],
        ) or city or "Germany/Switzerland"

        description = await _first_text(
            page,
            [
                "[data-at='jobad-description']",
                "[data-genesis-element='JOB_DESCRIPTION']",
                ".listing-content-provider--text",
                "article",
                "main",
            ],
        )

        if not title or not description or len(description) < _MIN_DESC_LEN:
            return None

        return JobListing(
            platform="StepStone",
            title=title,
            company=company or "Unknown",
            location=location_str,
            country=country,
            job_url=url,
            description=description,
        )
    except Exception:
        logger.warning("StepStone detail scrape failed: %s", url)
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
