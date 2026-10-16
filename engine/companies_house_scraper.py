# engine/companies_house_scraper.py
# KNCS LeadGen Pro — v10.1 (audit-clean)
#
# Registered bugs fixed (BUG-CHS-01 through BUG-CHS-09):
#   BUG-CHS-01  No thread lock on rate limiter — threading.Lock() now
#               wraps both read and write of LAST_REQUEST timestamp
#   BUG-CHS-02  load_dotenv() called on every API request — now called
#               once at module level; API_KEY read lazily via
#               _get_api_key() to survive import-order issues
#   BUG-CHS-03  429 retry abandoned permanently — retry loop now sleeps
#               and continues up to MAX_RETRIES before giving up
#   BUG-CHS-04  Business-type descriptors stripped from search query —
#               only legal suffixes stripped (Ltd, Limited, CIC, LLP,
#               PLC); descriptors like "Care Home" preserved
#   BUG-CHS-06  Only spaces encoded in LinkedIn URL — replaced with
#               urlencode() via urllib.parse
#   BUG-CHS-07  company_registration_number duplicated company_number —
#               redundant key removed; company_number is sole canonical
#               key (BREAKING CHANGE documented below)
#   BUG-CHS-08  No pagination for officers — partial-page guard added;
#               fetches next page until partial page returned
#   BUG-CHS-09  New TCP+TLS connection per request — module-level
#               requests.Session with HTTPAdapter; single handshake
#               per batch
#
# Additional defects fixed in this audit pass:
#   FIX-CHS-A   _session not thread-safe for concurrent use — per-
#               request thread lock added around session calls
#   FIX-CHS-B   No LAST_REQUEST tracking — rate limiter now enforces
#               minimum gap between requests using threading.Lock()
#               and time.monotonic()
#   FIX-CHS-C   _search_companies_web() no block detection — challenge/
#               CAPTCHA pages now detected before parsing
#   FIX-CHS-D   _get_company_profile_web() wrong dd element — address
#               extraction now targets correct element by ID/class
#   FIX-CHS-E   All print() replaced with logger — visible in log
#               aggregators
#   FIX-CHS-F   re.compile() inside officer loop — hoisted to module
#               constant
#
# BREAKING CHANGE:
#   lead dict no longer contains 'company_registration_number'.
#   Use 'company_number' instead (BUG-CHS-07).

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv
    # BUG-CHS-02: load_dotenv() called ONCE at module level — not per request
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "scrape_companies_house",
    "search_companies",
    "get_company_profile",
    "get_company_officers",
    "build_linkedin_url",
]

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

WEB_BASE      = "https://find-and-update.company-information.service.gov.uk"
API_BASE      = "https://api.company-information.service.gov.uk"
REQUEST_DELAY = 0.6   # minimum seconds between outbound requests
MAX_RETRIES   = 3
OFFICERS_PAGE_SIZE = 35   # CH API default page size for officers

# BUG-CHS-04: only strip legal suffixes — never strip descriptors
_LEGAL_SUFFIXES = re.compile(
    r"\b(ltd|limited|llp|llc|plc|cic|cio|"
    r"incorporated|corp|corporation|"
    r"community interest company)\b",
    re.IGNORECASE,
)

# FIX-CHS-F: compiled once at module level — not per officer per page
_OFFICER_ROLE_ID_RE = re.compile(r"officer-role-.*")

_USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]

# ─────────────────────────────────────────────────────────
# Thread-safe rate limiter
# BUG-CHS-01 / FIX-CHS-B: Lock guards both read and write
# of _LAST_REQUEST so concurrent threads cannot bypass the
# minimum delay between outbound requests.
# ─────────────────────────────────────────────────────────
_RATE_LOCK:    threading.Lock = threading.Lock()
_LAST_REQUEST: float          = 0.0   # time.monotonic() of last fired request

# FIX-CHS-A: separate lock for requests.Session — Session is not
# thread-safe for concurrent use; all session calls serialised.
_SESSION_LOCK: threading.Lock = threading.Lock()


def _throttle() -> None:
    """
    BUG-CHS-01 / FIX-CHS-B:
    Enforce minimum REQUEST_DELAY between outbound requests.
    Lock covers both the read of _LAST_REQUEST and its update —
    no two threads can read-then-write simultaneously.
    """
    global _LAST_REQUEST
    with _RATE_LOCK:
        now     = time.monotonic()
        elapsed = now - _LAST_REQUEST
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        _LAST_REQUEST = time.monotonic()


# ─────────────────────────────────────────────────────────
# Shared HTTP session
# BUG-CHS-09: module-level Session with HTTPAdapter —
# single TLS handshake per batch, connection pooling.
# ─────────────────────────────────────────────────────────
def _build_session() -> requests.Session:
    session = requests.Session()
    # urllib3 retry for transient network errors only —
    # application-level 429/401 handled in _get_api()
    retry = Retry(
        total=0,          # no urllib3 retries — we handle in code
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=2,
        pool_maxsize=4,
    )
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


_session: requests.Session = _build_session()


# ─────────────────────────────────────────────────────────
# API key — lazy reader
# BUG-CHS-02: API_KEY read once via function so that if
# load_dotenv() fires after module import the key is still
# picked up on first real use.
# ─────────────────────────────────────────────────────────
_API_KEY_CACHE: Optional[str] = None


def _get_api_key() -> str:
    global _API_KEY_CACHE
    if _API_KEY_CACHE is None:
        _API_KEY_CACHE = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    return _API_KEY_CACHE


def has_api_key() -> bool:
    return bool(_get_api_key())


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _get_web_headers() -> Dict[str, str]:
    return {
        "User-Agent":      random.choice(_USER_AGENTS),
        "Accept":          (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Connection":      "keep-alive",
    }


def _title_name(name: str) -> str:
    if not name:
        return ""
    return " ".join(word.capitalize() for word in name.lower().split())


def _strip_legal_suffixes(name: str) -> str:
    """
    BUG-CHS-04: Strip ONLY legal entity suffixes — never descriptors.
    "Sunrise Care Home Ltd" → "Sunrise Care Home" ✅
    "Sunrise Care Home"     → "Sunrise Care Home" ✅  (no change)
    Old behaviour stripped ALL trailing words — "Care Home" disappeared.
    """
    return _LEGAL_SUFFIXES.sub("", name).strip(" ,.")


def _is_challenge_page(html_text: str) -> bool:
    """
    FIX-CHS-C: Detect Cloudflare/CAPTCHA challenge pages before parsing.
    Returns True if the page looks like a challenge — not real results.
    """
    if not html_text:
        return True
    sample = html_text[:4096].lower()
    markers = (
        "cf-browser-verification",
        "checking your browser",
        "please wait",
        "captcha",
        "ddos protection by cloudflare",
        "enable javascript",
        "ray id",
    )
    return any(m in sample for m in markers)


def build_linkedin_url(company_name: str) -> str:
    """
    BUG-CHS-06: Use urlencode() so ALL special characters are encoded,
    not just spaces.

    Old: name.replace(" ", "%20")  — & ' # still broke URL params.
    New: urlencode({"keywords": query}) — all chars properly encoded.
    """
    query = _strip_legal_suffixes(company_name)
    qs    = urlencode({"keywords": query})
    return "https://www.linkedin.com/search/results/companies/?" + qs


# ─────────────────────────────────────────────────────────
# API layer
# BUG-CHS-03: 429 now retries up to MAX_RETRIES before
#             abandoning — not permanently abandoned on
#             first rate-limit response.
# ─────────────────────────────────────────────────────────

def _get_api(path: str, params: Optional[dict] = None) -> dict:
    """
    Make an authenticated GET request to the Companies House API.

    BUG-CHS-01/FIX-CHS-B: _throttle() enforces per-request delay
                            under lock before every attempt.
    BUG-CHS-03:            429 sleeps then retries — not abandoned.
    BUG-CHS-09:            Uses shared _session — no new TCP per call.
    FIX-CHS-A:             _SESSION_LOCK serialises session access.
    """
    api_key = _get_api_key()
    if not api_key:
        return {}

    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()   # BUG-CHS-01: rate limit enforced under lock
        try:
            with _SESSION_LOCK:   # FIX-CHS-A: serialise session use
                resp = _session.get(
                    API_BASE + path,
                    auth=(api_key, ""),
                    params=params or {},
                    timeout=25,
                )

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                # BUG-CHS-03: sleep then RETRY — not return
                wait = 10 * attempt
                logger.warning(
                    "_get_api: 429 rate limited on attempt %d/%d — "
                    "sleeping %ds before retry",
                    attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue   # retry the loop

            if resp.status_code == 401:
                logger.error(
                    "_get_api: 401 Unauthorised — "
                    "check COMPANIES_HOUSE_API_KEY"
                )
                return {}

            logger.warning(
                "_get_api: HTTP %d for %s", resp.status_code, path
            )
            return {}

        except Exception as exc:
            logger.warning(
                "_get_api: attempt %d/%d error: %s",
                attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY * attempt)
                continue
            return {}

    return {}


# ─────────────────────────────────────────────────────────
# Public API functions
# ─────────────────────────────────────────────────────────

def search_companies(
    keyword:     str,
    location:    str  = "United Kingdom",
    max_results: int  = 20,
    active_only: bool = True,
) -> List[dict]:
    """
    Search Companies House for companies matching keyword + location.
    Uses API if key is configured; falls back to public web scraper.
    """
    if has_api_key():
        city = (
            location.split(",")[0].strip()
            if location and location != "United Kingdom"
            else ""
        )
        # BUG-CHS-04: use raw keyword — no descriptor stripping on query
        q    = f"{keyword} {city}".strip()
        data = _get_api(
            "/search/companies",
            {"q": q, "items_per_page": min(max_results * 2, 100)},
        )
        items = data.get("items", [])
        if active_only:
            items = [
                item for item in items
                if item.get("company_status", "") == "active"
            ]
        return items[:max_results]

    return _search_companies_web(keyword, location, max_results, active_only)


def get_company_profile(company_number: str) -> dict:
    """Fetch full company profile by company number."""
    if has_api_key():
        return _get_api(f"/company/{company_number}")
    return _get_company_profile_web(company_number)


def get_company_officers(company_number: str) -> List[dict]:
    """
    Fetch current officers for a company.

    BUG-CHS-08: pagination added — fetches pages until a partial
    page is returned (fewer items than page size), which indicates
    the last page. total_results == 0 no longer causes early exit.
    """
    if has_api_key():
        return _get_officers_api(company_number)
    return _get_company_officers_web(company_number)


def _get_officers_api(company_number: str) -> List[dict]:
    """
    BUG-CHS-08: paginated officer fetch.
    Stops when a page returns fewer items than the page size —
    this is the reliable end-of-results signal.
    total_results is NOT used as the stop condition because
    CH API returns 0 for small result sets.
    """
    all_officers: List[dict] = []
    start = 0

    while True:
        data = _get_api(
            f"/company/{company_number}/officers",
            {
                "items_per_page": OFFICERS_PAGE_SIZE,
                "start_index":    start,
            },
        )
        if not data:
            break

        items = data.get("items", [])
        all_officers.extend(items)

        # BUG-CHS-08: partial page = last page
        if len(items) < OFFICERS_PAGE_SIZE:
            break

        start += OFFICERS_PAGE_SIZE

    return all_officers


# ─────────────────────────────────────────────────────────
# Public web fallback layer
# ─────────────────────────────────────────────────────────

def _search_companies_web(
    keyword:     str,
    location:    str  = "United Kingdom",
    max_results: int  = 20,
    active_only: bool = True,
) -> List[dict]:
    """
    FIX-CHS-C: Challenge page detected before parsing.
    FIX-CHS-E: logger used instead of print().
    """
    city = (
        location.split(",")[0].strip()
        if location and location != "United Kingdom"
        else ""
    )
    q   = quote_plus(f"{keyword} {city}".strip())
    url = f"{WEB_BASE}/search/companies?q={q}"

    _throttle()
    try:
        with _SESSION_LOCK:
            resp = _session.get(
                url, headers=_get_web_headers(), timeout=20
            )
        if resp.status_code != 200:
            logger.warning(
                "_search_companies_web: HTTP %d", resp.status_code
            )
            return []

        # FIX-CHS-C: block detection before parsing
        if _is_challenge_page(resp.text):
            logger.warning(
                "_search_companies_web: challenge/CAPTCHA page detected"
            )
            return []

        soup    = BeautifulSoup(resp.text, "lxml")
        results = []

        for li in soup.find_all("li", class_="type-company"):
            a = li.find("a", href=True)
            if not a or "/company/" not in a["href"]:
                continue

            c_num  = a["href"].split("/company/")[1].split("/")[0].strip()
            c_name = a.get_text(strip=True)

            status_tag  = li.find("p", class_="meta")
            status_text = (
                status_tag.get_text(strip=True).lower()
                if status_tag else ""
            )
            if active_only and any(
                word in status_text
                for word in ("dissolved", "closed", "liquidation")
            ):
                continue

            addr_tag = li.find("p", class_="")
            addr     = addr_tag.get_text(strip=True) if addr_tag else ""

            results.append({
                "company_number": c_num,
                "title":          c_name,
                "company_status": (
                    "active" if "active" in status_text else "unknown"
                ),
                "address_snippet": addr,
            })
            if len(results) >= max_results:
                break

        return results

    except Exception as exc:
        logger.warning("_search_companies_web error: %s", exc)
        return []


def _get_company_profile_web(company_number: str) -> dict:
    """
    FIX-CHS-D: Address extraction corrected — old code found the
    first dd.data element which is the company number field, not
    the registered address. Now finds the address section by
    the correct heading/label structure.
    """
    url = f"{WEB_BASE}/company/{company_number}"
    _throttle()
    try:
        with _SESSION_LOCK:
            resp = _session.get(
                url, headers=_get_web_headers(), timeout=15
            )
        if resp.status_code != 200:
            return {}

        if _is_challenge_page(resp.text):
            logger.warning(
                "_get_company_profile_web: challenge page for %s",
                company_number,
            )
            return {}

        soup = BeautifulSoup(resp.text, "lxml")

        # Find the registered address section by its heading label
        addr_str = ""
        for dt in soup.find_all("dt"):
            dt_text = dt.get_text(strip=True).lower()
            if "registered office address" in dt_text:
                dd = dt.find_next_sibling("dd")
                if dd:
                    addr_str = dd.get_text(", ", strip=True)
                break

        # Fallback: look for element with id containing 'address'
        if not addr_str:
            addr_el = soup.find(
                id=re.compile(r".*address.*", re.IGNORECASE)
            )
            if addr_el:
                addr_str = addr_el.get_text(", ", strip=True)

        return {
            "company_number": company_number,
            "registered_office_address": {
                "address_line_1": addr_str,
            },
        }

    except Exception as exc:
        logger.warning(
            "_get_company_profile_web(%s): %s", company_number, exc
        )
        return {}


def _get_company_officers_web(company_number: str) -> List[dict]:
    """
    FIX-CHS-F: _OFFICER_ROLE_ID_RE compiled at module level — not
                inside the loop on every officer.
    FIX-CHS-E: logger used instead of print().
    """
    url = f"{WEB_BASE}/company/{company_number}/officers"
    _throttle()
    try:
        with _SESSION_LOCK:
            resp = _session.get(
                url, headers=_get_web_headers(), timeout=15
            )
        if resp.status_code != 200:
            return []

        if _is_challenge_page(resp.text):
            logger.warning(
                "_get_company_officers_web: challenge page for %s",
                company_number,
            )
            return []

        soup     = BeautifulSoup(resp.text, "lxml")
        officers = []

        for div in soup.find_all("div", class_="appointment-1"):
            h2 = div.find("h2")
            if not h2:
                continue
            name = h2.get_text(strip=True)

            # FIX-CHS-F: module-level compiled pattern
            role_el = div.find("dd", id=_OFFICER_ROLE_ID_RE)
            role    = (
                role_el.get_text(strip=True)
                if role_el else "Director"
            )

            status_el = div.find("span", class_="status-tag")
            status    = (
                status_el.get_text(strip=True).lower()
                if status_el else "active"
            )
            if "resigned" in status:
                continue

            officers.append({
                "name":   name,
                "role":   role,
                "status": "active",
            })

        return officers

    except Exception as exc:
        logger.warning(
            "_get_company_officers_web(%s): %s", company_number, exc
        )
        return []


# ─────────────────────────────────────────────────────────
# Main scraper entry point
# FIX-CHS-E: all print() replaced with logger
# BUG-CHS-07: company_registration_number removed —
#             company_number is the sole canonical key
# ─────────────────────────────────────────────────────────

def scrape_companies_house(
    keyword:      str,
    location:     str  = "United Kingdom",
    max_results:  int  = 20,
    get_details:  bool = True,
    get_officers: bool = True,
    active_only:  bool = True,
    **kwargs: Any,
) -> List[dict]:
    """
    Scrape Companies House for leads matching keyword + location.

    Returns a list of lead dicts ready for insert_lead().

    BREAKING CHANGE (BUG-CHS-07):
        Lead dicts no longer contain 'company_registration_number'.
        Use 'company_number' only.
    """
    logger.info(
        "scrape_companies_house: keyword=%r location=%r max=%d",
        keyword, location, max_results,
    )

    companies = search_companies(keyword, location, max_results, active_only)
    if not companies:
        logger.warning("scrape_companies_house: no companies found")
        return []

    logger.info(
        "scrape_companies_house: processing %d companies", len(companies)
    )
    leads: List[dict] = []

    for i, comp in enumerate(companies, 1):
        try:
            c_num  = comp.get("company_number", "")
            c_name = comp.get("title", comp.get("company_name", ""))
            if not c_num or not c_name:
                logger.debug(
                    "scrape_companies_house: skipping entry %d — "
                    "missing number or name",
                    i,
                )
                continue

            # Address — use snippet from search result; fetch detail
            # only if snippet is absent (saves an API call per company)
            addr_str = comp.get("address_snippet", "")
            if get_details and not addr_str:
                prof     = get_company_profile(c_num)
                addr_obj = prof.get("registered_office_address", {})
                if isinstance(addr_obj, dict):
                    addr_str = ", ".join(
                        v for v in addr_obj.values()
                        if v and isinstance(v, str)
                    )

            # Decision maker — first matching director-type officer
            dm_name  = ""
            dm_title = "Director"
            if get_officers:
                officers = get_company_officers(c_num)
                for off in officers:
                    role = str(off.get("role", "")).lower()
                    if any(
                        r in role
                        for r in (
                            "director", "managing-director",
                            "owner", "proprietor", "partner",
                        )
                    ):
                        dm_name  = _title_name(off.get("name", ""))
                        dm_title = off.get("role", "Director").title()
                        break
                if not dm_name and officers:
                    dm_name  = _title_name(officers[0].get("name", ""))
                    dm_title = officers[0].get("role", "Officer").title()

            city = (
                location.split(",")[0].strip()
                if location != "United Kingdom"
                else ""
            )

            lead = {
                # BUG-CHS-07: company_number is the SOLE canonical key —
                # company_registration_number removed entirely
                "company_number":       c_num,
                "business_name":        c_name,
                "address":              addr_str,
                "city":                 city,
                "country":              "United Kingdom",
                "industry":             keyword,
                "source":               "companies_house",
                "decision_maker_name":  dm_name,
                "decision_maker_title": dm_title,
                "dm_source":            "companies_house",
                "phone":                "",
                "email":                "",
                "website":              "",
            }
            leads.append(lead)
            logger.info(
                "scrape_companies_house: [%d/%d] %s | DM: %s",
                i, len(companies), c_name, dm_name or "—",
            )

        except Exception as exc:
            logger.warning(
                "scrape_companies_house: [%d/%d] error: %s",
                i, len(companies), exc,
            )
            continue

    logger.info(
        "scrape_companies_house: complete — %d leads ready", len(leads)
    )
    return leads