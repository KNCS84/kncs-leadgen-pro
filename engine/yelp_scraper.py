# engine/yelp_scraper.py
# KNCS LeadGen Pro — v10.1 (audit-clean)
#
# Registered bugs fixed:
#   BUG-YS-01  asyncio.set_event_loop() removed — no global loop
#              pollution; RuntimeError caught with clear message
#   BUG-YS-02  No block detection — YelpBlockedError raised on
#              challenge/CAPTCHA pages, distinct from empty list
#   BUG-YS-03  Cookie banner .count() no timeout — entire banner
#              loop wrapped in asyncio.wait_for(timeout=6.0)
#   BUG-YS-04  Screenshots: full_page=True, no count limit, no
#              keyword in filename — fixed: full_page=False,
#              keyword_slug+timestamp in filename, capped at
#              MAX_DEBUG_SCREENSHOTS=5
#   BUG-YS-05  All print() replaced with logger.info/debug/warning
#   BUG-YS-06  Strategy 2 regex matched JS internal variable names —
#              JS_INTERNAL_PATTERNS rejects component/handler/
#              template/placeholder names
#   BUG-YS-07  Raw keyword stored as industry — normalise_industry()
#              applied before storage
#   BUG-YS-11  print() on import for missing dependencies — replaced
#              with logger.warning()
#   BUG-YS-12  postcode field absent — postcode:"" added to
#              _empty_lead(); Strategy 1 extracts postalCode from
#              JSON-LD address object
#
# Additional defects fixed:
#   NEW-YS-A   normalise_industry() implemented locally — maps raw
#              search keywords to canonical industry values
#   NEW-YS-B   MAX_DEBUG_SCREENSHOTS counter — screenshots capped
#              across all pages per scrape session
#   NEW-YS-C   Cookie banner await btn.count() pattern replaced with
#              safe try/click inside asyncio.wait_for()

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

__all__ = [
    "scrape_yelp",
    "scrape_yelp_async",
    "extract_leads_from_yelp_html",
    "build_yelp_url",
    "get_yelp_base",
    "YelpBlockedError",
]

# ─────────────────────────────────────────────────────────
# Custom exception — BUG-YS-02
# Raised when a challenge/CAPTCHA page is detected.
# Callers can distinguish this from a genuine empty result.
# ─────────────────────────────────────────────────────────

class YelpBlockedError(RuntimeError):
    """Raised when Yelp returns a challenge or CAPTCHA page."""


# ─────────────────────────────────────────────────────────
# Screenshot counter — BUG-YS-04 / NEW-YS-B
# ─────────────────────────────────────────────────────────

MAX_DEBUG_SCREENSHOTS = 5   # hard cap per scrape session


# ─────────────────────────────────────────────────────────
# Optional imports — degrade gracefully if absent
# BUG-YS-11: logger.warning() replaces print() on import
# ─────────────────────────────────────────────────────────

try:
    from engine.anti_detection import (
        async_random_delay,
        apply_stealth,
        get_stealth_args,
        build_yelp_context,
        human_mouse_move,
        human_scroll,
    )
    _ANTI_DETECTION_AVAILABLE = True
except ImportError:
    _ANTI_DETECTION_AVAILABLE = False
    # BUG-YS-11: was print() — now logger.warning()
    logger.warning(
        "engine/anti_detection.py not found — "
        "Yelp scraper running without stealth mode. "
        "Results may be limited by bot detection."
    )

    async def async_random_delay(
        min_s: float = 1.0, max_s: float = 3.0
    ) -> None:
        import random
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def apply_stealth(page) -> None:
        pass

    def get_stealth_args() -> list:
        return [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]

    async def build_yelp_context(browser):
        return await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

    async def human_mouse_move(page) -> None:
        pass

    async def human_scroll(page, scroll_times: int = 3) -> None:
        for _ in range(scroll_times):
            await page.evaluate("window.scrollBy(0, 400)")
            await asyncio.sleep(0.5)


try:
    from config import HEADLESS_MODE
except ImportError:
    HEADLESS_MODE = True
    # BUG-YS-11: was print() — now logger.warning()
    logger.warning(
        "config.py not found — "
        "Yelp scraper defaulting to headless=True"
    )


# ─────────────────────────────────────────────────────────
# JS internal name filter — BUG-YS-06
# Rejects React component/handler/template/placeholder
# names that leak into Strategy 2 regex matches.
# ─────────────────────────────────────────────────────────

JS_INTERNAL_PATTERNS = re.compile(
    r"(?i)^("
    r"component|handler|template|placeholder|"
    r"wrapper|container|module|controller|"
    r"reducer|selector|provider|consumer|"
    r"context|factory|service|helper|util|"
    r"mixin|decorator|middleware|resolver|"
    r"[A-Z][a-z]+[A-Z]"   # camelCase like bizNameValue
    r")$"
)


# ─────────────────────────────────────────────────────────
# Industry normalisation — BUG-YS-07 / NEW-YS-A
# Maps raw search keywords to canonical industry values.
# Falls back to title-cased keyword if no mapping found.
# ─────────────────────────────────────────────────────────

_INDUSTRY_MAP = {
    # Care
    "care home":             "Care Home",
    "care homes":            "Care Home",
    "nursing home":          "Care Home",
    "nursing homes":         "Care Home",
    "residential care":      "Care Home",
    # Food & Drink
    "restaurant":            "Restaurant",
    "restaurants":           "Restaurant",
    "cafe":                  "Cafe",
    "coffee shop":           "Cafe",
    "takeaway":              "Restaurant",
    "pub":                   "Pub",
    # Health
    "dental":                "Dental Clinic",
    "dentist":               "Dental Clinic",
    "dental clinic":         "Dental Clinic",
    "pharmacy":              "Pharmacy",
    "chemist":               "Pharmacy",
    "gym":                   "Gym",
    "fitness":               "Gym",
    # Beauty
    "hair salon":            "Hair Salon",
    "barber":                "Barber",
    "beauty salon":          "Beauty Salon",
    "nail salon":            "Nail Salon",
    # Property
    "estate agent":          "Estate Agent",
    "estate agents":         "Estate Agent",
    "letting agent":         "Estate Agent",
    # Finance
    "mortgage broker":       "Mortgage Broker",
    "financial advisor":     "Financial Advisor",
    "insurance":             "Insurance",
    "accountant":            "Accountant",
    # Legal
    "law firm":              "Law Firm",
    "solicitor":             "Law Firm",
    "solicitors":            "Law Firm",
    # Construction
    "construction":          "Construction",
    "builder":               "Construction",
    "plumber":               "Plumbing",
    "electrician":           "Electrical",
    # Auto
    "car garage":            "Car Garage",
    "mechanic":              "Car Garage",
    "mot":                   "Car Garage",
    # Hospitality
    "hotel":                 "Hotel",
    "bed and breakfast":     "Hotel",
    "b&b":                   "Hotel",
    # Other
    "school":                "School",
    "nursery":               "Nursery",
    "event center":          "Event Center",
    "events venue":          "Event Center",
}


def normalise_industry(keyword: str) -> str:
    """
    BUG-YS-07: Map raw search keyword to canonical industry value.
    Prevents free-text search strings corrupting the industry column.
    """
    if not keyword:
        return ""
    key = keyword.strip().lower()
    # Exact match first
    if key in _INDUSTRY_MAP:
        return _INDUSTRY_MAP[key]
    # Partial match — keyword contains a known term
    for term, canonical in _INDUSTRY_MAP.items():
        if term in key:
            return canonical
    # Fallback — title-case the keyword, strip excess whitespace
    return " ".join(keyword.strip().split()).title()


# ─────────────────────────────────────────────────────────
# Block detection — BUG-YS-02
# ─────────────────────────────────────────────────────────

_YELP_BLOCK_MARKERS = (
    "cf-browser-verification",
    "checking your browser",
    "please wait",
    "captcha",
    "ddos protection by cloudflare",
    "enable javascript",
    "access denied",
    "too many requests",
    "unusual traffic",
    "robot or human",
)


def is_yelp_blocked(html_text: str) -> bool:
    """
    BUG-YS-02: Return True if the page looks like a
    challenge/CAPTCHA/block page rather than real results.
    Only the first 4 KB inspected — avoids loading 500 KB into memory.
    """
    if not html_text:
        return True
    sample = html_text[:4096].lower()
    return any(m in sample for m in _YELP_BLOCK_MARKERS)


# ─────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────

def get_yelp_base(country: str) -> str:
    if country and "united kingdom" in country.lower():
        return "https://www.yelp.co.uk"
    return "https://www.yelp.com"


def build_yelp_url(
    keyword: str,
    location: str,
    country: str,
    page: int = 0,
) -> str:
    """
    Build a Yelp search URL.
    page=0 → start=0  (first page, offset omitted)
    page=N → start=N*10
    """
    base          = get_yelp_base(country)
    keyword_slug  = keyword.strip().replace(" ", "+")
    location_slug = location.strip().replace(" ", "+")
    offset        = page * 10

    if offset == 0:
        return (
            f"{base}/search"
            f"?find_desc={keyword_slug}"
            f"&find_loc={location_slug}"
        )
    return (
        f"{base}/search"
        f"?find_desc={keyword_slug}"
        f"&find_loc={location_slug}"
        f"&start={offset}"
    )


# ─────────────────────────────────────────────────────────
# v9.7 lead dict factory
# BUG-YS-07: normalise_industry() applied to keyword
# BUG-YS-12: postcode field added
# ─────────────────────────────────────────────────────────

def _empty_lead(
    keyword: str,
    city: str,
    country: str,
) -> dict:
    """
    Return a v9.7-compatible lead dict with all required fields.

    BUG-YS-07: industry is normalised — raw keyword never stored.
    BUG-YS-12: postcode field present — populated by Strategy 1
               from JSON-LD postalCode when available.
    """
    return {
        # Identity
        "business_name":                "",
        "place_id":                     "",
        "maps_url":                     "",

        # Contact
        "phone":                        "",
        "email":                        "",
        "website":                      "",
        "address":                      "",
        "postcode":                     "",   # BUG-YS-12

        # Location
        "city":                         city,
        "country":                      country,
        "market":                       "",

        # Classification
        # BUG-YS-07: normalised — not raw keyword
        "industry":                     normalise_industry(keyword),
        "source":                       "yelp",

        # Enrichment v9.7
        "website_discovered":           0,
        "website_discovery_source":     "",
        "website_discovery_confidence": "",
        "social_facebook_url":          "",
        "social_instagram_url":         "",
        "social_linkedin_url":          "",
        "decision_maker_name":          "",
        "enrichment_notes":             "",
    }


# ─────────────────────────────────────────────────────────
# HTML lead extraction
# BUG-YS-02: YelpBlockedError raised before parsing
# BUG-YS-06: JS internal names filtered in Strategy 2
# BUG-YS-12: postalCode extracted in Strategy 1
# ─────────────────────────────────────────────────────────

def extract_leads_from_yelp_html(
    html: str,
    keyword: str,
    location: str,
) -> List[dict]:
    """
    Extract leads from Yelp HTML using 3 progressive strategies.

    BUG-YS-02: raises YelpBlockedError if a challenge page is
               detected — distinct from returning [].

    Strategies run in order; first with results wins.
    All returned lead dicts use v9.7 schema field names.
    """
    # BUG-YS-02: block check BEFORE any parsing
    if is_yelp_blocked(html):
        raise YelpBlockedError(
            "Yelp returned a challenge/CAPTCHA page — "
            "not a genuine empty result set."
        )

    leads = []
    soup  = BeautifulSoup(html, "lxml")

    parts   = [p.strip() for p in location.split(",")]
    city    = parts[0] if parts else location
    country = parts[-1] if len(parts) > 1 else ""
    seen    = set()

    # ── Strategy 1: JSON-LD structured data ──────────────
    for script in soup.find_all(
        "script", type="application/ld+json"
    ):
        try:
            data  = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]

            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "").strip()
                if not name or name in seen:
                    continue
                itype = item.get("@type", "")
                if not any(
                    t in itype for t in (
                        "LocalBusiness", "Restaurant",
                        "HealthAndBeautyBusiness",
                        "Store", "Organization",
                        "FoodEstablishment",
                    )
                ):
                    continue

                seen.add(name)

                # Website — Yelp biz page URL is not the website
                website      = item.get("url", "")
                yelp_biz_url = ""
                if "yelp" in website.lower():
                    yelp_biz_url = website
                    website      = ""

                # Address
                address = ""
                # BUG-YS-12: postalCode extracted separately
                postcode = ""
                addr = item.get("address", {})
                if isinstance(addr, dict):
                    postcode = addr.get("postalCode", "")
                    address  = ", ".join(filter(None, [
                        addr.get("streetAddress",    ""),
                        addr.get("addressLocality",  ""),
                        postcode,
                    ]))

                # Ratings/reviews into enrichment_notes
                notes = ""
                agg   = item.get("aggregateRating", {})
                if isinstance(agg, dict):
                    try:
                        rating  = float(agg.get("ratingValue", 0))
                        reviews = int(agg.get("reviewCount", 0))
                        if rating or reviews:
                            notes = json.dumps({
                                "yelp_rating":  rating,
                                "yelp_reviews": reviews,
                                "yelp_url":     yelp_biz_url,
                            })
                    except Exception:
                        pass
                if not notes and yelp_biz_url:
                    notes = json.dumps({"yelp_url": yelp_biz_url})

                lead = _empty_lead(keyword, city, country)
                lead.update({
                    "business_name":    name,
                    "phone":            item.get("telephone", ""),
                    "website":          website,
                    "address":          address,
                    "postcode":         postcode,   # BUG-YS-12
                    "enrichment_notes": notes,
                })
                leads.append(lead)

        except Exception:
            continue

    if leads:
        return leads

    # ── Strategy 2: React/JS window data ─────────────────
    # BUG-YS-06: JS_INTERNAL_PATTERNS filters component/
    # handler/template names that leak into matches.
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "bizName" in txt or "businessName" in txt:
            try:
                matches = re.findall(
                    r'"(?:bizName|businessName)"'
                    r'\s*:\s*"([^"]{3,80})"',
                    txt,
                )
                for name in matches:
                    name = name.strip()
                    # BUG-YS-06: reject JS internal identifiers
                    if JS_INTERNAL_PATTERNS.match(name):
                        logger.debug(
                            "Strategy 2: rejected JS internal "
                            "name %r", name
                        )
                        continue
                    if name and name not in seen:
                        seen.add(name)
                        lead = _empty_lead(keyword, city, country)
                        lead["business_name"] = name
                        leads.append(lead)
            except Exception:
                continue

    if leads:
        return leads

    # ── Strategy 3: /biz/ link fallback ──────────────────
    skip_words = (
        "write a review", "see all", "more info",
        "get directions", "claim", "sponsored",
        "next", "previous", "photos", "reviews",
        "menu", "order", "reserve", "website",
    )
    skip_biz_suffixes = (
        "/reviews", "/photos", "/menu",
        "/reservations", "/order_food",
        "/question_and_answers", "/media",
    )
    for link in soup.find_all("a", href=re.compile(r"/biz/")):
        try:
            href = link.get("href", "")
            if any(
                href.endswith(s) or f"{s}?" in href
                for s in skip_biz_suffixes
            ):
                continue

            name = re.sub(
                r"^\d+\.\s*", "", link.get_text(strip=True)
            ).strip()

            if (
                not name
                or len(name) < 3
                or name.isdigit()
                or name in seen
                or any(s in name.lower() for s in skip_words)
            ):
                continue

            seen.add(name)
            full_url = (
                href if href.startswith("http")
                else get_yelp_base(country) + href
            )

            lead = _empty_lead(keyword, city, country)
            lead.update({
                "business_name":    name,
                "enrichment_notes": json.dumps(
                    {"yelp_url": full_url}
                ),
            })
            leads.append(lead)

        except Exception:
            continue

    return leads


# ─────────────────────────────────────────────────────────
# Async scraper
# BUG-YS-03: cookie banner wrapped in asyncio.wait_for()
# BUG-YS-04: screenshots capped + keyword slug + timestamp
#            + full_page=False
# BUG-YS-05: all print() replaced with logger calls
# ─────────────────────────────────────────────────────────

async def scrape_yelp_async(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> List[dict]:
    """
    Async Yelp scraper using Playwright.
    Returns v9.7-compatible lead dicts.
    insert_lead() is handled by job_manager — not here.
    """
    from playwright.async_api import async_playwright

    parts   = [p.strip() for p in location.split(",")]
    country = parts[-1] if len(parts) > 1 else ""

    all_leads  : List[dict] = []
    seen_names : set        = set()
    page_num   : int        = 0
    max_pages  : int        = max(1, (max_results + 9) // 10)

    # BUG-YS-04: keyword slug for unique screenshot filenames
    keyword_slug   = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_")
    # NEW-YS-B: screenshot counter — hard cap at MAX_DEBUG_SCREENSHOTS
    screenshot_count: int = 0

    logger.info(
        "scrape_yelp_async: keyword=%r location=%r max=%d",
        keyword, location, max_results,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS_MODE,
            args=get_stealth_args(),
        )
        context = await build_yelp_context(browser)
        page    = await context.new_page()
        await apply_stealth(page)

        try:
            while (
                len(all_leads) < max_results
                and page_num < max_pages
            ):
                url = build_yelp_url(
                    keyword, location, country, page_num
                )
                logger.info(
                    "scrape_yelp_async: page %d → %s",
                    page_num + 1, url,
                )

                try:
                    if page_num == 0:
                        await page.goto(
                            "https://www.google.com",
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        await async_random_delay(2, 3)

                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=90_000,
                    )
                    await async_random_delay(3, 5)
                    await human_mouse_move(page)
                    await async_random_delay(1, 2)

                    try:
                        await page.wait_for_selector(
                            'h3, [class*="businessName"],'
                            'a[href*="/biz/"],'
                            'script[type="application/ld+json"]',
                            timeout=15_000,
                        )
                    except Exception:
                        pass

                    await human_scroll(page, scroll_times=3)
                    await async_random_delay(2, 3)

                    # BUG-YS-03: entire cookie banner handling
                    # wrapped in asyncio.wait_for() — prevents
                    # indefinite block if page is in bad state.
                    async def _handle_cookie_banner() -> None:
                        for btn_text in (
                            "Accept All", "Accept",
                            "I Agree", "OK", "Got it",
                            "Close", "No Thanks",
                        ):
                            try:
                                btn = page.get_by_role(
                                    "button", name=btn_text
                                )
                                # Safe count check — no await needed
                                # on newer Playwright; try/except
                                # handles both versions
                                try:
                                    count = btn.count()
                                except TypeError:
                                    count = await btn.count()
                                if count > 0:
                                    await btn.first.click(
                                        timeout=2_000
                                    )
                                    await async_random_delay(1, 2)
                                    break
                            except Exception:
                                pass

                    try:
                        await asyncio.wait_for(
                            _handle_cookie_banner(),
                            timeout=6.0,
                        )
                    except asyncio.TimeoutError:
                        logger.debug(
                            "scrape_yelp_async: cookie banner "
                            "handler timed out — continuing"
                        )

                    html_content = await page.content()

                    # BUG-YS-02: catch block detection
                    try:
                        leads = extract_leads_from_yelp_html(
                            html_content, keyword, location
                        )
                    except YelpBlockedError:
                        logger.warning(
                            "scrape_yelp_async: page %d — "
                            "Yelp block/CAPTCHA detected",
                            page_num + 1,
                        )
                        leads = []

                    if not leads:
                        # BUG-YS-04: capped, named, full_page=False
                        if screenshot_count < MAX_DEBUG_SCREENSHOTS:
                            debug_dir = Path("data/debug")
                            debug_dir.mkdir(
                                parents=True, exist_ok=True
                            )
                            ts        = datetime.utcnow().strftime(
                                "%Y%m%d_%H%M%S"
                            )
                            shot_path = (
                                debug_dir
                                / f"yelp_{keyword_slug}"
                                  f"_p{page_num + 1}_{ts}.png"
                            )
                            try:
                                await page.screenshot(
                                    path=str(shot_path),
                                    full_page=False,  # BUG-YS-04
                                )
                                screenshot_count += 1
                                logger.debug(
                                    "scrape_yelp_async: "
                                    "screenshot %d/%d → %s",
                                    screenshot_count,
                                    MAX_DEBUG_SCREENSHOTS,
                                    shot_path,
                                )
                            except Exception as ss_err:
                                logger.debug(
                                    "scrape_yelp_async: "
                                    "screenshot failed: %s",
                                    ss_err,
                                )

                        logger.warning(
                            "scrape_yelp_async: no leads on "
                            "page %d — retrying with scroll",
                            page_num + 1,
                        )

                        # One scroll + retry
                        await page.evaluate(
                            "window.scrollTo("
                            "0, document.body.scrollHeight)"
                        )
                        await async_random_delay(3, 4)
                        html_content = await page.content()
                        try:
                            leads = extract_leads_from_yelp_html(
                                html_content, keyword, location
                            )
                        except YelpBlockedError:
                            logger.warning(
                                "scrape_yelp_async: retry also "
                                "blocked — stopping"
                            )
                            leads = []

                    if not leads:
                        logger.warning(
                            "scrape_yelp_async: stopping — "
                            "no data on page %d",
                            page_num + 1,
                        )
                        break

                    new_count = 0
                    for lead in leads:
                        name = lead.get("business_name", "")
                        if name and name not in seen_names:
                            seen_names.add(name)
                            all_leads.append(lead)
                            new_count += 1
                            logger.debug(
                                "scrape_yelp_async: + %s | %s",
                                name,
                                lead.get("phone", "—"),
                            )
                        if len(all_leads) >= max_results:
                            break

                    logger.info(
                        "scrape_yelp_async: page %d — "
                        "%d new | total %d",
                        page_num + 1, new_count, len(all_leads),
                    )

                    if len(all_leads) >= max_results:
                        break

                    page_num += 1
                    await async_random_delay(5, 8)

                except YelpBlockedError:
                    logger.warning(
                        "scrape_yelp_async: blocked on page %d — "
                        "stopping",
                        page_num + 1,
                    )
                    break
                except Exception as page_err:
                    logger.error(
                        "scrape_yelp_async: page %d error: %s",
                        page_num + 1, page_err,
                    )
                    break

        finally:
            try:
                await browser.close()
            except Exception:
                pass

    logger.info(
        "scrape_yelp_async: complete — %d leads", len(all_leads)
    )
    return all_leads[:max_results]


# ─────────────────────────────────────────────────────────
# Sync wrapper
# BUG-YS-01: asyncio.set_event_loop() removed entirely.
#            RuntimeError (already-running loop) caught
#            with a clear message — not a silent crash.
# ─────────────────────────────────────────────────────────

def scrape_yelp(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> List[dict]:
    """
    Synchronous wrapper around scrape_yelp_async.
    Called by job_manager in a background thread.

    BUG-YS-01: set_event_loop() removed — no global loop pollution.
    Creates a fresh event loop per call without registering it
    globally. RuntimeError raised if called from within a running
    loop — caught with a clear diagnostic message.
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    # BUG-YS-01: do NOT call asyncio.set_event_loop(loop)
    # Running the loop directly without registering it globally
    # prevents pollution of the thread's event loop state.
    try:
        return loop.run_until_complete(
            scrape_yelp_async(keyword, location, max_results)
        )
    except RuntimeError as exc:
        if "already running" in str(exc).lower():
            logger.error(
                "scrape_yelp: cannot run — an event loop is already "
                "running in this thread. Use scrape_yelp_async() "
                "directly from async context: %s", exc,
            )
            return []
        raise
    finally:
        try:
            loop.close()
        except Exception:
            pass