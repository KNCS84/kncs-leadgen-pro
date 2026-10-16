# engine/social_osint.py
# KNCS LeadGen Pro — v10.4
# Social OSINT Engine — Google Dorking for Decision Maker Discovery
#
# Fixes vs v10.3:
#   BUG-OSI-02: ALL CAPS name handling in snippet parser
#   BUG-OSI-04: Pattern 3 group order corrected
#   BUG-OSI-07: Missing DMSource import added with fallback

from __future__ import annotations

import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# BUG-OSI-07: DMSource import with fallback
try:
    from engine.dm_validator import DMSource
except ImportError:
    class DMSource:  # type: ignore
        GOOGLE_LINKEDIN_SNIPPET = ("google_linkedin_snippet", 75)
        GOOGLE_OTHER_SNIPPET    = ("google_other_snippet",    40)

        @staticmethod
        def get_confidence(source: str) -> int:
            defaults = {
                "google_linkedin_snippet": 75,
                "google_other_snippet":    40,
            }
            return defaults.get(source, 25)

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

MIN_SEARCH_DELAY         = 8.0
MAX_SEARCH_DELAY         = 12.0
MAX_SEARCHES_PER_SESSION = 10

USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
    ),
]

CHALLENGE_MARKERS: List[str] = [
    "unusual traffic",
    "automated requests",
    "captcha",
    "robot check",
    "security check",
    "verify you are human",
    "sorry/index",
    "ipv4.google.com/sorry",
    "blocked",
]

# BUG-OSI-04: Pattern 3 corrected — name always in group 1
LINKEDIN_SNIPPET_PATTERNS = [
    # Pattern 1: "Name – Title at Company – Location"
    re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})'
        r'\s*[–\-—|]\s*'
        r'(.+?)'
        r'\s*(?:at\s+)?'
        r'(?:[–\-—|]|$)',
        re.IGNORECASE,
    ),
    # Pattern 2: "Name, Title at Company"
    re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})'
        r',\s*'
        r'(.+?)'
        r'\s+at\s+',
        re.IGNORECASE,
    ),
    # Pattern 3 FIXED: name first, title second
    re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})'
        r'(?:\s*[–\-—|,]\s*|\s+)'
        r'(.{5,60}?)'
        r'(?:\s+at\s+|\s*[–\-—|]\s*)',
        re.IGNORECASE,
    ),
]

DM_TITLE_KEYWORDS: List[str] = [
    "owner", "founder", "ceo", "managing director", "md",
    "director", "partner", "proprietor", "principal",
    "president", "chairman", "chair", "chief",
    "head of", "vp of", "general manager",
    "practice manager", "clinic director",
    "operations director", "commercial director",
    "sales director", "business development",
]

NON_DM_TITLES: List[str] = [
    "intern", "trainee", "graduate", "apprentice",
    "administrator", "receptionist", "secretary",
    "customer service", "support", "helpdesk",
    "janitor", "cleaner", "security guard",
    "student", "volunteer", "temp",
    "assistant", "junior", "associate",
]


# ─────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────

def _get_stealth_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":              random.choice(USER_AGENTS),
        "Accept":                  (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8"
        ),
        "Accept-Language":         "en-GB,en;q=0.9,en-US;q=0.8",
        "Accept-Encoding":         "gzip, deflate, br",
        "DNT":                     "1",
        "Connection":              "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":          "document",
        "Sec-Fetch-Mode":          "navigate",
        "Sec-Fetch-Site":          "none",
        "Sec-Fetch-User":          "?1",
        "Cache-Control":           "max-age=0",
    })
    return session


def _is_google_challenge(html: str) -> bool:
    if not html:
        return True
    preview = html[:8192].lower()
    return any(marker in preview for marker in CHALLENGE_MARKERS)


# ─────────────────────────────────────────────────────────
# Snippet parsing
# ─────────────────────────────────────────────────────────

def _extract_name_title_from_snippet(
    snippet: str,
    business_name: str,
) -> Optional[Tuple[str, str]]:
    if not snippet or len(snippet) < 10:
        return None

    snippet = re.sub(r'\s+', ' ', snippet.strip())

    # Try structured patterns first
    for pattern in LINKEDIN_SNIPPET_PATTERNS:
        match = pattern.search(snippet)
        if match:
            groups = match.groups()
            name_candidate  = ""
            title_candidate = ""

            for g in groups:
                g = g.strip()
                if not g:
                    continue
                words = g.split()
                if (
                    2 <= len(words) <= 3
                    and all(
                        w[0].isupper() for w in words if w
                    )
                    and not any(
                        kw in g.lower() for kw in DM_TITLE_KEYWORDS
                    )
                ):
                    name_candidate = g
                else:
                    title_candidate = g

            if name_candidate and title_candidate:
                title_lower = title_candidate.lower()
                if any(kw in title_lower for kw in NON_DM_TITLES):
                    continue
                if any(
                    kw in title_lower for kw in DM_TITLE_KEYWORDS
                ):
                    return (name_candidate, title_candidate)

    # BUG-OSI-02: Handle ALL CAPS names
    allcaps_match = re.search(
        r'\b([A-Z]{2,15}\s+[A-Z]{2,15}'
        r'(?:\s+[A-Z]{2,15})?)\b',
        snippet,
    )
    if allcaps_match:
        name = allcaps_match.group(1).title()
        for keyword in DM_TITLE_KEYWORDS:
            if keyword in snippet.lower():
                title_match = re.search(
                    r'([^,.\n]{0,50}'
                    + re.escape(keyword)
                    + r'[^,.\n]{0,30})',
                    snippet,
                    re.IGNORECASE,
                )
                title = (
                    title_match.group(1).strip()
                    if title_match
                    else keyword.title()
                )
                return (name, title)

    # Fallback: name near keyword
    for keyword in DM_TITLE_KEYWORDS:
        if keyword in snippet.lower():
            name_match = re.search(
                r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})',
                snippet,
            )
            if name_match:
                name = name_match.group(1)
                title_match = re.search(
                    r'([^,.\n]{0,50}'
                    + re.escape(keyword)
                    + r'[^,.\n]{0,30})',
                    snippet,
                    re.IGNORECASE,
                )
                title = (
                    title_match.group(1).strip()
                    if title_match
                    else keyword.title()
                )
                return (name, title)

    return None


def _extract_linkedin_url(
    snippet: str, html_surrounding: str
) -> Optional[str]:
    match = re.search(
        r'https?://(?:www\.)?linkedin\.com/in/'
        r'[A-Za-z0-9\-_%]+/?',
        html_surrounding,
        re.IGNORECASE,
    )
    return match.group(0).rstrip('/') if match else None


# ─────────────────────────────────────────────────────────
# Core Google dorking
# ─────────────────────────────────────────────────────────

def search_linkedin_dm(
    business_name: str,
    city: str    = "",
    country: str = "",
) -> Optional[Dict]:
    """
    Search Google for LinkedIn profile snippets to find DM.
    Parses Google result snippets — NOT the LinkedIn page itself.
    """
    if not business_name:
        return None

    location_parts = [p for p in [city, country] if p]
    location_str   = " ".join(location_parts).strip()

    dork_queries = []
    if location_str:
        dork_queries.append(
            f'site:linkedin.com/in/ "{business_name}" '
            f'{location_str} '
            f'("owner" OR "founder" OR "managing director" '
            f'OR "director")'
        )
    dork_queries.append(
        f'site:linkedin.com/in/ "{business_name}" '
        f'("owner" OR "founder" OR "managing director" '
        f'OR "director")'
    )
    if location_str:
        dork_queries.append(
            f'site:linkedin.com/in/ "{business_name}" '
            f'{location_str} '
            f'("manager" OR "head" OR "lead" OR "chief")'
        )
    dork_queries.append(
        f'"{business_name}" linkedin "managing director"'
    )
    if location_str:
        dork_queries.append(
            f'"{business_name}" {location_str} linkedin director'
        )

    session       = _get_stealth_session()
    searches_done = 0

    for query in dork_queries:
        if searches_done >= MAX_SEARCHES_PER_SESSION:
            logger.debug(
                "social_osint: max searches reached for '%s'",
                business_name,
            )
            break

        delay = random.uniform(MIN_SEARCH_DELAY, MAX_SEARCH_DELAY)
        logger.debug(
            "social_osint: sleeping %.1fs for '%s'",
            delay, business_name,
        )
        time.sleep(delay)

        try:
            encoded_query = quote_plus(query)
            url = (
                f"https://www.google.com/search"
                f"?q={encoded_query}&num=10&hl=en"
            )
            session.headers["User-Agent"] = random.choice(
                USER_AGENTS
            )
            resp = session.get(url, timeout=15)
            searches_done += 1

            if resp.status_code == 429:
                logger.warning(
                    "social_osint: 429 rate limited for '%s' — "
                    "backing off 30s",
                    business_name,
                )
                time.sleep(30)
                continue

            if resp.status_code != 200:
                logger.debug(
                    "social_osint: HTTP %d for '%s'",
                    resp.status_code, business_name,
                )
                continue

            if _is_google_challenge(resp.text):
                logger.warning(
                    "social_osint: Google challenge for '%s' — "
                    "aborting",
                    business_name,
                )
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            result_divs = (
                soup.find_all("div", class_="g")
                or soup.find_all("div", class_="tF2Cxc")
                or soup.find_all(
                    "div",
                    attrs={"data-sokoban-container": True},
                )
                or []
            )

            for div in result_divs:
                snippet_el = (
                    div.find("div", class_="VwiC3b")
                    or div.find("span", class_="aCOpRe")
                    or div.find("div", class_="IsZvec")
                    or div.find("span", class_="st")
                )
                if not snippet_el:
                    continue

                snippet  = snippet_el.get_text(strip=True)
                div_html = str(div)

                if "linkedin.com/in/" not in div_html.lower():
                    continue

                result = _extract_name_title_from_snippet(
                    snippet, business_name,
                )
                if not result:
                    continue

                name, title = result

                if len(name) < 4 or len(name) > 60:
                    continue
                if not re.match(
                    r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$', name
                ):
                    continue

                linkedin_url = _extract_linkedin_url(
                    snippet, div_html
                )

                logger.info(
                    "social_osint: FOUND DM for '%s': %s — %s",
                    business_name, name, title,
                )

                return {
                    "decision_maker_name":  name,
                    "decision_maker_title": title,
                    "dm_source":  "google_linkedin_snippet",
                    "confidence": DMSource.GOOGLE_LINKEDIN_SNIPPET[1],
                    "linkedin_url": linkedin_url,
                    "snippet_text": snippet,
                    "search_query": query,
                }

        except requests.RequestException as exc:
            logger.debug(
                "social_osint: request error for '%s': %s",
                business_name, exc,
            )
            continue
        except Exception as exc:
            logger.debug(
                "social_osint: parse error for '%s': %s",
                business_name, exc,
            )
            continue

    logger.info(
        "social_osint: no DM found for '%s' after %d searches",
        business_name, searches_done,
    )
    return None


# ─────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────

def osint_find_decision_maker(
    business_name: str,
    city: str    = "",
    country: str = "",
    website: str = "",
) -> Optional[Dict]:
    """Main entry point for enricher pipeline integration."""
    return search_linkedin_dm(business_name, city, country)


__all__ = [
    "osint_find_decision_maker",
    "search_linkedin_dm",
    "DMSource",
    "test_social_osint",
]


def test_social_osint() -> None:
    test_cases = [
        ("Acme Corporation", "London", "UK"),
        ("Lagos Kitchen", "Lagos", "Nigeria"),
    ]
    for business, city, country in test_cases:
        print(f"\n--- Testing: {business}, {city} ---")
        result = osint_find_decision_maker(business, city, country)
        if result:
            print(f"  Name:     {result['decision_maker_name']}")
            print(f"  Title:    {result['decision_maker_title']}")
            print(f"  Source:   {result['dm_source']}")
            print(
                f"  LinkedIn: "
                f"{result.get('linkedin_url', 'N/A')}"
            )
        else:
            print("  No DM found")