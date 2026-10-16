"""
KNCS LeadGen Pro — engine/scraper.py — v10.2

Fixes applied (v10.2):
  FIX-SC-01 (HIGH) WindowsSelectorEventLoopPolicy set at module level
            conflicted with scrape_sync() which creates a
            ProactorEventLoop. Policy override moved inside
            scrape_sync() only — not applied globally.

  FIX-SC-02 (MEDIUM) is_already_in_db(): conn.close() was inside try
            block — connection leaked on any exception.
            Replaced with try/finally pattern.

  FIX-SC-03 (MEDIUM) get_existing_names_bulk(): same conn leak as above.
            Replaced with try/finally. Also added conn.close() guard.

  FIX-SC-04 (MEDIUM) is_already_in_db(): dedup on business_name only —
            same name in two cities would false-positive. Added city
            parameter and composite (business_name, city) check.
            Caller updated to pass city.

  FIX-SC-05 (LOW) All print() calls replaced with logger calls so
            output is visible to log aggregators (Streamlit, file
            handlers) not just the terminal.

  FIX-SC-06 (LOW) nest_asyncio.apply() at module level is correct and
            intentional — retained. Applied once at import, idempotent.

  FIX-SC-07 (MEDIUM) scrape_sync(): WindowsSelectorEventLoopPolicy
            removed from module level (see FIX-SC-01). scrape_sync()
            already uses ProactorEventLoop on Windows which supersedes
            any policy — policy line was redundant AND conflicting.

  RETAINED: nest_asyncio — required for Streamlit compatibility.
  RETAINED: ProactorEventLoop in scrape_sync() — required for
            Playwright subprocess support on Windows/Python 3.12.
"""

import asyncio
import logging
import re
import sys
from urllib.parse import urljoin

import nest_asyncio
nest_asyncio.apply()

# FIX-SC-01 / FIX-SC-07: WindowsSelectorEventLoopPolicy REMOVED from
# module level. scrape_sync() uses ProactorEventLoop directly which
# is the correct loop for Playwright on Windows. Setting Selector
# policy here then creating a Proactor loop in scrape_sync() caused
# a policy/loop mismatch that surfaced as intermittent failures.

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from engine.anti_detection import (
    async_random_delay,
    random_viewport,
    get_random_user_agent,
    get_stealth_args,
    apply_stealth,
)
from config import HEADLESS_MODE, MAX_RESULTS
from database import get_connection

logger = logging.getLogger(__name__)

BASE_URL = "https://www.google.com"

PAGE_TIMEOUT     = 120_000   # 120s — MTN 4G friendly
DETAIL_TIMEOUT   =  90_000   # 90s for detail pages
SELECTOR_TIMEOUT =  15_000   # 15s for selectors
MAX_RETRIES      = 2


# ══════════════════════════════════════════════════════════
# DEDUPLICATION
# ══════════════════════════════════════════════════════════

def is_already_in_db(business_name: str, city: str = "") -> bool:
    """
    FIX-SC-02: conn.close() moved to finally — no leak on exception.
    FIX-SC-04: composite (business_name, city) check — same name in
               two cities no longer produces a false positive.
    """
    if not business_name or len(business_name.strip()) < 3:
        return False

    conn = None
    try:
        conn = get_connection()
        if not conn:
            return False
        cursor = conn.cursor()

        if city:
            cursor.execute(
                """
                SELECT id FROM leads
                WHERE LOWER(TRIM(business_name)) = LOWER(TRIM(?))
                AND   LOWER(TRIM(city))          = LOWER(TRIM(?))
                LIMIT 1
                """,
                (business_name.strip(), city.strip()),
            )
        else:
            # No city provided — name-only fallback (less precise)
            cursor.execute(
                """
                SELECT id FROM leads
                WHERE LOWER(TRIM(business_name)) = LOWER(TRIM(?))
                LIMIT 1
                """,
                (business_name.strip(),),
            )

        return cursor.fetchone() is not None

    except Exception as exc:
        # FIX-SC-05: logger replaces print()
        logger.warning("is_already_in_db check error: %s", exc)
        return False   # Allow scraping if check fails

    finally:
        # FIX-SC-02: guaranteed close
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_existing_names_bulk(keyword: str, city: str) -> set:
    """
    FIX-SC-03: conn.close() moved to finally — no leak on exception.
    FIX-SC-05: logger replaces print().
    """
    existing = set()
    conn = None
    try:
        conn = get_connection()
        if not conn:
            return existing
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT LOWER(TRIM(business_name))
            FROM   leads
            WHERE  LOWER(industry) = LOWER(?)
            AND    LOWER(city)     = LOWER(?)
            """,
            (keyword.strip(), city.strip()),
        )
        rows     = cursor.fetchall()
        existing = {row[0] for row in rows}
        if existing:
            logger.info(
                "%d existing leads found for '%s' in '%s' "
                "— duplicates will be skipped",
                len(existing), keyword, city,
            )
    except Exception as exc:
        # FIX-SC-05: logger replaces print()
        logger.warning("get_existing_names_bulk error: %s", exc)
    finally:
        # FIX-SC-03: guaranteed close
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return existing


# ══════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════

def clean_business_name(name: str) -> str:
    if not name:
        return ""
    name = name.strip()
    name = re.sub(
        r"\s+-\s+Google Maps.*$", "", name, flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\s+-\s+Google.*$", "", name, flags=re.IGNORECASE,
    )
    name = name.replace("Sponsored", "").strip()
    return name


def is_bad_business_name(name: str) -> bool:
    if not name:
        return True

    bad_exact = {
        "sponsored", "google maps", "google", "results",
        "loading", "directions", "reviews", "photos",
        "share", "map", "continue", "go back to web",
        "open in app", "this site can't be reached",
        "this site can t be reached",
        "before you continue to google",
        "before you continue", "page not found",
        "404", "error", "access denied", "just a moment",
        "please wait", "enable javascript",
        "you've been blocked", "attention required",
        "cloudflare", "captcha", "robot",
        "none", "null", "undefined", "n/a",
    }

    bad_phrases = [
        "before you continue", "this site can",
        "enable javascript", "just a moment",
        "access denied", "please wait",
        "not a robot", "verify you", "checking your",
        "sponsored", "privacy policy", "terms of service",
        "cookie", "404 not found", "page not found",
        "site can't be reached", "connection refused",
        "err_connection", "this page", "open the",
        "get the app", "google maps is better",
        "better on the app",
    ]

    normalized = name.strip().lower()

    if normalized in bad_exact:
        return True
    for phrase in bad_phrases:
        if phrase in normalized:
            return True
    if len(normalized) < 3:
        return True
    if normalized.startswith("http"):
        return True
    return False


def extract_phone_from_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(\+44[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})",
        r"(0[1-9]\d{1,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})",
        r"(\+234[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4})",
        r"(0[789][01]\d[\s\-]?\d{3}[\s\-]?\d{4})",
        r"(0\d{10})",
        r"(\+?[1-9]\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4})",
        r"(\+?[\d\s\-\(\)]{10,18})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            phone  = match.group(1).strip()
            digits = re.sub(r"\D", "", phone)
            if 9 <= len(digits) <= 15:
                return phone
    return ""


def normalize_phone_for_whatsapp(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if not digits:
        return ""
    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    if digits.startswith("234") and len(digits) >= 12:
        return digits
    if 10 <= len(digits) <= 15:
        return digits
    return ""


def build_whatsapp_url(phone: str) -> str:
    normalized = normalize_phone_for_whatsapp(phone)
    if not normalized:
        return ""
    return f"https://wa.me/{normalized}"


# ══════════════════════════════════════════════════════════
# POPUP HANDLERS
# ══════════════════════════════════════════════════════════

async def handle_google_consent(page):
    try:
        current_url = (page.url or "").lower()
        if (
            "consent.google.com" not in current_url
            and "consent" not in current_url
        ):
            return

        # FIX-SC-05: logger replaces print()
        logger.info("Consent page detected — attempting dismissal")

        for text in [
            "Accept all", "I agree", "Accept",
            "Reject all", "Reject", "Continue", "Agree",
        ]:
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() > 0:
                    await btn.first.click(timeout=4000)
                    logger.debug("Consent dismissed via: %s", text)
                    await async_random_delay(2, 4)
                    return
            except Exception:
                pass

        try:
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                label = (await btn.inner_text()).strip().lower()
                if any(
                    k in label
                    for k in ["accept", "agree", "reject", "continue"]
                ):
                    await btn.click()
                    await async_random_delay(2, 4)
                    return
        except Exception:
            pass

    except Exception as exc:
        logger.warning("Consent handler error: %s", exc)


async def dismiss_google_maps_app_prompt(page):
    try:
        for text in [
            "Continue", "Go back to web", "Not now",
            "Use web version", "Open in browser", "Skip",
        ]:
            try:
                btn = page.get_by_role(
                    "button", name=re.compile(text, re.I),
                )
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await async_random_delay(1, 2)
                    return
            except Exception:
                pass
    except Exception as exc:
        logger.warning("App prompt dismissal error: %s", exc)


async def handle_all_popups(page):
    await handle_google_consent(page)
    await dismiss_google_maps_app_prompt(page)


# ══════════════════════════════════════════════════════════
# SAFE NAVIGATION WITH RETRY
# ══════════════════════════════════════════════════════════

async def safe_goto(
    page, url: str, retries: int = MAX_RETRIES,
) -> bool:
    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                logger.debug("Navigation retry %d/%d", attempt, retries)
                await async_random_delay(3, 6)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )
            return True
        except Exception as exc:
            error_msg = str(exc).lower()
            if "timeout" in error_msg:
                logger.debug(
                    "Timeout on attempt %d — %s",
                    attempt + 1,
                    "retrying" if attempt < retries else "giving up",
                )
                continue
            if any(
                x in error_msg
                for x in ["net::", "connection", "refused"]
            ):
                logger.debug(
                    "Connection error attempt %d — %s",
                    attempt + 1,
                    "retrying" if attempt < retries else "giving up",
                )
                continue
            logger.warning("Navigation error: %s", exc)
            return False
    return False


async def safe_goto_detail(
    page, url: str, retries: int = MAX_RETRIES,
) -> bool:
    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                logger.debug(
                    "Detail retry %d/%d", attempt, retries,
                )
                await async_random_delay(2, 4)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=DETAIL_TIMEOUT,
            )
            return True
        except Exception as exc:
            error_msg = str(exc).lower()
            if "timeout" in error_msg or "net::" in error_msg:
                logger.debug(
                    "Detail timeout attempt %d", attempt + 1,
                )
                continue
            logger.warning("Detail navigation error: %s", exc)
            return False
    return False


# ══════════════════════════════════════════════════════════
# MAIN ASYNC SCRAPER
# ══════════════════════════════════════════════════════════

async def scrape_google_maps(
    keyword: str,
    location: str,
    max_results: int = MAX_RESULTS,
) -> list:
    """
    Async Google Maps scraper.
    Do NOT call this directly from a sync thread —
    use scrape_sync() which manages the event loop correctly.
    """
    parts   = [p.strip() for p in location.split(",")]
    city    = parts[0] if parts else location
    country = parts[-1] if len(parts) > 1 else ""

    search_query = f"{keyword} {location}".strip()
    search_url   = (
        f"{BASE_URL}/maps/search/"
        f"{search_query.replace(' ', '+')}"
    )

    # Pre-load existing names for this keyword+city
    existing_names = get_existing_names_bulk(keyword, city)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS_MODE,
            args=get_stealth_args(),
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=get_random_user_agent(),
            locale="en-GB",
            timezone_id="Europe/London",
            is_mobile=False,
            has_touch=False,
            device_scale_factor=1,
        )
        page  = await context.new_page()
        await apply_stealth(page)
        leads = []

        try:
            logger.info("Google Maps scrape: %s", search_url)

            success = await safe_goto(page, search_url)
            if not success:
                logger.error(
                    "Failed to load Google Maps after retries",
                )
                return []

            await async_random_delay(3, 6)
            await handle_all_popups(page)
            await async_random_delay(2, 4)

            raw_leads = await extract_all_listings(
                page, browser, max_results, existing_names,
            )

            for lead in raw_leads:
                lead["source"]   = "google_maps"
                lead["industry"] = keyword
                if not lead.get("city"):
                    lead["city"] = city
                if not lead.get("location"):
                    lead["location"] = location
                if not lead.get("country"):
                    lead["country"] = country

            leads = raw_leads

        except Exception as exc:
            logger.error(
                "Google Maps scraping error: %s", exc, exc_info=True,
            )
        finally:
            await browser.close()

    logger.info("Google Maps done: %d leads", len(leads))
    return leads


# ══════════════════════════════════════════════════════════
# LISTING COLLECTOR
# ══════════════════════════════════════════════════════════

async def extract_all_listings(
    page,
    browser,
    max_results: int,
    existing_names: set = None,
) -> list:

    if existing_names is None:
        existing_names = set()

    all_leads  = []
    seen_names = set()
    urls       = []
    seen_urls  = set()

    await async_random_delay(2, 4)

    scroll_attempts     = 0
    max_scroll_attempts = 15

    while (
        len(urls) < max_results * 2
        and scroll_attempts < max_scroll_attempts
    ):
        try:
            for selector in [
                "a.hfpxzc",
                "a[href*='/maps/place/']",
            ]:
                locator = page.locator(selector)
                count   = await locator.count()
                for i in range(count):
                    try:
                        el   = locator.nth(i)
                        href = await el.get_attribute("href")
                        if not href:
                            continue
                        full_url = urljoin(BASE_URL, href)
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            urls.append(full_url)
                        if len(urls) >= max_results * 2:
                            break
                    except Exception:
                        continue
                if len(urls) >= max_results * 2:
                    break

            logger.debug("%d URLs collected so far", len(urls))

            try:
                await page.evaluate("""
                    const feed = document.querySelector(
                        'div[role="feed"]'
                    );
                    if (feed) feed.scrollTop += 1500;
                    else window.scrollBy(0, 1500);
                """)
            except Exception:
                pass

            await async_random_delay(2, 4)
            scroll_attempts += 1

        except Exception as exc:
            logger.debug("Scroll error: %s", exc)
            scroll_attempts += 1

    if not urls:
        logger.warning("No URLs found — trying HTML fallback")
        fallback = await extract_via_html(page)
        for lead in fallback:
            name = lead.get("business_name", "")
            if not name or is_bad_business_name(name):
                continue
            name_lower = name.strip().lower()
            if name_lower in existing_names:
                logger.debug("Already in DB (fallback): %s", name)
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            lead["whatsapp_url"] = build_whatsapp_url(
                lead.get("phone", "")
            )
            all_leads.append(lead)
        return all_leads[:max_results]

    detail_page = await browser.new_page(
        viewport={"width": 1440, "height": 900},
        user_agent=get_random_user_agent(),
        locale="en-GB",
    )
    await apply_stealth(detail_page)

    try:
        for url in urls:
            if len(all_leads) >= max_results:
                break
            try:
                # Fast pre-check from URL
                url_name = ""
                url_match = re.search(
                    r"/maps/place/([^/@]+)", url,
                )
                if url_match:
                    url_name = (
                        url_match.group(1)
                        .replace("+", " ")
                        .replace("%20", " ")
                        .strip()
                    )
                    url_name_lower = url_name.lower()
                    if url_name_lower in existing_names:
                        logger.debug(
                            "Skip (in DB): %s", url_name,
                        )
                        continue
                    if url_name in seen_names:
                        logger.debug(
                            "Skip (seen): %s", url_name,
                        )
                        continue

                lead = await extract_from_listing_url(
                    detail_page, url,
                )
                if not lead:
                    continue

                name = clean_business_name(
                    lead.get("business_name", "")
                )
                if not name or is_bad_business_name(name):
                    logger.debug("Bad name rejected: %s", name)
                    continue

                name_lower = name.strip().lower()
                if name_lower in existing_names:
                    logger.debug("Already in DB: %s", name)
                    continue
                if name in seen_names:
                    continue

                lead["business_name"] = name
                lead["whatsapp_url"]  = build_whatsapp_url(
                    lead.get("phone", "")
                )
                lead["maps_url"] = url

                seen_names.add(name)
                existing_names.add(name_lower)
                all_leads.append(lead)

                logger.info(
                    "Captured: %s | phone=%s | rating=%s",
                    lead["business_name"],
                    lead.get("phone", "—"),
                    lead.get("rating", 0),
                )
                await async_random_delay(2, 3)

            except Exception as exc:
                logger.warning("Lead extraction error: %s", exc)
                continue
    finally:
        await detail_page.close()

    return all_leads[:max_results]


# ══════════════════════════════════════════════════════════
# DETAIL PAGE EXTRACTOR
# ══════════════════════════════════════════════════════════

async def extract_from_listing_url(page, url: str) -> dict:
    lead = {
        "business_name": "",
        "phone":         "",
        "website":       "",
        "address":       "",
        "rating":        0.0,
        "reviews":       0,
        "has_instagram": 0,
        "instagram_url": "",
        "whatsapp_url":  "",
        "maps_url":      url,
    }
    try:
        success = await safe_goto_detail(page, url)
        if not success:
            logger.debug("Skipping — could not load: %s", url)
            return lead
        await async_random_delay(2, 4)
        await handle_all_popups(page)
        await async_random_delay(1, 2)
        try:
            await page.wait_for_selector(
                "h1", timeout=SELECTOR_TIMEOUT,
            )
        except Exception:
            pass
        lead = await extract_detail_panel(page, lead)
    except Exception as exc:
        logger.warning("Listing error for %s: %s", url, exc)
    return lead


async def extract_detail_panel(page, lead: dict) -> dict:
    try:
        page_text = ""
        content   = ""
        try:
            page_text = await page.inner_text("body")
        except Exception:
            pass
        try:
            content = await page.content()
        except Exception:
            pass

        soup = (
            BeautifulSoup(content, "lxml")
            if content else None
        )

        error_signals = [
            "before you continue",
            "this site can't be reached",
            "enable javascript", "just a moment",
            "access denied", "captcha",
            "not a robot", "err_connection_refused",
            "err_name_not_resolved",
        ]
        page_text_lower = page_text.lower() if page_text else ""
        for signal in error_signals:
            if signal in page_text_lower[:500]:
                logger.debug("Error page detected: %s", signal)
                return lead

        # Business Name
        try:
            name_el = await page.query_selector("h1")
            if name_el:
                name = (await name_el.inner_text()).strip()
                if name and not is_bad_business_name(name):
                    lead["business_name"] = clean_business_name(name)
        except Exception:
            pass

        if not lead.get("business_name") and page_text:
            first_line = next(
                (
                    line.strip()
                    for line in page_text.splitlines()
                    if line.strip()
                ),
                "",
            )
            if not is_bad_business_name(first_line):
                lead["business_name"] = clean_business_name(
                    first_line,
                )

        # Rating
        try:
            rating_match = re.search(
                r"\b([1-5]\.[0-9])\b", page_text,
            )
            if rating_match:
                lead["rating"] = float(rating_match.group(1))
        except Exception:
            pass

        # Reviews
        try:
            for pattern in [
                r"([\d,]+)\s+reviews?",
                r"\(([\d,]+)\)",
            ]:
                m = re.search(pattern, page_text, re.IGNORECASE)
                if m:
                    lead["reviews"] = int(
                        m.group(1).replace(",", "")
                    )
                    break
        except Exception:
            pass

        # Phone
        try:
            tel_link = await page.query_selector("a[href^='tel:']")
            if tel_link:
                href = await tel_link.get_attribute("href")
                if href:
                    lead["phone"] = href.replace("tel:", "").strip()

            if not lead.get("phone"):
                for selector in [
                    "button[data-item-id*='phone']",
                    "a[data-item-id*='phone']",
                    "[aria-label*='phone' i]",
                    "[aria-label*='call' i]",
                ]:
                    el = await page.query_selector(selector)
                    if el:
                        txt = await el.inner_text()
                        if txt and txt.strip():
                            lead["phone"] = txt.strip()
                            break

            if not lead.get("phone"):
                lead["phone"] = extract_phone_from_text(page_text)
        except Exception:
            pass

        # Website
        try:
            for selector in [
                "a[data-item-id='authority']",
                "a[aria-label*='Website' i]",
            ]:
                web_el = await page.query_selector(selector)
                if web_el:
                    href = await web_el.get_attribute("href")
                    if href and "google" not in href.lower():
                        lead["website"] = href
                        break

            if not lead.get("website") and soup:
                for a in soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if href and href.startswith("http"):
                        low = href.lower()
                        if (
                            "google" not in low
                            and "maps" not in low
                            and "tel:" not in low
                            and "javascript" not in low
                        ):
                            lead["website"] = href
                            break
        except Exception:
            pass

        # Address
        try:
            for selector in [
                "button[data-item-id='address']",
                "[aria-label*='address' i]",
                "button[data-tooltip='Copy address']",
            ]:
                addr_el = await page.query_selector(selector)
                if addr_el:
                    addr_text = await addr_el.inner_text()
                    if addr_text and addr_text.strip():
                        lead["address"] = addr_text.strip()
                        break
        except Exception:
            pass

        # Instagram
        try:
            if page_text and "instagram" in page_text.lower():
                insta_match = re.search(
                    r"instagram\.com/([a-zA-Z0-9_.]+)",
                    page_text,
                    re.IGNORECASE,
                )
                if insta_match:
                    handle = insta_match.group(1).strip()
                    lead["has_instagram"] = 1
                    lead["instagram_url"] = (
                        f"https://instagram.com/{handle}"
                    )
                else:
                    lead["has_instagram"] = 1
        except Exception:
            pass

        lead["whatsapp_url"] = build_whatsapp_url(
            lead.get("phone", "")
        )

    except Exception as exc:
        logger.warning("Detail panel error: %s", exc)

    return lead


# ══════════════════════════════════════════════════════════
# HTML FALLBACK
# ══════════════════════════════════════════════════════════

async def extract_via_html(page) -> list:
    leads = []
    try:
        content = await page.content()
        soup    = BeautifulSoup(content, "lxml")
        candidates = soup.find_all(
            "div",
            {"class": re.compile(r"fontHeadline|qBF1Pd|NrDZNb|Nv2PK")},
        )
        for tag in candidates:
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 2:
                name = clean_business_name(
                    text.split("•")[0].strip()
                )
                if not name or is_bad_business_name(name):
                    continue
                leads.append({
                    "business_name": name,
                    "phone":         "",
                    "website":       "",
                    "address":       "",
                    "rating":        0.0,
                    "reviews":       0,
                    "has_instagram": 0,
                    "instagram_url": "",
                    "whatsapp_url":  "",
                    "maps_url":      "",
                })
    except Exception as exc:
        logger.warning("HTML fallback error: %s", exc)
    return leads


# ══════════════════════════════════════════════════════════
# SYNC WRAPPER — Public entry point for job_manager
# ══════════════════════════════════════════════════════════

def scrape_sync(
    keyword: str,
    location: str,
    max_results: int = MAX_RESULTS,
) -> list:
    """
    Synchronous entry point — safe to call from any sync thread.

    FIX-SC-07: WindowsSelectorEventLoopPolicy removed from module
    level. This function creates a ProactorEventLoop directly on
    Windows (required for Playwright subprocess support on
    Python 3.12). No global policy override needed or wanted.

    nest_asyncio is already applied at import — this loop will
    not conflict with Streamlit's event loop.
    """
    if sys.platform == "win32":
        # ProactorEventLoop required for Playwright on Windows
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            scrape_google_maps(keyword, location, max_results)
        )
    except Exception as exc:
        logger.error(
            "scrape_sync failed: %s", exc, exc_info=True,
        )
        return []
    finally:
        try:
            loop.close()
        except Exception:
            pass