# engine/yellowpages_scraper.py
# KNCS LeadGen Pro — v10.1 (audit-clean)
#
# Registered bugs fixed:
#   BUG-YP-01  asyncio.set_event_loop() removed — no global loop
#              pollution; RuntimeError caught with clear message
#   BUG-YP-02  No block detection — YPBlockedError raised on
#              challenge/CAPTCHA pages, distinct from empty list
#   BUG-YP-03  Cookie banner .count() no timeout — entire banner
#              loop wrapped in asyncio.wait_for(timeout=6.0)
#   BUG-YP-04  Two screenshot blocks: full_page=True, no count
#              limit, no keyword in filename — fixed: single
#              counter, keyword_slug+timestamp, full_page=False,
#              capped at MAX_DEBUG_SCREENSHOTS=5
#   BUG-YP-05  _warmup_browser() print() only + no CAPTCHA
#              detection — logger used; is_yp_blocked() check
#              after each warmup page load
#   BUG-YP-06  Raw keyword as industry; postcode field missing —
#              normalise_industry() applied; postcode:"" added
#   BUG-YP-07  All progress via print() — replaced with logger
#   BUG-YP-08  print() on import for missing dependencies —
#              replaced with logger.warning()
#   BUG-YP-12  postalCode from JSON-LD not extracted to postcode
#              field — addr.get("postalCode","") now extracted
#   BUG-YP-13  Screenshot failure in error handler silently
#              swallowed with pass — logged at DEBUG level
#
# Additional defects fixed:
#   NEW-YP-A   normalise_industry() implemented locally — maps
#              raw keywords to canonical industry values
#   NEW-YP-B   is_yp_blocked() used in warmup AND main loop —
#              CAPTCHA state cannot carry into real scrape
#   NEW-YP-C   Screenshot counter unified — empty-page and error
#              handler screenshots share single cap

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

__all__ = [
    "scrape_yellowpages",
    "scrape_yellowpages_async",
    "extract_yp_leads",
    "get_yp_url",
    "YPBlockedError",
]

# ─────────────────────────────────────────────────────────
# Custom exception — BUG-YP-02
# ─────────────────────────────────────────────────────────

class YPBlockedError(RuntimeError):
    """Raised when YellowPages/Yell returns a challenge page."""


# ─────────────────────────────────────────────────────────
# Screenshot counter — BUG-YP-04 / NEW-YP-C
# Shared across both empty-page and error-handler blocks.
# ─────────────────────────────────────────────────────────

MAX_DEBUG_SCREENSHOTS = 5


# ─────────────────────────────────────────────────────────
# Optional imports — BUG-YP-08: logger.warning not print()
# ─────────────────────────────────────────────────────────

try:
    from engine.anti_detection import (
        async_random_delay,
        apply_stealth,
        get_stealth_args,
        build_yp_context,
        human_mouse_move,
        human_scroll,
    )
    _ANTI_DETECTION_AVAILABLE = True
except ImportError:
    _ANTI_DETECTION_AVAILABLE = False
    # BUG-YP-08: was print() — now logger.warning()
    logger.warning(
        "engine/anti_detection.py not found — "
        "YellowPages scraper running without stealth mode."
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

    async def build_yp_context(browser, is_uk: bool = False):
        return await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

    async def human_mouse_move(page) -> None:
        pass

    async def human_scroll(
        page, scroll_times: int = 3
    ) -> None:
        for _ in range(scroll_times):
            await page.evaluate("window.scrollBy(0, 400)")
            await asyncio.sleep(0.5)


try:
    from config import HEADLESS_MODE
except ImportError:
    HEADLESS_MODE = True
    # BUG-YP-08: was print() — now logger.warning()
    logger.warning(
        "config.py not found — "
        "YellowPages scraper defaulting to headless=True"
    )


# ─────────────────────────────────────────────────────────
# Block detection — BUG-YP-02 / NEW-YP-B
# ─────────────────────────────────────────────────────────

_YP_BLOCK_MARKERS = (
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
    "just a moment",
    "verify you are human",
)


def is_yp_blocked(html_text: str) -> bool:
    """
    BUG-YP-02 / NEW-YP-B: Return True if the page looks like a
    challenge/CAPTCHA/block page rather than real results.
    Only first 4 KB inspected — avoids 500 KB string allocation.
    """
    if not html_text:
        return True
    sample = html_text[:4096].lower()
    return any(m in sample for m in _YP_BLOCK_MARKERS)


# ─────────────────────────────────────────────────────────
# Industry normalisation — BUG-YP-06 / NEW-YP-A
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
    BUG-YP-06: Map raw search keyword to canonical industry value.
    Prevents free-text search strings corrupting the industry column.
    """
    if not keyword:
        return ""
    key = keyword.strip().lower()
    if key in _INDUSTRY_MAP:
        return _INDUSTRY_MAP[key]
    for term, canonical in _INDUSTRY_MAP.items():
        if term in key:
            return canonical
    return " ".join(keyword.strip().split()).title()


# ─────────────────────────────────────────────────────────
# URL builder
# ─────────────────────────────────────────────────────────

def get_yp_url(
    keyword:  str,
    location: str,
    country:  str,
    page:     int = 1,
) -> str:
    """
    Build a YellowPages.com (US) or Yell.com (UK) search URL.
    """
    country_lower = country.lower() if country else ""
    keyword_enc   = keyword.strip().replace(" ", "+")
    location_enc  = location.strip().replace(" ", "+")

    if "united kingdom" in country_lower:
        base = (
            f"https://www.yell.com/ucs/UcsSearchAction.do"
            f"?keywords={keyword_enc}"
            f"&location={location_enc}"
        )
        if page > 1:
            base += f"&pageNum={page}"
        return base

    else:
        parts     = [p.strip() for p in location.split(",")]
        city_raw  = parts[0] if parts else location
        state_raw = parts[1].strip() if len(parts) >= 2 else ""

        kw_slug   = keyword.strip().lower().replace(" ", "-")
        city_slug = city_raw.lower().replace(" ", "-")

        if state_raw and state_raw.lower() not in (
            "united states", "us", "usa"
        ):
            state_slug = state_raw.lower().replace(" ", "-")
            loc_slug   = f"{city_slug}-{state_slug}"
        else:
            loc_slug = city_slug

        if page == 1:
            return (
                f"https://www.yellowpages.com"
                f"/{loc_slug}/{kw_slug}"
            )
        return (
            f"https://www.yellowpages.com"
            f"/{loc_slug}/{kw_slug}?page={page}"
        )


# ─────────────────────────────────────────────────────────
# v9.7 lead dict factory
# BUG-YP-06: normalise_industry() + postcode field added
# ─────────────────────────────────────────────────────────

def _empty_lead(
    keyword: str,
    city:    str,
    country: str,
) -> dict:
    """
    Return a v9.7-compatible lead dict with all required fields.

    BUG-YP-06: industry is normalised — raw keyword never stored.
               postcode field added — populated by Strategy 1
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
        "postcode":                     "",   # BUG-YP-06/12

        # Location
        "city":                         city,
        "country":                      country,
        "market":                       "",

        # Classification
        # BUG-YP-06: normalised — not raw keyword
        "industry":                     normalise_industry(keyword),
        "source":                       "yellowpages",

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
# YP URL / redirect helpers
# ─────────────────────────────────────────────────────────

_YP_PLATFORM_DOMAINS = (
    "yellowpages.com",
    "yellowpages.ca",
    "yell.com",
    "yell.co.uk",
)

_YP_REDIRECT_PATTERNS = (
    "/ycb/?",
    "yellowpages.com/ycb",
    "yp.com/ycb",
)


def _is_yp_url(url: str) -> bool:
    url_lower = url.lower()
    return any(d in url_lower for d in _YP_PLATFORM_DOMAINS)


def _is_yp_redirect(url: str) -> bool:
    return any(p in url for p in _YP_REDIRECT_PATTERNS)


# ─────────────────────────────────────────────────────────
# Lead extractor
# BUG-YP-02: YPBlockedError raised before parsing
# BUG-YP-12: postalCode extracted to postcode field
# ─────────────────────────────────────────────────────────

def extract_yp_leads(
    html:     str,
    keyword:  str,
    location: str,
    country:  str,
) -> List[dict]:
    """
    Extract leads from YellowPages/Yell HTML.

    BUG-YP-02: raises YPBlockedError if a challenge page is
               detected — distinct from returning [].

    4 strategies run in order; first with results wins.
    All returned lead dicts use v9.7 schema field names.
    """
    # BUG-YP-02: block check BEFORE any parsing
    if is_yp_blocked(html):
        raise YPBlockedError(
            "YellowPages/Yell returned a challenge/CAPTCHA page — "
            "not a genuine empty result set."
        )

    leads = []
    soup  = BeautifulSoup(html, "lxml")

    parts         = [p.strip() for p in location.split(",")]
    city          = parts[0] if parts else location
    country_lower = country.lower() if country else ""
    seen          = set()

    # ── Strategy 1: JSON-LD (all regions) ────────────────
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
                if not name or len(name) < 3 or name in seen:
                    continue
                itype = item.get("@type", "")
                if not any(
                    t in itype for t in (
                        "LocalBusiness", "Organization",
                        "Store", "Restaurant", "Service",
                    )
                ):
                    continue

                seen.add(name)

                website    = item.get("url", "")
                yp_biz_url = ""
                if _is_yp_url(website) or _is_yp_redirect(website):
                    yp_biz_url = website
                    website    = ""

                # BUG-YP-12: postalCode extracted separately
                address  = ""
                postcode = ""
                addr     = item.get("address", {})
                if isinstance(addr, dict):
                    postcode = addr.get("postalCode", "")
                    address  = ", ".join(filter(None, [
                        addr.get("streetAddress",   ""),
                        addr.get("addressLocality", ""),
                        postcode,
                    ]))

                notes = ""
                agg   = item.get("aggregateRating", {})
                if isinstance(agg, dict):
                    try:
                        rating  = float(agg.get("ratingValue", 0))
                        reviews = int(agg.get("reviewCount",  0))
                        if rating or reviews or yp_biz_url:
                            notes = json.dumps({
                                "yp_rating":  rating,
                                "yp_reviews": reviews,
                                "yp_url":     yp_biz_url,
                            })
                    except Exception:
                        pass
                if not notes and yp_biz_url:
                    notes = json.dumps({"yp_url": yp_biz_url})

                lead = _empty_lead(keyword, city, country)
                lead.update({
                    "business_name":    name,
                    "phone":            item.get("telephone", ""),
                    "website":          website,
                    "address":          address,
                    "postcode":         postcode,   # BUG-YP-12
                    "enrichment_notes": notes,
                })
                leads.append(lead)

        except Exception:
            continue

    if leads:
        return leads

    # ── Strategy 2: Yell.com HTML (UK only) ──────────────
    if "united kingdom" in country_lower:
        containers = soup.find_all(
            ["article", "li", "div"],
            {"class": re.compile(
                r"businessCapsule|listing|result", re.I
            )},
        )
        for container in containers:
            try:
                name_el = container.find(["h2", "h3", "a"])
                if not name_el:
                    continue
                name = re.sub(
                    r"^\d+\.\s*",
                    "",
                    name_el.get_text(strip=True),
                ).strip()
                if not name or len(name) < 3 or name in seen:
                    continue
                seen.add(name)

                phone  = ""
                tel_el = container.find(
                    "a", href=re.compile(r"tel:")
                )
                if tel_el:
                    phone = (
                        tel_el["href"].replace("tel:", "").strip()
                    )

                website = ""
                web_el  = container.find(
                    "a",
                    {"class": re.compile(
                        r"website|web|url", re.I
                    )},
                )
                if web_el:
                    href = web_el.get("href", "")
                    if (
                        href.startswith("http")
                        and not _is_yp_url(href)
                    ):
                        website = href

                address = ""
                addr_el = container.find(
                    ["address", "span", "div"],
                    {"class": re.compile(
                        r"address|location|street", re.I
                    )},
                )
                if addr_el:
                    address = addr_el.get_text(" ", strip=True)

                lead = _empty_lead(keyword, city, country)
                lead.update({
                    "business_name": name,
                    "phone":         phone,
                    "website":       website,
                    "address":       address,
                })
                leads.append(lead)

            except Exception:
                continue

    # ── Strategy 3: YellowPages.com HTML (US) ────────────
    else:
        cards = soup.find_all(
            "div",
            {"class": re.compile(
                r"\bresult\b|\bv-card\b|srp-listing", re.I
            )},
        )
        for card in cards:
            try:
                name_el = card.find(
                    "a",
                    {"class": re.compile(r"business-name", re.I)},
                )
                if not name_el:
                    name_el = card.find(["h2", "h3"])
                if not name_el:
                    continue

                name = re.sub(
                    r"^\d+\.\s*",
                    "",
                    name_el.get_text(strip=True),
                ).strip()
                if not name or len(name) < 3 or name in seen:
                    continue
                seen.add(name)

                phone    = ""
                phone_el = card.find(
                    ["div", "span"],
                    {"class": re.compile(r"\bphones?\b", re.I)},
                )
                if phone_el:
                    phone = phone_el.get_text(strip=True)
                if not phone:
                    tel_el = card.find(
                        "a", href=re.compile(r"tel:")
                    )
                    if tel_el:
                        phone = (
                            tel_el["href"]
                            .replace("tel:", "")
                            .strip()
                        )

                address = ""
                addr_el = card.find(
                    ["div", "span"],
                    {"class": re.compile(
                        r"\badr\b|street|locality", re.I
                    )},
                )
                if addr_el:
                    address = addr_el.get_text(" ", strip=True)

                website = ""
                web_el  = card.find(
                    "a",
                    {"class": re.compile(
                        r"track-visit-website|website", re.I
                    )},
                )
                if web_el:
                    href = web_el.get("href", "")
                    if (
                        href.startswith("http")
                        and not _is_yp_url(href)
                        and not _is_yp_redirect(href)
                    ):
                        website = href

                notes   = ""
                rate_el = card.find(
                    ["div", "span"],
                    {"class": re.compile(r"rating", re.I)},
                )
                if rate_el:
                    m = re.search(
                        r"(\d+\.?\d*)",
                        rate_el.get("aria-label", "")
                        or rate_el.get_text(),
                    )
                    if m:
                        notes = json.dumps(
                            {"yp_rating": float(m.group(1))}
                        )

                lead = _empty_lead(keyword, city, country)
                lead.update({
                    "business_name":    name,
                    "phone":            phone,
                    "website":          website,
                    "address":          address,
                    "enrichment_notes": notes,
                })
                leads.append(lead)

            except Exception:
                continue

    return leads


# ─────────────────────────────────────────────────────────
# Browser warmup
# BUG-YP-05: logger used + is_yp_blocked() after each load
# NEW-YP-B:  CAPTCHA detected in warmup — does not carry
#            into real scrape silently
# ─────────────────────────────────────────────────────────

async def _warmup_browser(pg) -> bool:
    """
    Visit a neutral site before scraping to warm up the browser
    session and appear as organic traffic.

    BUG-YP-05: logger replaces print(); is_yp_blocked() checked
               after each warmup load — CAPTCHA state cannot
               silently carry into the real scrape session.
    """
    warmup_sites = [
        "https://www.bing.com",
        "https://www.wikipedia.org",
        "https://www.bbc.com",
    ]

    for site in warmup_sites:
        try:
            logger.debug("_warmup_browser: visiting %s", site)
            await pg.goto(
                site,
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await async_random_delay(1, 2)

            # BUG-YP-05 / NEW-YP-B: check warmup page for block
            html_content = await pg.content()
            if is_yp_blocked(html_content):
                logger.warning(
                    "_warmup_browser: challenge page on %s — "
                    "trying next warmup site",
                    site,
                )
                continue

            logger.debug(
                "_warmup_browser: warmup successful on %s", site
            )
            return True

        except Exception as exc:
            logger.warning(
                "_warmup_browser: %s failed: %s", site, exc
            )
            continue

    logger.warning(
        "_warmup_browser: all warmup sites failed — "
        "continuing with unwarmed session"
    )
    return False


# ─────────────────────────────────────────────────────────
# Async scraper
# BUG-YP-03: cookie banner in asyncio.wait_for(timeout=6.0)
# BUG-YP-04: screenshots — single counter, keyword slug,
#            timestamp, full_page=False, capped at 5
# BUG-YP-07: all print() replaced with logger
# BUG-YP-13: screenshot failure logged at DEBUG not swallowed
# ─────────────────────────────────────────────────────────

async def scrape_yellowpages_async(
    keyword:     str,
    location:    str,
    max_results: int = 20,
) -> List[dict]:
    """
    Async YellowPages/Yell scraper using Playwright.
    Returns v9.7-compatible lead dicts.
    insert_lead() is handled by job_manager — not here.
    """
    from playwright.async_api import async_playwright

    parts   = [p.strip() for p in location.split(",")]
    country = parts[-1] if len(parts) > 1 else ""
    is_uk   = "united kingdom" in country.lower()

    all_leads  : list = []
    seen_names : set  = set()
    page_num   : int  = 1
    max_pages  : int  = max(1, (max_results + 9) // 10)

    # BUG-YP-04: keyword slug for unique screenshot filenames
    keyword_slug = re.sub(
        r"[^a-z0-9]+", "_", keyword.lower()
    ).strip("_")
    # NEW-YP-C: unified screenshot counter — both empty-page
    # and error-handler screenshots share this single cap
    screenshot_count: int = 0

    source_name = "Yell.com" if is_uk else "YellowPages.com"
    logger.info(
        "scrape_yellowpages_async: %s keyword=%r location=%r max=%d",
        source_name, keyword, location, max_results,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS_MODE,
            args=get_stealth_args(),
        )
        context = await build_yp_context(browser, is_uk=is_uk)
        pg      = await context.new_page()
        await apply_stealth(pg)

        try:
            # BUG-YP-05: warmup with block detection
            await _warmup_browser(pg)

            while (
                len(all_leads) < max_results
                and page_num <= max_pages
            ):
                url = get_yp_url(
                    keyword, location, country, page_num
                )
                logger.info(
                    "scrape_yellowpages_async: page %d → %s",
                    page_num, url,
                )

                try:
                    await pg.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=120_000,
                    )
                    await async_random_delay(3, 5)
                    await human_mouse_move(pg)
                    await async_random_delay(1, 2)

                    try:
                        await pg.wait_for_selector(
                            ".result, .v-card, "
                            ".srp-listing, "
                            ".businessCapsule, "
                            "article, h2",
                            timeout=20_000,
                        )
                    except Exception:
                        logger.debug(
                            "scrape_yellowpages_async: "
                            "selector timeout page %d — "
                            "trying anyway",
                            page_num,
                        )

                    # BUG-YP-03: cookie banner wrapped in
                    # asyncio.wait_for(timeout=6.0)
                    async def _handle_cookie_banner() -> None:
                        for btn_text in (
                            "Accept All", "Accept",
                            "I Agree", "OK",
                            "Accept Cookies",
                        ):
                            try:
                                btn = pg.get_by_role(
                                    "button", name=btn_text
                                )
                                # Safe for both Playwright versions
                                try:
                                    count = btn.count()
                                except TypeError:
                                    count = await btn.count()
                                if count > 0:
                                    await btn.first.click(
                                        timeout=3_000
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
                            "scrape_yellowpages_async: "
                            "cookie banner timed out — continuing"
                        )

                    await human_scroll(pg, scroll_times=3)
                    await async_random_delay(1, 2)

                    html_content = await pg.content()

                    # BUG-YP-02: catch block detection
                    try:
                        leads = extract_yp_leads(
                            html_content, keyword,
                            location, country,
                        )
                    except YPBlockedError:
                        logger.warning(
                            "scrape_yellowpages_async: page %d — "
                            "block/CAPTCHA detected",
                            page_num,
                        )
                        leads = []

                    if not leads:
                        # BUG-YP-04 / NEW-YP-C: capped, named,
                        # full_page=False
                        if screenshot_count < MAX_DEBUG_SCREENSHOTS:
                            debug_dir = Path("data/debug")
                            debug_dir.mkdir(
                                parents=True, exist_ok=True
                            )
                            ts = datetime.utcnow().strftime(
                                "%Y%m%d_%H%M%S"
                            )
                            shot_path = (
                                debug_dir
                                / f"yp_{keyword_slug}"
                                  f"_p{page_num}_{ts}.png"
                            )
                            try:
                                await pg.screenshot(
                                    path=str(shot_path),
                                    full_page=False,  # BUG-YP-04
                                )
                                screenshot_count += 1
                                logger.debug(
                                    "scrape_yellowpages_async: "
                                    "screenshot %d/%d → %s",
                                    screenshot_count,
                                    MAX_DEBUG_SCREENSHOTS,
                                    shot_path,
                                )
                            except Exception as ss_err:
                                # BUG-YP-13: log — not swallow
                                logger.debug(
                                    "scrape_yellowpages_async: "
                                    "screenshot failed: %s",
                                    ss_err,
                                )

                        logger.warning(
                            "scrape_yellowpages_async: "
                            "no leads on page %d — stopping",
                            page_num,
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
                                "scrape_yellowpages_async: + %s | %s",
                                name,
                                lead.get("phone", "—"),
                            )
                        if len(all_leads) >= max_results:
                            break

                    logger.info(
                        "scrape_yellowpages_async: page %d — "
                        "%d new | total %d",
                        page_num, new_count, len(all_leads),
                    )

                    if len(all_leads) >= max_results:
                        break

                    page_num += 1
                    await async_random_delay(4, 7)

                except YPBlockedError:
                    logger.warning(
                        "scrape_yellowpages_async: blocked on "
                        "page %d — stopping",
                        page_num,
                    )
                    break

                except Exception as page_err:
                    logger.error(
                        "scrape_yellowpages_async: page %d "
                        "error: %s",
                        page_num, page_err,
                    )

                    # BUG-YP-04 / NEW-YP-C: error screenshot —
                    # same cap, same naming, full_page=False
                    if screenshot_count < MAX_DEBUG_SCREENSHOTS:
                        try:
                            debug_dir = Path("data/debug")
                            debug_dir.mkdir(
                                parents=True, exist_ok=True
                            )
                            ts = datetime.utcnow().strftime(
                                "%Y%m%d_%H%M%S"
                            )
                            shot_path = (
                                debug_dir
                                / f"yp_{keyword_slug}"
                                  f"_err_p{page_num}_{ts}.png"
                            )
                            await pg.screenshot(
                                path=str(shot_path),
                                full_page=False,
                            )
                            screenshot_count += 1
                            logger.debug(
                                "scrape_yellowpages_async: "
                                "error screenshot → %s",
                                shot_path,
                            )
                        except Exception as ss_err:
                            # BUG-YP-13: log — not swallow
                            logger.debug(
                                "scrape_yellowpages_async: "
                                "error screenshot failed: %s",
                                ss_err,
                            )
                    break

        finally:
            try:
                await browser.close()
            except Exception:
                pass

    logger.info(
        "scrape_yellowpages_async: complete — %d leads",
        len(all_leads),
    )
    return all_leads[:max_results]


# ─────────────────────────────────────────────────────────
# Sync wrapper
# BUG-YP-01: asyncio.set_event_loop() removed entirely
# ─────────────────────────────────────────────────────────

def scrape_yellowpages(
    keyword:     str,
    location:    str,
    max_results: int = 20,
) -> List[dict]:
    """
    Synchronous entry point for the YellowPages/Yell scraper.
    Called from job_manager.py in a background thread.

    BUG-YP-01: set_event_loop() removed — no global loop pollution.
    Creates a fresh event loop per call without registering it
    globally. RuntimeError if called from a running loop is caught
    with a clear diagnostic message.
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    # BUG-YP-01: do NOT call asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            scrape_yellowpages_async(
                keyword, location, max_results
            )
        )
    except RuntimeError as exc:
        if "already running" in str(exc).lower():
            logger.error(
                "scrape_yellowpages: cannot run — an event loop "
                "is already running in this thread. Use "
                "scrape_yellowpages_async() directly from async "
                "context: %s", exc,
            )
            return []
        raise
    finally:
        try:
            loop.close()
        except Exception:
            pass