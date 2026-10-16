# engine/alignable_scraper.py
# FULL REPLACEMENT — Converted to Playwright to fix HTTP 403 blocking

import re
import json
import asyncio
from bs4 import BeautifulSoup

from engine.anti_detection import (
    get_random_user_agent,
    get_stealth_args,
    apply_stealth,
    async_random_delay,
)


# ─────────────────────────────────────────────────────────
# URL Builder
# ─────────────────────────────────────────────────────────

def build_alignable_url(
    keyword: str,
    location: str,
    page: int = 1,
) -> str:
    """Build Alignable search URL."""
    keyword_slug  = keyword.strip().replace(" ", "+")
    location_slug = location.strip().replace(" ", "+")

    if page == 1:
        return (
            f"https://www.alignable.com/find-a-business"
            f"?category={keyword_slug}"
            f"&location={location_slug}"
        )
    return (
        f"https://www.alignable.com/find-a-business"
        f"?category={keyword_slug}"
        f"&location={location_slug}"
        f"&page={page}"
    )


# ─────────────────────────────────────────────────────────
# HTML Parser
# ─────────────────────────────────────────────────────────

def extract_alignable_leads(
    html: str,
    keyword: str,
    location: str,
) -> list:
    """
    Extract business leads from Alignable HTML.
    Uses multiple strategies to find business cards.
    """
    leads = []
    soup  = BeautifulSoup(html, "lxml")

    parts   = [p.strip() for p in location.split(",")]
    city    = parts[0] if parts else location
    country = parts[-1] if len(parts) > 1 else ""

    seen = set()

    # ── Strategy 1: JSON-LD structured data ───────────────
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
                if not name or len(name) < 3:
                    continue
                if name in seen:
                    continue

                itype = item.get("@type", "")
                if not any(
                    t in itype for t in [
                        "LocalBusiness", "Organization",
                        "Store", "Restaurant", "Service",
                    ]
                ):
                    continue

                seen.add(name)

                phone   = item.get("telephone", "")
                email   = item.get("email", "")
                website = item.get("url", "")

                # Clean alignable internal URLs
                if website and "alignable.com" in website:
                    website = ""

                address = ""
                addr    = item.get("address", {})
                if isinstance(addr, dict):
                    addr_parts = [
                        addr.get("streetAddress", ""),
                        addr.get("addressLocality", ""),
                        addr.get("postalCode", ""),
                    ]
                    address = ", ".join(
                        p for p in addr_parts if p
                    )

                leads.append({
                    "business_name": name,
                    "phone":         phone,
                    "email":         email,
                    "website":       website,
                    "address":       address,
                    "rating":        0.0,
                    "reviews":       0,
                    "source":        "alignable",
                    "source_url":    "https://www.alignable.com",
                    "industry":      keyword,
                    "city":          city,
                    "location":      location,
                    "country":       country,
                    "has_website":   1 if website else 0,
                    "has_instagram": 0,
                    "has_facebook":  0,
                })

        except Exception:
            continue

    # ── Strategy 2: Business card divs ────────────────────
    card_selectors = [
        {"class": re.compile(
            r"business-card|member-card|"
            r"profile-card|business_card|"
            r"BusinessCard|MemberCard"
        )},
    ]

    cards = []
    for selector in card_selectors:
        found = soup.find_all("div", selector)
        if found:
            cards = found
            break

    if not cards:
        cards = soup.find_all(
            "div",
            {"class": re.compile(r"col-md-4|col-sm-6|col-lg-4")},
        )

    for card in cards:
        try:
            name_el = card.find(["h2", "h3", "h4"])
            if not name_el:
                name_el = card.find(
                    "a",
                    {"class": re.compile(r"name|title|business")},
                )
            if not name_el:
                continue

            name = name_el.get_text(strip=True)
            if not name or len(name) < 3:
                continue
            if name in seen:
                continue

            # Skip navigation / UI elements
            skip_words = [
                "sign", "log", "join", "find", "search",
                "home", "about", "help", "menu", "contact us",
                "get started", "learn more", "click here",
            ]
            if any(w in name.lower() for w in skip_words):
                continue

            seen.add(name)

            # Phone
            phone = ""
            tel_link = card.find("a", href=re.compile(r"tel:"))
            if tel_link:
                phone = tel_link.get(
                    "href", ""
                ).replace("tel:", "").strip()

            if not phone:
                phone_el = card.find(
                    "span",
                    {"class": re.compile(r"phone|tel|number")},
                )
                if phone_el:
                    phone = phone_el.get_text(strip=True)

            # Email
            email = ""
            mail_link = card.find(
                "a", href=re.compile(r"mailto:")
            )
            if mail_link:
                email = mail_link.get(
                    "href", ""
                ).replace("mailto:", "").strip()

            # Website
            website = ""
            web_el = card.find(
                "a",
                {"class": re.compile(r"website|web|url")},
            )
            if web_el:
                website = web_el.get("href", "")
                if website and "alignable.com" in website:
                    website = ""

            # Address
            address = ""
            addr_el = card.find(
                ["address", "span", "div"],
                {"class": re.compile(r"address|location|city")},
            )
            if addr_el:
                address = addr_el.get_text(strip=True)

            leads.append({
                "business_name": name,
                "phone":         phone,
                "email":         email,
                "website":       website,
                "address":       address,
                "rating":        0.0,
                "reviews":       0,
                "source":        "alignable",
                "source_url":    "https://www.alignable.com",
                "industry":      keyword,
                "city":          city,
                "location":      location,
                "country":       country,
                "has_website":   1 if website else 0,
                "has_instagram": 0,
                "has_facebook":  0,
            })

        except Exception:
            continue

    # ── Strategy 3: Profile links fallback ────────────────
    if not leads:
        profile_links = soup.find_all(
            "a",
            href=re.compile(r"/[a-z]+-[a-z]+/[a-z]"),
        )

        for link in profile_links:
            try:
                name = link.get_text(strip=True)
                if not name or len(name) < 3:
                    continue
                if name in seen:
                    continue

                skip_words = [
                    "sign", "log", "join", "find",
                    "search", "home", "about", "help",
                    "privacy", "terms", "cookie",
                ]
                if any(w in name.lower() for w in skip_words):
                    continue

                # Only take names that look like businesses
                if len(name.split()) < 2:
                    continue

                seen.add(name)

                href = link.get("href", "")
                full_url = (
                    "https://www.alignable.com" + href
                    if href.startswith("/")
                    else href
                )

                leads.append({
                    "business_name": name,
                    "phone":         "",
                    "email":         "",
                    "website":       "",
                    "address":       "",
                    "rating":        0.0,
                    "reviews":       0,
                    "source":        "alignable",
                    "source_url":    full_url,
                    "industry":      keyword,
                    "city":          city,
                    "location":      location,
                    "country":       country,
                    "has_website":   0,
                    "has_instagram": 0,
                    "has_facebook":  0,
                })

            except Exception:
                continue

    return leads


# ─────────────────────────────────────────────────────────
# Async Playwright Scraper
# ─────────────────────────────────────────────────────────

async def scrape_alignable_async(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> list:
    """
    Async Alignable scraper using Playwright.
    Bypasses 403 blocking by using real browser.
    Best for US small businesses.
    Owners often list email + phone directly.
    """
    from playwright.async_api import async_playwright
    from config import HEADLESS_MODE

    parts   = [p.strip() for p in location.split(",")]
    country = parts[-1] if len(parts) > 1 else ""

    all_leads  = []
    seen_names = set()
    page_num   = 1
    max_pages  = max(1, max_results // 10)

    print(f"🔍 Alignable scraping: {keyword} in {location}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS_MODE,
            args=get_stealth_args(),
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=get_random_user_agent(),
            locale="en-US",
            timezone_id="America/Chicago",
            is_mobile=False,
            has_touch=False,
            device_scale_factor=1,
        )

        page = await context.new_page()
        await apply_stealth(page)

        try:
            while (
                len(all_leads) < max_results
                and page_num <= max_pages
            ):
                url = build_alignable_url(
                    keyword, location, page_num
                )
                print(f"   📄 Page {page_num}: {url}")

                try:
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=90000,
                    )
                    await async_random_delay(4, 7)

                    # Handle login prompts or popups
                    for btn_text in [
                        "Close", "No thanks", "Skip",
                        "Not now", "Maybe later",
                        "Continue browsing",
                    ]:
                        try:
                            btn = page.get_by_role(
                                "button", name=btn_text
                            )
                            if await btn.count() > 0:
                                await btn.first.click(
                                    timeout=2000
                                )
                                await async_random_delay(1, 2)
                                break
                        except Exception:
                            pass

                    # Also try closing modal overlays
                    try:
                        close_btn = await page.query_selector(
                            "[aria-label='Close'], "
                            ".modal-close, "
                            ".close-button, "
                            "[data-dismiss='modal']"
                        )
                        if close_btn:
                            await close_btn.click()
                            await async_random_delay(1, 2)
                    except Exception:
                        pass

                    await async_random_delay(2, 3)

                    # Check if we hit a login wall
                    page_text = await page.inner_text("body")
                    page_lower = page_text.lower()

                    login_signals = [
                        "sign in to view",
                        "create a free account",
                        "join alignable",
                        "log in to see",
                        "members only",
                    ]

                    hit_login_wall = any(
                        s in page_lower
                        for s in login_signals
                    )

                    if hit_login_wall:
                        print(
                            "   ⚠️ Alignable login wall detected"
                            " — extracting visible data only"
                        )

                    # Get HTML and extract
                    html = await page.content()
                    leads = extract_alignable_leads(
                        html, keyword, location
                    )

                    if not leads:
                        print(
                            f"   ⚠️ No leads on page "
                            f"{page_num}"
                        )
                        if hit_login_wall:
                            print(
                                "   💡 Alignable requires login"
                                " for full data."
                                " Switching to Google Maps"
                                " for US leads instead."
                            )
                        break

                    new_count = 0
                    for lead in leads:
                        name = lead.get("business_name", "")
                        if name and name not in seen_names:
                            seen_names.add(name)
                            all_leads.append(lead)
                            new_count += 1
                            print(
                                f"   ✅ {name} | "
                                f"📞 {lead.get('phone', '—')} | "
                                f"📧 {lead.get('email', '—')}"
                            )

                        if len(all_leads) >= max_results:
                            break

                    print(
                        f"   📊 Page {page_num}: "
                        f"{new_count} new | "
                        f"Total: {len(all_leads)}"
                    )

                    if len(all_leads) >= max_results:
                        break

                    page_num += 1
                    await async_random_delay(5, 9)

                except Exception as e:
                    print(f"   ❌ Page error: {e}")
                    break

        finally:
            await browser.close()

    print(f"✅ Alignable done: {len(all_leads)} leads")
    return all_leads[:max_results]


# ─────────────────────────────────────────────────────────
# Sync Wrapper
# ─────────────────────────────────────────────────────────

def scrape_alignable(
    keyword: str,
    location: str,
    max_results: int = 20,
) -> list:
    """
    Sync wrapper for Alignable scraper.
    Called from job_manager background thread.
    Uses Playwright to bypass 403 blocking.
    Best for US small businesses.
    """
    return asyncio.run(
        scrape_alignable_async(keyword, location, max_results)
    )


# ─────────────────────────────────────────────────────────
# Kept for backwards compatibility
# ─────────────────────────────────────────────────────────

def build_alignable_url_alias(
    keyword: str,
    location: str,
    page: int = 1,
) -> str:
    return build_alignable_url(keyword, location, page)


def extract_alignable_leads_alias(
    html: str,
    keyword: str,
    location: str,
) -> list:
    return extract_alignable_leads(html, keyword, location)