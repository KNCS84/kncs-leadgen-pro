# engine/google_maps_scraper.py
# KNCS LeadGen Pro — v9.7
# Google Maps scraper using Playwright (async) + BeautifulSoup fallback.
#
# Architecture:
#   scrape_google_maps(keyword, location, max_results)
#     → async generator / list of lead dicts
#     → integrates with v9.7 deduplicator + insert_lead
#
# Design decisions:
#   - Playwright headless Chromium (handles JS-rendered Maps)
#   - Extracts: name, phone, website, address, rating, category
#   - place_id extracted from Maps URL (v9.7 primary dedup key)
#   - maps_url stored (v9.7 secondary dedup key)
#   - Website presence checked carefully — never false "no website"
#   - Polite delays + random user agent rotation
#   - Stops cleanly when max_results reached

from __future__ import annotations

import asyncio
import random
import re
import time
import logging
from typing import Optional
from urllib.parse import quote_plus, urlparse

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Safe imports
# ─────────────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    logger.warning("playwright not installed — Google Maps scraper disabled")

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    from fake_useragent import UserAgent
    _UA = UserAgent()
    def _random_ua() -> str:
        try:
            return _UA.chrome
        except Exception:
            return (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
except ImportError:
    def _random_ua() -> str:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

MAPS_BASE       = "https://www.google.com/maps/search/"
MIN_DELAY       = 1.8    # seconds between panel opens
MAX_DELAY       = 3.5
SCROLL_PAUSE    = 1.2
MAX_SCROLL_TRIES = 40    # max scroll attempts before giving up

# CSS selectors (Google Maps — may need periodic updates)
SEL_RESULT_ITEM = 'a[href*="/maps/place/"]'
SEL_NAME        = 'h1[class*="fontHeadlineLarge"]'
SEL_CATEGORY    = 'button[jsaction*="category"]'
SEL_ADDRESS     = '[data-item-id="address"] .fontBodyMedium'
SEL_PHONE       = '[data-item-id^="phone:tel"] .fontBodyMedium'
SEL_WEBSITE     = '[data-item-id="authority"] a'
SEL_RATING      = 'div[jsaction*="reviewSort"] span[aria-label]'
SEL_REVIEWS     = 'span[aria-label*="review"]'


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _extract_place_id(url: str) -> str:
    """
    Extract place_id from Google Maps URL.
    Format: /maps/place/Name/data=...!1s<PLACE_ID>!...
    or: /maps/place/...?...&place_id=<ID>
    Returns empty string if not found.
    """
    if not url:
        return ""

    # Pattern 1: ChIJ... style place ID in URL path
    match = re.search(r"!1s(ChIJ[^!&?]+)", url)
    if match:
        return match.group(1)

    # Pattern 2: place_id= query parameter
    match = re.search(r"place_id=([^&]+)", url)
    if match:
        return match.group(1)

    # Pattern 3: hex-format place ID
    match = re.search(r"!1s(0x[0-9a-fA-F]+:[0-9a-fA-F]+)", url)
    if match:
        return match.group(1)

    return ""


def _clean_phone(phone: str) -> str:
    """Normalize phone number."""
    if not phone:
        return ""
    cleaned = re.sub(r"[^\d\+\-\(\)\s]", "", str(phone)).strip()
    return cleaned if len(re.sub(r"\D", "", cleaned)) >= 7 else ""


def _clean_website(url: str) -> str:
    """
    Clean and validate website URL.
    Critical: never return empty string for a valid URL.
    Never claim "no website" if a URL exists.
    """
    if not url:
        return ""
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        if parsed.netloc:
            return url
    except Exception:
        pass
    return ""


def _build_search_url(keyword: str, location: str) -> str:
    """Build Google Maps search URL."""
    query = f"{keyword} {location}".strip()
    return f"{MAPS_BASE}{quote_plus(query)}"


def _polite_delay(min_s: float = MIN_DELAY, max_s: float = MAX_DELAY):
    """Random polite delay between requests."""
    time.sleep(random.uniform(min_s, max_s))


def _parse_rating(text: str) -> float:
    """Parse '4.5 stars' or '4.5' → 4.5"""
    if not text:
        return 0.0
    match = re.search(r"(\d+\.?\d*)", str(text))
    return float(match.group(1)) if match else 0.0


def _parse_review_count(text: str) -> int:
    """Parse '(1,234 reviews)' → 1234"""
    if not text:
        return 0
    match = re.search(r"([\d,]+)", str(text).replace(",", ""))
    return int(match.group(1).replace(",", "")) if match else 0


# ─────────────────────────────────────────────────────────
# Core async scraper
# ─────────────────────────────────────────────────────────

async def _scrape_listing_panel(page, url: str, keyword: str, location: str) -> dict:
    """
    Navigate to a single Maps listing panel and extract all fields.
    Returns a lead dict.
    """
    lead = {
        "source":        "google_maps",
        "maps_url":      url,
        "place_id":      _extract_place_id(url),
        "industry":      keyword,
        "location":      location,
        "business_name": "",
        "phone":         "",
        "website":       "",
        "address":       "",
        "city":          "",
        "country":       "",
        "category":      "",
        "rating":        0.0,
        "review_count":  0,
        "contact_status": "new",
        "enrichment_status": "pending",
    }

    try:
        # Navigate to listing
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(random.randint(800, 1400))

        # Business name
        try:
            el = await page.query_selector(SEL_NAME)
            if el:
                lead["business_name"] = (await el.inner_text()).strip()
        except Exception:
            pass

        # Category / industry
        try:
            el = await page.query_selector(SEL_CATEGORY)
            if el:
                cat = (await el.inner_text()).strip()
                lead["category"] = cat
                if not lead["industry"] or lead["industry"] == keyword:
                    lead["industry"] = cat
        except Exception:
            pass

        # Address
        try:
            el = await page.query_selector(SEL_ADDRESS)
            if el:
                addr = (await el.inner_text()).strip()
                lead["address"] = addr
                # Extract city from address (last meaningful part before country)
                parts = [p.strip() for p in addr.split(",")]
                if len(parts) >= 2:
                    lead["city"] = parts[-2] if len(parts) >= 2 else parts[0]
        except Exception:
            pass

        # Phone
        try:
            el = await page.query_selector(SEL_PHONE)
            if el:
                raw_phone = (await el.inner_text()).strip()
                lead["phone"] = _clean_phone(raw_phone)
        except Exception:
            pass

        # Website — CRITICAL: check multiple selectors, never false negative
        try:
            # Primary selector
            el = await page.query_selector(SEL_WEBSITE)
            if el:
                href = await el.get_attribute("href")
                if href:
                    lead["website"] = _clean_website(href)

            # Fallback: any outbound link that isn't Google
            if not lead["website"]:
                links = await page.query_selector_all('a[href^="http"]')
                for link in links:
                    href = await link.get_attribute("href") or ""
                    if (
                        href
                        and "google.com" not in href
                        and "goo.gl" not in href
                        and "maps" not in href.lower()
                        and len(href) > 10
                    ):
                        cleaned = _clean_website(href)
                        if cleaned:
                            lead["website"] = cleaned
                            break

        except Exception:
            pass

        # Rating
        try:
            el = await page.query_selector(SEL_RATING)
            if el:
                aria = await el.get_attribute("aria-label") or ""
                lead["rating"] = _parse_rating(aria)
        except Exception:
            pass

        # Review count
        try:
            els = await page.query_selector_all(SEL_REVIEWS)
            for el in els:
                aria = await el.get_attribute("aria-label") or ""
                count = _parse_review_count(aria)
                if count > 0:
                    lead["review_count"] = count
                    break
        except Exception:
            pass

    except PWTimeout:
        logger.debug("Timeout loading listing: %s", url)
    except Exception as exc:
        logger.debug("Error extracting listing %s: %s", url, exc)

    return lead


async def _scroll_results_panel(page, max_results: int) -> list:
    """
    Scroll the left results panel to load more listings.
    Returns list of Maps listing URLs.
    """
    urls: list = []
    seen: set  = set()

    try:
        # Wait for results panel
        await page.wait_for_selector(SEL_RESULT_ITEM, timeout=10000)
    except Exception:
        logger.debug("Results panel not found — page may have changed layout")
        return urls

    scroll_tries = 0

    while len(urls) < max_results and scroll_tries < MAX_SCROLL_TRIES:
        # Collect current visible results
        try:
            items = await page.query_selector_all(SEL_RESULT_ITEM)
            for item in items:
                href = await item.get_attribute("href") or ""
                if href and href not in seen and "/maps/place/" in href:
                    seen.add(href)
                    urls.append(href)
                    if len(urls) >= max_results:
                        break
        except Exception:
            pass

        if len(urls) >= max_results:
            break

        # Check if we hit the "end of results" sentinel
        try:
            end_marker = await page.query_selector(
                'span[class*="HlvSq"]'  # "You've reached the end of the list"
            )
            if end_marker:
                logger.debug("Reached end of Maps results")
                break
        except Exception:
            pass

        # Scroll the left panel
        try:
            panel = await page.query_selector('div[role="feed"]')
            if panel:
                await panel.evaluate("el => el.scrollTop += 800")
            else:
                await page.mouse.wheel(0, 800)
        except Exception:
            await page.mouse.wheel(0, 800)

        await page.wait_for_timeout(int(SCROLL_PAUSE * 1000))
        scroll_tries += 1

    logger.debug("Collected %d result URLs after %d scroll attempts", len(urls), scroll_tries)
    return urls[:max_results]


async def scrape_google_maps(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> list:
    """
    Main async entry point for Google Maps scraping.

    Args:
        keyword:     Business type / niche (e.g. "private school")
        location:    City + country (e.g. "Benin City, Nigeria")
        max_results: Maximum leads to return

    Returns:
        List of lead dicts — v9.7 schema compatible.
        Each lead includes place_id and maps_url for dedup.

    Usage (from job_manager via run_async):
        leads = run_async(scrape_google_maps("school", "Benin City", 20))
    """
    if not _PLAYWRIGHT_AVAILABLE:
        logger.error("Playwright not installed — cannot scrape Google Maps")
        return []

    if not keyword or not location:
        logger.warning("scrape_google_maps: keyword and location required")
        return []

    search_url = _build_search_url(keyword, location)
    logger.info("Google Maps search: %s", search_url)

    results: list = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            user_agent=_random_ua(),
            viewport={"width": 1280, "height": 900},
            locale="en-GB",
            timezone_id="Africa/Lagos",      # Appropriate for Nigeria-first targeting
        )

        # Block unnecessary resources to speed up scraping
        await context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,css}",
            lambda route: route.abort()
        )

        page = await context.new_page()

        try:
            # Load search results page
            logger.debug("Loading Maps search page...")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(random.randint(1500, 2500))

            # Handle cookie consent if shown
            try:
                consent_btn = await page.query_selector(
                    'button[aria-label*="Accept"], form[action*="consent"] button'
                )
                if consent_btn:
                    await consent_btn.click()
                    await page.wait_for_timeout(800)
            except Exception:
                pass

            # Collect listing URLs by scrolling
            listing_urls = await _scroll_results_panel(page, max_results)
            logger.info("Found %d listing URLs to process", len(listing_urls))

            if not listing_urls:
                logger.warning("No listing URLs found — Maps layout may have changed")

            # Process each listing
            for idx, url in enumerate(listing_urls):
                logger.debug("[%d/%d] Scraping: %s", idx + 1, len(listing_urls), url[:80])

                try:
                    lead = await _scrape_listing_panel(page, url, keyword, location)

                    # Quality gate: must have a business name
                    if not lead.get("business_name"):
                        logger.debug("Skipping — no business name: %s", url)
                        continue

                    # Stamp location if not detected from listing
                    if not lead.get("city"):
                        loc_parts = [p.strip() for p in location.split(",")]
                        lead["city"] = loc_parts[0] if loc_parts else location

                    if not lead.get("country"):
                        loc_parts = [p.strip() for p in location.split(",")]
                        lead["country"] = loc_parts[-1] if len(loc_parts) > 1 else ""

                    results.append(lead)
                    logger.info(
                        "  ✅ [%d] %s | phone=%s | website=%s | place_id=%s",
                        len(results),
                        lead.get("business_name", "?")[:40],
                        "✓" if lead.get("phone") else "✗",
                        "✓" if lead.get("website") else "✗",
                        lead.get("place_id", "")[:20] or "none",
                    )

                except Exception as exc:
                    logger.warning("Error processing listing %s: %s", url[:60], exc)

                # Polite delay between listings
                _polite_delay(MIN_DELAY, MAX_DELAY)

                if len(results) >= max_results:
                    break

        except Exception as exc:
            logger.error("Google Maps scraper failed: %s", exc)
            raise

        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    logger.info(
        "Google Maps scrape complete: %d leads from '%s %s'",
        len(results), keyword, location
    )
    return results


# ─────────────────────────────────────────────────────────
# Convenience sync wrapper (for testing outside job_manager)
# ─────────────────────────────────────────────────────────

def scrape_google_maps_sync(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> list:
    """
    Synchronous wrapper for use in testing or non-async contexts.
    In production, use run_async(scrape_google_maps(...)) from job_manager.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            scrape_google_maps(keyword, location, max_results)
        )
    except Exception as exc:
        logger.error("scrape_google_maps_sync error: %s", exc)
        return []
    finally:
        try:
            loop.close()
        except Exception:
            pass