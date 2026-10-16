# engine/enricher.py
# KNCS LeadGen Pro — v10.3 (Audit Compliant + DM Name Cleaner + CH Country Gate)
#
# Bugs fixed in this version:
#   BUG-014:   Per-step exception collection via _run_step
#   BUG-015:   validate_website_url() before any crawl
#   BUG-016:   _is_uk_context AND _is_care_context both required for CQC
#   BUG-017:   crawl_budget mutable list caps ALL HTTP calls
#   NEW-01:    safe_unpack() prevents TypeError on None tuple unpack
#   NEW-02:    website local var re-read after Step 1
#   NEW-03:    reenrich_leads_missing_emails writes all fields
#   NEW-04:    enrichment_notes — explicit if/else (Py3.7 safe)
#   NEW-05:    _run_step distinguishes None from exception via sentinel
#   NEW-06:    discover_business_website always returns tuple
#   NEW-07:    Homepage crawl_page() deducts from crawl_budget
#   NEW-08:    fields_improved > 0 always wins over step_errors
#   FIX-DM-01: clean_decision_maker_name() — full cleaning pipeline
#   BUG-RE-02: bingj.com / bingj.net added to SEARCH_ENGINE_DOMAINS
#   BUG-RE-03: RFC 5321 local-part validation in is_junk_email()
#   BUG-RE-06: JUNK_EMAIL_PATTERNS extended with CDN/cache domains
#   BUG-RE-07: min_confidence raised 40->60 for all search HTML parsing
#   BUG-RE-08: score_email_confidence requires >=5-char token for full points
#   BUG-RE-04: is_foreign_email() now called inside is_junk_email()
#              so foreign TLDs (.gr .de .fr etc.) are always blocked
#   FIX-CH-COUNTRY-01 (CRITICAL): _lookup_companies_house_director()
#              previously had NO country check at all and was called
#              from enrich_lead() Step 4 for EVERY lead worldwide with
#              a blank decision_maker_name. Because its internal
#              matching is a loose token-overlap search, generic words
#              like "dental", "view", "royal", "central" in a US/Nigeria
#              business name would match unrelated UK companies in
#              Companies House and attach a real UK officer's name to
#              a non-UK business as its "decision maker" (e.g. Dallas,
#              TX dental clinics were assigned names like "Bobbie
#              Woolgrove" and "Amrik Singh Bhandal" — real UK company
#              officers with zero connection to Texas). Worse, when no
#              token matched at all, the fallback loop picked the
#              FIRST active UK company in the result set — a fully
#              random assignment. Fixed by adding a hard `country`
#              parameter and gating the entire function to UK leads
#              only via _is_uk_context(), and passing `country`
#              through at the Step 4 call site in enrich_lead().

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

__all__ = [
    "enrich_lead",
    "find_email_multi_source",
    "find_phone_multi_source",
    "get_contact_completeness",
    "discover_business_website",
    "reenrich_leads_missing_emails",
    "safe_merge",
    "clean_decision_maker_name",
    "is_junk_email",
    "is_foreign_email",
]

# ─────────────────────────────────────────────────────────
# Imports — use database.py for all DB operations
# ─────────────────────────────────────────────────────────
try:
    from database import (
        get_connection,
        update_lead_email,
        get_leads_missing_emails,
        insert_lead,
    )
    _DB_AVAILABLE = True
except ImportError:
    logger.error(
        "database.py not available — enrichment DB operations disabled"
    )
    get_connection           = None  # type: ignore
    update_lead_email        = None  # type: ignore
    get_leads_missing_emails = None  # type: ignore
    insert_lead              = None  # type: ignore
    _DB_AVAILABLE            = False

# ─────────────────────────────────────────────────────────
# CQC Integration
# ─────────────────────────────────────────────────────────
try:
    from engine.cqc_client import lookup_cqc_location
    CQC_AVAILABLE = True
except ImportError:
    CQC_AVAILABLE = False
    logger.info("CQC module not available")

    def lookup_cqc_location(*args, **kwargs) -> Dict:
        return {
            "registered_manager": "",
            "phone":              "",
            "address":            "",
            "cqc_rating":         "",
            "cqc_url":            "",
            "cqc_location_id":    "",
            "match_confidence":   "none",
            "source":             "",
        }

# ─────────────────────────────────────────────────────────
# DDGS Import
# ─────────────────────────────────────────────────────────
try:
    from ddgs import DDGS as DDGS_CLASS
    DDGS_AVAILABLE = True
    logger.info("ddgs package available")
except ImportError:
    try:
        from duckduckgo_search import DDGS as DDGS_CLASS
        DDGS_AVAILABLE = True
        logger.info("duckduckgo_search package available")
    except ImportError:
        DDGS_CLASS     = None
        DDGS_AVAILABLE = False
        logger.info("No DDGS package — Bing HTTP fallback only")

# ─────────────────────────────────────────────────────────
# Companies House
# ─────────────────────────────────────────────────────────
import os

_CH_KEY       = os.getenv("COMPANIES_HOUSE_API_KEY", "")
_CH_AVAILABLE = bool(_CH_KEY)

# ─────────────────────────────────────────────────────────
# Sentinel
# ─────────────────────────────────────────────────────────
_STEP_EXCEPTION = object()

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────
HEADERS_POOL = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Cache-Control":   "max-age=0",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.4 Safari/605.1.15"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Connection":      "keep-alive",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
            "Gecko/20100101 Firefox/125.0"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.7",
        "Connection":      "keep-alive",
    },
]

# ── BUG-RE-06: Extended with search engine CDN / cache domains ────
JUNK_EMAIL_PATTERNS = [
    # Placeholder / test addresses
    "example", "test@", "noreply", "no-reply", "donotreply",
    "schema.org", "w3.org", "sentry",
    # Image file extensions in email address
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    # Fake names used in documentation
    "support@b2b", "jane.doe", "john.doe", "jdoe",
    "jane@", "john@", "user@", "admin@example",
    "info@example", "hello@example", "test@test",
    "email@email", "yourname@", "youremail@",
    "name@domain", "sample@",
    # BUG-RE-06 / BUG-RE-12: Search engine cache / CDN domains
    "bingj.com", "bingj.net", "cc.bingj.", "bingj",
    "webcache.googleusercontent", "googleusercontent",
    "gstatic.com", "googleapis.com",
    "r.search.yahoo", "search.yahoo",
    "duckduckgo.com",
    "bing.com", "google.com", "yahoo.com",
    "yell.com", "yelp.com", "192.com",
    "thephone.book", "bt.com/directory",
]

FOREIGN_TLDS = {
    ".cz", ".de", ".fr", ".pl", ".ru", ".cn", ".es", ".it", ".nl",
    ".pt", ".ro", ".hu", ".sk", ".hr", ".bg", ".lt", ".lv", ".ee",
    ".ua", ".tr", ".gr", ".rs", ".si", ".at", ".ch", ".se", ".no",
    ".dk", ".fi",
}

# ── BUG-RE-02: Extended with Bing CDN subdomains ──────────────────
SEARCH_ENGINE_DOMAINS = [
    "google.com", "bing.com", "yahoo.com", "duckduckgo.com",
    "baidu.com", "ask.com",
    "w3.org", "schema.org",
    "wikipedia.org",
    "facebook.com", "twitter.com", "instagram.com",
    "linkedin.com", "youtube.com",
    # BUG-RE-02: Bing CDN subdomains
    "bingj.com", "bingj.net",
    # Google infrastructure
    "googleusercontent.com", "gstatic.com", "googleapis.com",
    # Yahoo infrastructure
    "yimg.com",
]

SEARCH_ENGINE_DOMAINS_EXTENDED = [
    "google.com", "bing.com", "yahoo.com", "duckduckgo.com",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "wikipedia.org", "yelp.com",
    "yellowpages.com", "whitepages.com", "bbb.org", "manta.com",
    "hotfrog.com", "w3.org", "schema.org", "ico.org.uk", "gov.uk",
    "legislation.gov.uk", "openstreetmap.org", "gstatic.com",
    "googleapis.com", "cloudflare.com", "jquery.com",
    "bootstrapcdn.com", "live.com", "microsoft.com",
    "microsoftonline.com", "office.com", "nhs.uk", "yell.com",
    "housingcare.org", "rated.care", "carebenefit.co.uk",
    "trustedcare.co.uk", "patient.info",
    "economicsbydesign.com", "careadvisor.co.uk", "carehome.direct",
    # BUG-RE-02: Bing CDN
    "bingj.com", "bingj.net",
    "googleusercontent.com", "yimg.com",
]

CARE_HOME_AGGREGATORS = [
    "carehome.co.uk", "carehomes.co.uk", "caring.com",
    "homecare.co.uk", "cqc.org.uk", "nhs.uk",
    "careinspectorate.com", "carehomelist.co.uk", "autumna.co.uk",
    "lottie.org", "carechoices.co.uk", "goodcare.guide",
    "carehome.direct", "elder.org", "carehome.org.uk",
    "theguardian.com", "dailymail.co.uk", "bbc.co.uk", "bbc.com",
    "housingcare.org", "rated.care", "carebenefit.co.uk",
    "trustedcare.co.uk", "patient.info", "caresourcer.com",
    "healthgps.co.uk", "careadvisor.co.uk", "caredirectory.co.uk",
    "laingbuisson.com", "myhomecare.co.uk", "seniorcareguide.co.uk",
    "carehomefinder.co.uk", "local.gov.uk", "cqcratings.com",
    "adviserplus.com", "hscic.gov.uk",
]

NAME_IGNORE_WORDS = {
    "inc", "llc", "ltd", "limited", "company", "corp",
    "corporation", "services", "group", "holdings", "solutions",
    "the", "and", "for", "nigeria", "nigerian", "nig", "ent",
    "enterprises", "associates", "global", "prime", "galaxy",
    "alpha", "omega", "royal", "premier", "elite", "supreme",
    "mega", "macro", "micro", "inter", "integrated",
    "care", "home", "house", "nursing", "residential", "lodge",
    "manor", "court", "hall", "place", "cottage", "villa", "park",
    "rise", "gardens", "centre", "center", "trust", "foundation",
}

GENERIC_MANAGER_VALUES = {
    "Registered Manager", "Manager Post Vacant", "",
}


# ─────────────────────────────────────────────────────────
# FIX-DM-01: Decision Maker Name Cleaner — v10.2
# ─────────────────────────────────────────────────────────

_DM_JOB_TITLE_SUFFIXES = re.compile(
    r'\s*\b('
    r'Clinical|Dentist|Director|Manager|Owner|Principal|'
    r'Founder|Partner|Associate|Consultant|Specialist|'
    r'Surgeon|Doctor|Practitioner|Therapist|Nurse|Hygienist|'
    r'Pharmacist|Optician|Physiotherapist|Radiographer|'
    r'CEO|MD|COO|CFO|CTO|CMO|CSO|VP|'
    r'Lead|Head|Chief|Senior|Junior|Assistant|Deputy|'
    r'Registered|Nominated|Individual|Officer|Secretary|'
    r'Proprietor|Administrator|Coordinator|Supervisor|'
    r'Prof|Professor|MBChB|BDS|MFDS|MJDF|FDS|BChD|'
    r'Engineer|Developer|Designer|Analyst|Architect'
    r')\b.*$',
    re.IGNORECASE,
)

_DM_TECH_NAME = re.compile(
    r'^[A-Z]{2,}[a-z]*[A-Z][a-z]+\d*$'
)

_DM_MULTI_PERSON = re.compile(
    r',\s*(?:Mrs?\.?\s|Ms\.?\s|Dr\.?\s|Mr\.?\s)'
)

_DM_LEADING_TITLES = re.compile(
    r'^(?:Dr\.?\s+|Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+|'
    r'Prof\.?\s+|Sir\s+|Lord\s+|Dame\s+)',
    re.IGNORECASE,
)

_DM_ORG_INDICATORS = [
    "institute", "international", "national", "foundation",
    "association", "federation", "contributors", "bootstrap",
    "framework", "network", "head office", "head chef",
    "reception", "enquiries", "solutions", "systems",
    "technologies", "enterprises", "holdings", "committee",
    "department", "division", "ministry", "authority",
    "corporation", "incorporated", "organisation", "organization",
    "university", "college", "school", "academy", "charity",
    "trust", "council", "government", "agency", "bureau",
]

_DM_BAD_EXACT = {
    "team", "staff", "contact", "about", "services",
    "welcome", "management", "administration", "reception",
    "anonymous", "unknown", "n/a", "none", "null",
    "head office", "head chef", "our team", "the team",
    "our staff", "enquiries", "info", "support",
}


def _fix_ch_name_format(name: str) -> str:
    """
    Convert Companies House "SURNAME, Firstname" to
    "Firstname Surname".
    Only called when a comma is present in the name.
    """
    parts   = name.split(",", 1)
    surname = parts[0].strip()
    rest    = parts[1].strip() if len(parts) > 1 else ""

    rest = _DM_LEADING_TITLES.sub("", rest).strip()

    rest = re.sub(
        r',?\s*\b(Dr|Mr|Mrs|Ms|Prof|Sir|MBChB|BDS|MJDF|'
        r'MFDS|FDS|BChD|[A-Z]{1,4})\b\.?\s*$',
        '',
        rest,
        flags=re.IGNORECASE,
    ).strip()

    rest = re.sub(r'[,.\s]+$', '', rest).strip()

    if not rest:
        return surname.title()

    return (rest + " " + surname).title()


def clean_decision_maker_name(raw_name: str) -> str:
    """
    FIX-DM-01: Full decision maker name cleaning pipeline.
    """
    if not raw_name or not isinstance(raw_name, str):
        return ""

    name = raw_name.strip()
    if not name:
        return ""

    if "@" in name:
        return ""

    if re.search(r'\d{5,}', name):
        return ""

    if name.lower().startswith(("http", "www.")):
        return ""

    if "," in name:
        name = _fix_ch_name_format(name)

    parts = _DM_MULTI_PERSON.split(name)
    name  = parts[0].strip()

    if _DM_TECH_NAME.match(name):
        return ""

    name = _DM_LEADING_TITLES.sub("", name).strip()
    name = _DM_JOB_TITLE_SUFFIXES.sub("", name).strip()
    name = re.sub(r'[\s,.\-–|]+$', '', name).strip()

    if name.isupper():
        name = name.title()

    if len(name) < 3:
        return ""

    if not re.search(r'[a-zA-Z]{2,}', name):
        return ""

    if name.lower() in _DM_BAD_EXACT:
        return ""

    name_lower = name.lower()
    if any(ind in name_lower for ind in _DM_ORG_INDICATORS):
        return ""

    if len(name.split()) > 4:
        return ""

    if len(name) > 60:
        return ""

    return name


# ─────────────────────────────────────────────────────────
# General helpers
# ─────────────────────────────────────────────────────────

def get_headers() -> Dict:
    return random.choice(HEADERS_POOL)


def human_pause(min_s: float = 0.5, max_s: float = 1.5) -> None:
    time.sleep(random.uniform(min_s, max_s))


def safe_merge(base: Dict, updates: Dict) -> Dict:
    """Merge updates into base — only fills empty slots."""
    result = dict(base)
    for key, new_val in updates.items():
        existing = result.get(key)
        existing_empty = existing is None or str(
            existing
        ).strip() in ("", "None", "null", "0")
        new_empty = new_val is None or str(new_val).strip() in (
            "", "None", "null"
        )
        if existing_empty and not new_empty:
            result[key] = new_val
    return result


def safe_unpack(value: Any, default: Tuple) -> Tuple:
    """Safely unpack a tuple — returns default on failure."""
    if value is _STEP_EXCEPTION or value is None:
        return default
    if isinstance(value, tuple):
        return value
    return default


# ─────────────────────────────────────────────────────────
# Domain helpers
# ─────────────────────────────────────────────────────────

def normalize_domain(url: str) -> str:
    if not url:
        return ""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return domain.strip("/")
    except Exception:
        return ""


def is_bad_domain(domain: str, niche: str = "") -> bool:
    if not domain:
        return True
    check = domain.lower().replace("www.", "")
    for bad in SEARCH_ENGINE_DOMAINS_EXTENDED:
        if check == bad or check.endswith("." + bad):
            return True
    if "care" in niche.lower():
        for agg in CARE_HOME_AGGREGATORS:
            if check == agg or check.endswith("." + agg):
                return True
    return False


def extract_domains_from_html(html: str) -> List[str]:
    if not html:
        return []
    pattern = r'https?://([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})'
    matches = re.findall(pattern, html)
    domains = []
    seen: set = set()
    for d in matches:
        d = d.lower().replace("www.", "")
        if d not in seen:
            seen.add(d)
            domains.append(d)
    return domains


def business_name_tokens(name: str) -> List[str]:
    if not name:
        return []
    words = re.findall(r"[a-zA-Z]{4,}", name.lower())
    return [w for w in words if w not in NAME_IGNORE_WORDS]


def score_domain_relevance(domain: str, business_name: str) -> int:
    if not domain:
        return 0
    score  = 0
    domain = domain.lower().replace("www.", "")
    tokens = business_name_tokens(business_name)
    root   = domain.split(".")[0]
    if not tokens:
        return 0
    for token in tokens:
        if len(token) < 4:
            continue
        if token == root:
            score += 100
        elif token in root:
            score += 40
        elif root in token:
            score += 20
    return score


def is_foreign_email(email: str) -> bool:
    """Returns True if the email domain uses a foreign TLD."""
    if not email or "@" not in email:
        return True
    domain = email.split("@")[-1].lower()
    return any(domain.endswith(tld) for tld in FOREIGN_TLDS)


# ─────────────────────────────────────────────────────────
# BUG-015: URL Validation
# ─────────────────────────────────────────────────────────

def _validate_website_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    check_url = (
        url if url.startswith(("http://", "https://"))
        else "https://" + url
    )
    try:
        parsed = urlparse(check_url)
        domain = parsed.netloc.lower().replace("www.", "")
        if not domain or "." not in domain:
            return False
        if is_bad_domain(domain, ""):
            return False
        tld = domain.split(".")[-1]
        if len(tld) < 2 or len(tld) > 10:
            return False
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────
# Companies House Director Lookup
# ─────────────────────────────────────────────────────────

def _lookup_companies_house_director(
    business_name: str, postcode: str = "", country: str = ""
) -> str:
    """
    FIX-CH-COUNTRY-01: Companies House is the UK company registry —
    it has zero coverage of US, Nigerian, or any non-UK entities.
    This function previously had NO country check at all and was
    called from enrich_lead() Step 4 for every lead worldwide with
    a blank decision_maker_name. Because the matching logic below
    is a loose token-overlap search (business_name_tokens), generic
    words like "dental", "view", "royal", "central" would match
    unrelated UK companies and attach a random real UK officer's
    name to a US business as its "decision maker" — e.g. Dallas
    dental clinics were assigned names like "Bobbie Woolgrove" and
    "Amrik Singh Bhandal", real Companies House officers with zero
    connection to Texas. Worse, if no token matched at all, the code
    fell back to picking the FIRST active UK company in the results
    list — a fully random assignment.

    This hard gate makes the function a guaranteed no-op for any
    non-UK lead, regardless of what calls it or from where, so this
    class of bug cannot silently reappear via a different call site.
    """
    if not _is_uk_context(country):
        return ""
    if not business_name or not _CH_AVAILABLE:
        return ""
    try:
        search_url = (
            "https://api.company-information.service.gov.uk"
            "/search/companies"
        )
        params = {"q": business_name, "items_per_page": 5}
        auth   = (_CH_KEY, "")
        resp   = requests.get(
            search_url, params=params, auth=auth, timeout=10,
        )
        if resp.status_code != 200:
            return ""
        companies = resp.json().get("items", [])
        if not companies:
            return ""

        target_company = None
        tokens = business_name_tokens(business_name)
        for company in companies:
            status = company.get("company_status", "").lower()
            if status not in ("active", ""):
                continue
            title_lower = company.get("title", "").lower()
            match_count = sum(
                1 for t in tokens if t in title_lower
            )
            if match_count >= 1:
                target_company = company
                break
        if not target_company:
            for company in companies:
                if company.get(
                    "company_status", ""
                ).lower() in ("active", ""):
                    target_company = company
                    break
        if not target_company:
            return ""

        company_number = target_company.get("company_number", "")
        if not company_number:
            return ""

        officers_url = (
            f"https://api.company-information.service.gov.uk"
            f"/company/{company_number}/officers"
        )
        resp2 = requests.get(
            officers_url, auth=auth, timeout=10,
        )
        if resp2.status_code != 200:
            return ""
        officers = resp2.json().get("items", [])
        if not officers:
            return ""

        DIRECTOR_ROLES = {
            "director", "managing director",
            "chief executive officer", "proprietor",
            "manager", "registered manager",
            "nominated individual",
        }
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            role = (
                officer.get("officer_role", "")
                .lower().replace("-", " ")
            )
            if any(r in role for r in DIRECTOR_ROLES):
                raw = officer.get("name", "")
                if raw:
                    formatted = _format_ch_name(raw)
                    cleaned   = clean_decision_maker_name(formatted)
                    if cleaned:
                        return cleaned
        for officer in officers:
            if officer.get("resigned_on"):
                continue
            raw = officer.get("name", "")
            if raw:
                formatted = _format_ch_name(raw)
                cleaned   = clean_decision_maker_name(formatted)
                if cleaned:
                    return cleaned
        return ""
    except Exception as exc:
        logger.debug("_lookup_companies_house_director: %s", exc)
        return ""


def _format_ch_name(raw_name: str) -> str:
    """Format raw Companies House name from SURNAME, Firstname."""
    if not raw_name:
        return ""
    raw_name = raw_name.strip()
    if "," in raw_name:
        parts    = raw_name.split(",", 1)
        surname  = parts[0].strip().title()
        forename = parts[1].strip().title()
        if forename:
            return forename + " " + surname
        return surname
    return raw_name.title()


# ─────────────────────────────────────────────────────────
# Email Validation
# BUG-RE-03: RFC 5321 local-part validation
# BUG-RE-04: is_foreign_email() called inside is_junk_email()
# ─────────────────────────────────────────────────────────

_RFC5321_LOCAL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+$')


def is_junk_email(email: str) -> bool:
    """
    Returns True if the email should be rejected.

    Checks (in order):
      1. Missing or no @ sign
      2. BUG-RE-03: RFC 5321 local part — rejects commas,
         spaces, consecutive dots
      3. Domain has at least one dot
      4. Pattern blocklist (JUNK_EMAIL_PATTERNS)
      5. Known search engine / CDN domain blocklist
      6. BUG-RE-04: Foreign TLD check (.gr .de .fr .ru etc.)
         is_foreign_email() is called here so any caller that
         calls is_junk_email() gets the foreign TLD check for
         free — no separate call needed.
    """
    if not email or "@" not in email:
        return True

    email_lower = email.strip().lower()

    # BUG-RE-03: Reject syntactically invalid local parts
    if email_lower.count('@') != 1:
        return True
    local, domain = email_lower.split('@', 1)
    if not _RFC5321_LOCAL_RE.match(local):
        return True          # comma, space, slash, etc.
    if '..' in local or '..' in domain:
        return True          # consecutive dots
    if '.' not in domain:
        return True          # no TLD at all

    # Pattern blocklist — BUG-RE-06 patterns included above
    for pattern in JUNK_EMAIL_PATTERNS:
        if pattern in email_lower:
            return True

    # Known search engine / CDN domain blocklist — BUG-RE-02
    for bad_domain in SEARCH_ENGINE_DOMAINS:
        if domain == bad_domain or domain.endswith(
            "." + bad_domain
        ):
            return True

    # BUG-RE-04: Foreign TLD check
    # is_foreign_email() is defined below but Python resolves
    # this at call time not definition time so forward ref is fine
    if is_foreign_email(email_lower):
        return True

    return False


def email_matches_business(
    email: str,
    business_name: str,
    website: str = None,
) -> bool:
    if not email or "@" not in email:
        return False
    email_lower        = email.lower()
    local_part, domain = email_lower.split("@", 1)
    if website:
        try:
            parsed_site = urlparse(
                website
                if website.startswith("http")
                else "https://" + website
            )
            site_domain  = (
                parsed_site.netloc.lower().replace("www.", "")
            )
            email_domain = domain.replace("www.", "")
            if site_domain and site_domain == email_domain:
                return True
            site_root = site_domain.split(".")[0]
            if (
                site_root
                and len(site_root) > 3
                and site_root in email_domain
            ):
                return True
        except Exception:
            pass
    if business_name:
        words = re.findall(r"[a-zA-Z]{4,}", business_name.lower())
        for word in words:
            if word in domain or word in local_part:
                return True
    return False


def score_email_confidence(
    email: str,
    business_name: str,
    website: str = None,
    source: str  = "unknown",
) -> int:
    """
    BUG-RE-08: Business name match now requires at least one token
    of >= 5 characters to award full 40 points. Short tokens
    (care, home, park, etc.) only award 15 points to prevent
    false positive matches on generic words.
    """
    if is_junk_email(email):
        return 0
    score = 20
    if source == "website":
        score += 40
    if email_matches_business(email, business_name, website):
        if business_name:
            long_tokens = [
                w for w in re.findall(
                    r"[a-zA-Z]{5,}", business_name.lower()
                )
                if w not in NAME_IGNORE_WORDS
            ]
            if long_tokens:
                score += 40
            else:
                score += 15
        else:
            score += 15
    return min(score, 100)


# ─────────────────────────────────────────────────────────
# HTML Extractors
# ─────────────────────────────────────────────────────────

def extract_emails_from_html(
    html: str,
    domain: str         = None,
    business_name: str  = None,
    source: str         = "unknown",
    min_confidence: int = 40,
) -> List[str]:
    if not html:
        return []
    pattern = (
        r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+'
        r'\.[a-zA-Z]{2,})\b'
    )
    raw_emails    = re.findall(pattern, html)
    seen: set     = set()
    unique_emails = []
    for e in raw_emails:
        e_lower = e.lower()
        if e_lower not in seen:
            seen.add(e_lower)
            unique_emails.append(e_lower)
    website = ("https://" + domain) if domain else None
    scored  = []
    for email in unique_emails:
        if is_junk_email(email):
            continue
        confidence = score_email_confidence(
            email, business_name or "", website, source,
        )
        if confidence >= min_confidence:
            scored.append((email, confidence))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [email for email, _ in scored]


def extract_phones_from_html(html: str) -> List[str]:
    if not html:
        return []
    patterns = [
        r'0[12]\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
        r'\(0\d{2,4}\)\s?\d{3,4}[\s\-]?\d{3,4}',
        r'07\d{3}[\s\-]?\d{6}',
        r'\+44[\s\-]?\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
        r'\+1[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}',
        r'\+234[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4}',
        r'0[789]\d{9}',
        r'\+\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}',
    ]
    found       = []
    seen_digits: set = set()
    for pattern in patterns:
        for phone in re.findall(pattern, html):
            digits = re.sub(r"\D", "", phone)
            if (
                10 <= len(digits) <= 15
                and digits not in seen_digits
            ):
                seen_digits.add(digits)
                found.append(phone.strip())
    return found


def extract_phones_from_tel_links(
    soup: BeautifulSoup,
) -> List[str]:
    phones      = []
    seen_digits: set = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if href.startswith("tel:"):
            phone  = href.replace("tel:", "").strip()
            digits = re.sub(r"\D", "", phone)
            if (
                9 <= len(digits) <= 15
                and digits not in seen_digits
            ):
                seen_digits.add(digits)
                phones.append(phone)
    return phones


def extract_emails_from_mailto_links(
    soup: BeautifulSoup,
) -> List[str]:
    emails    = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if href.startswith("mailto:"):
            email = (
                href.replace("mailto:", "")
                .split("?")[0].strip().lower()
            )
            if "@" in email and email not in seen:
                seen.add(email)
                emails.append(email)
    return emails


def extract_from_json_ld(soup: BeautifulSoup) -> Dict:
    result: Dict[str, Any] = {
        "email": None, "phone": None, "address": None,
    }
    for script in soup.find_all(
        "script", type="application/ld+json"
    ):
        try:
            raw = script.string or ""
            if not raw.strip():
                continue
            data  = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if not result["email"]:
                    email = item.get("email", "")
                    if email and "@" in email:
                        result["email"] = email.lower()
                if not result["phone"]:
                    phone = item.get(
                        "telephone", ""
                    ) or item.get("phone", "")
                    if phone:
                        result["phone"] = str(phone).strip()
                if not result["address"]:
                    addr = item.get("address", {})
                    if isinstance(addr, dict):
                        parts = [
                            addr.get("streetAddress", ""),
                            addr.get("addressLocality", ""),
                            addr.get("addressRegion", ""),
                            addr.get("postalCode", ""),
                        ]
                        address_str = ", ".join(
                            p for p in parts if p
                        )
                        if address_str:
                            result["address"] = address_str
        except Exception:
            continue
    return result


def extract_social_links(html: str) -> Dict:
    social: Dict[str, Any] = {
        "social_facebook_url":  None,
        "social_instagram_url": None,
        "social_linkedin_url":  None,
    }
    patterns = {
        "social_instagram_url": (
            r'https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+/?'
        ),
        "social_facebook_url": (
            r'https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.]+/?'
        ),
        "social_linkedin_url": (
            r'https?://(?:www\.)?linkedin\.com/'
            r'(?:company|in)/[A-Za-z0-9_\-]+/?'
        ),
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            social[field] = match.group(0)
    return social


# ─────────────────────────────────────────────────────────
# Decision Maker Extraction from HTML
# ─────────────────────────────────────────────────────────

DM_TITLE_PATTERNS = re.compile(
    r'\b(?:Registered\s+Manager|Care\s+Home\s+Manager|'
    r'Nominated\s+Individual|Deputy\s+Manager|Unit\s+Manager|'
    r'Clinical\s+Lead|Matron|HR\s+Manager|Recruitment\s+Lead|'
    r'Director|Owner|Proprietor|Managing\s+Director|MD|CEO|'
    r'Founder|Head|CTO|CFO|COO|Co-Founder|Principal|President|'
    r'Chairman|Chairwoman|Executive\s+Director)\b',
    re.IGNORECASE,
)

NAME_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z\'\-]{1,20}"
    r"(?:\s+[A-Z][a-zA-Z\'\-]{1,20}){1,2})\b"
)
NAME_PATTERN_ALLCAPS = re.compile(
    r'\b([A-Z]{2,20}\s+[A-Z]{2,20})\b'
)


def _extract_decision_maker_from_html(
    html: str, soup: BeautifulSoup
) -> str:
    if not html or not soup:
        return ""
    text      = soup.get_text(separator=" ", strip=True)
    sentences = re.split(r'[.\n,;|]', text)
    for sentence in sentences:
        if DM_TITLE_PATTERNS.search(sentence):
            for name in NAME_PATTERN.findall(sentence):
                combined = name.strip()
                if _is_valid_name(combined):
                    cleaned = clean_decision_maker_name(combined)
                    if cleaned:
                        return cleaned
            for name in NAME_PATTERN_ALLCAPS.findall(sentence):
                combined = name.strip()
                if _is_valid_name(combined):
                    cleaned = clean_decision_maker_name(combined)
                    if cleaned:
                        return cleaned
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author:
        content = meta_author.get("content", "").strip()
        if content and 3 < len(content) < 60:
            cleaned = clean_decision_maker_name(content)
            if cleaned:
                return cleaned
    return ""


def _is_valid_name(name: str) -> bool:
    if not name or len(name) < 4:
        return False
    lower   = name.lower()
    invalid = {
        "team", "staff", "home", "care", "manager",
        "contact", "about", "services", "welcome",
        "phone", "email",
    }
    return lower not in invalid and not any(
        w in lower for w in invalid
    )


def _extract_decision_maker_from_website(
    website_url: str,
    business_name: str,
    crawl_budget: List[int],
) -> str:
    if not website_url or crawl_budget[0] <= 0:
        return ""
    if not website_url.startswith("http"):
        website_url = "https://" + website_url
    try:
        parsed = urlparse(website_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return ""

    paths = [
        "/about", "/about-us", "/team", "/our-team",
        "/management", "/staff",
    ]
    for path in paths:
        if crawl_budget[0] <= 0:
            break
        url = urljoin(base, path)
        try:
            human_pause(0.4, 0.9)
            crawl_budget[0] -= 1
            html, soup = crawl_page(url)
            if not html or not soup:
                continue
            name = _extract_decision_maker_from_html(html, soup)
            if name:
                return name
        except Exception:
            continue
    return ""


def _get_parser() -> str:
    try:
        import lxml  # noqa: F401
        return "lxml"
    except ImportError:
        return "html.parser"


def crawl_page(
    url: str, timeout: int = 10
) -> Tuple[Optional[str], Optional[BeautifulSoup]]:
    try:
        resp = requests.get(
            url,
            headers=get_headers(),
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code < 400:
            parser = _get_parser()
            soup   = BeautifulSoup(resp.text, parser)
            return resp.text, soup
    except Exception:
        pass
    return None, None


def get_deep_contact_paths() -> List[str]:
    return [
        "", "/contact", "/contact-us",
        "/about", "/about-us", "/team",
    ]


def find_email_on_website(
    website_url: str, business_name: str = ""
) -> Optional[str]:
    if not website_url:
        return None
    if not website_url.startswith("http"):
        website_url = "https://" + website_url
    try:
        parsed = urlparse(website_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        domain = parsed.netloc.replace("www.", "")
        paths  = get_deep_contact_paths()
        pages  = [urljoin(base, path) for path in paths]
        if website_url not in pages:
            pages.insert(0, website_url)
        for page_url in pages:
            try:
                human_pause(0.2, 0.5)
                html, soup = crawl_page(page_url)
                if not html or not soup:
                    continue
                for email in extract_emails_from_mailto_links(soup):
                    if not is_junk_email(email):
                        return email
                structured = extract_from_json_ld(soup)
                if structured.get("email"):
                    email = structured["email"]
                    if not is_junk_email(email):
                        return email
                emails = extract_emails_from_html(
                    html,
                    domain=domain,
                    business_name=business_name,
                    source="website",
                    min_confidence=40,
                )
                if emails:
                    return emails[0]
            except Exception:
                continue
    except Exception:
        pass
    return None


def find_phone_on_website(website_url: str) -> Optional[str]:
    if not website_url:
        return None
    if not website_url.startswith("http"):
        website_url = "https://" + website_url
    try:
        parsed = urlparse(website_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        pages  = [
            website_url,
            urljoin(base, "/contact"),
            urljoin(base, "/contact-us"),
        ]
        for page_url in pages:
            try:
                human_pause(0.2, 0.5)
                html, soup = crawl_page(page_url)
                if not html or not soup:
                    continue
                tel_phones = extract_phones_from_tel_links(soup)
                if tel_phones:
                    return tel_phones[0]
                structured = extract_from_json_ld(soup)
                if structured.get("phone"):
                    return structured["phone"]
                phones = extract_phones_from_html(html)
                if phones:
                    return phones[0]
            except Exception:
                continue
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────
# Website Health
# ─────────────────────────────────────────────────────────

def check_website_health(url: str, timeout: int = 10) -> Dict:
    result: Dict[str, Any] = {
        "active":       False,
        "status_code":  None,
        "redirect_url": None,
        "has_ssl":      False,
        "load_time":    None,
        "error":        None,
    }
    if not url:
        return result
    if not url.startswith("http"):
        url = "https://" + url
    result["has_ssl"] = url.startswith("https")
    try:
        start   = time.time()
        resp    = requests.get(
            url,
            headers=get_headers(),
            timeout=timeout,
            allow_redirects=True,
        )
        elapsed = time.time() - start
        result["status_code"] = resp.status_code
        result["active"]      = resp.status_code < 400
        result["load_time"]   = round(elapsed, 2)
        if resp.url != url:
            result["redirect_url"] = resp.url
    except requests.exceptions.SSLError:
        try:
            http_url = url.replace("https://", "http://")
            resp     = requests.get(
                http_url,
                headers=get_headers(),
                timeout=timeout,
                allow_redirects=True,
            )
            result["status_code"] = resp.status_code
            result["active"]      = resp.status_code < 400
        except Exception as exc:
            result["error"] = str(exc)
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ─────────────────────────────────────────────────────────
# Search Engine Finders
# BUG-RE-07: min_confidence raised to 60 for all search HTML
# ─────────────────────────────────────────────────────────

def search_email_duckduckgo(
    business_name: str,
    location: str = "",
    website: str  = None,
) -> Optional[str]:
    if not business_name:
        return None
    if DDGS_AVAILABLE and DDGS_CLASS is not None:
        try:
            query = (
                f'"{business_name}" {location} email'.strip()
            )
            with DDGS_CLASS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
                for r in results:
                    emails = extract_emails_from_html(
                        r.get("body", ""),
                        business_name=business_name,
                        source="search",
                        min_confidence=60,  # BUG-RE-07: was 40
                    )
                    for email in emails:
                        if (
                            not is_junk_email(email)
                            and not is_foreign_email(email)
                        ):
                            logger.info("DDGS email: %s", email)
                            return email
        except Exception as exc:
            logger.debug("DDGS email search: %s", exc)
    try:
        query = (
            f'"{business_name}" {location} email'.strip()
        )
        url   = (
            "https://html.duckduckgo.com/html/?q="
            + query.replace(" ", "+")
        )
        human_pause(0.8, 1.5)
        resp = requests.get(
            url, headers=get_headers(), timeout=15,
        )
        if resp.status_code == 200:
            emails = extract_emails_from_html(
                resp.text,
                business_name=business_name,
                source="search",
                min_confidence=60,  # BUG-RE-07: was 40
            )
            for email in emails:
                if (
                    not is_junk_email(email)
                    and not is_foreign_email(email)
                ):
                    logger.info("DDG email: %s", email)
                    return email
    except Exception as exc:
        logger.debug("search_email_duckduckgo: %s", exc)
    return None


def search_email_bing(
    business_name: str,
    location: str = "",
    website: str  = None,
) -> Optional[str]:
    if not business_name:
        return None
    try:
        query = (
            f'"{business_name}" {location} '
            f'email OR contact'.strip()
        )
        url   = (
            "https://www.bing.com/search?q="
            + query.replace(" ", "+")
        )
        human_pause(0.8, 1.5)
        resp = requests.get(
            url, headers=get_headers(), timeout=15,
        )
        if resp.status_code == 200:
            # BUG-RE-07: raised from 40 to 60 for search HTML
            emails = extract_emails_from_html(
                resp.text,
                business_name=business_name,
                source="search",
                min_confidence=60,  # BUG-RE-07: was 40
            )
            for email in emails:
                if (
                    not is_junk_email(email)
                    and not is_foreign_email(email)
                ):
                    logger.info("Bing email: %s", email)
                    return email
    except Exception as exc:
        logger.debug("search_email_bing: %s", exc)
    return None


def search_phone_duckduckgo(
    business_name: str, location: str = ""
) -> Optional[str]:
    if not business_name:
        return None
    if DDGS_AVAILABLE and DDGS_CLASS is not None:
        try:
            query = (
                f'"{business_name}" {location} '
                f'phone number'.strip()
            )
            with DDGS_CLASS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
                for r in results:
                    phones = extract_phones_from_html(
                        r.get("body", "")
                    )
                    if phones:
                        return phones[0]
        except Exception as exc:
            logger.debug("DDGS phone search: %s", exc)
    try:
        query = (
            f'"{business_name}" {location} '
            f'phone number'.strip()
        )
        url   = (
            "https://html.duckduckgo.com/html/?q="
            + query.replace(" ", "+")
        )
        human_pause(0.8, 1.5)
        resp = requests.get(
            url, headers=get_headers(), timeout=15,
        )
        if resp.status_code == 200:
            phones = extract_phones_from_html(resp.text)
            if phones:
                return phones[0]
    except Exception as exc:
        logger.debug("search_phone_duckduckgo: %s", exc)
    return None


# ─────────────────────────────────────────────────────────
# Website Discovery
# ─────────────────────────────────────────────────────────

def discover_business_website_duckduckgo(
    business_name: str,
    location: str = "",
    niche: str    = "",
    postcode: str = "",
) -> str:
    if not DDGS_AVAILABLE or DDGS_CLASS is None:
        return ""
    care_excludes = ""
    if "care" in niche.lower():
        care_excludes = (
            " -site:carehome.co.uk -site:cqc.org.uk"
            " -site:caring.com -site:homecare.co.uk"
            " -site:autumna.co.uk -site:lottie.org"
            " -site:nhs.uk -site:yell.com"
            " -site:housingcare.org -site:facebook.com"
        )
    queries = []
    if postcode and postcode.strip():
        queries.append(
            f'"{business_name}" {postcode.strip()}'
            f'{care_excludes}'
        )
    if location:
        queries.append(
            f'"{business_name}" {location}{care_excludes}'
        )
    queries.append(
        f'"{business_name}" official website{care_excludes}'
    )
    tokens = business_name_tokens(business_name)
    for query in queries:
        try:
            with DDGS_CLASS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
            for r in results:
                url = r.get("href", "")
                if not url:
                    continue
                domain = normalize_domain(url)
                if not domain:
                    continue
                if is_bad_domain(domain, niche):
                    continue
                dom_score = score_domain_relevance(
                    domain, business_name,
                )
                if dom_score > 0:
                    return "https://" + domain
                url_lower  = url.lower()
                path_match = any(
                    len(t) >= 5 and t in url_lower
                    for t in tokens
                )
                if path_match:
                    return "https://" + domain
        except Exception as exc:
            logger.debug("DDGS query failed: %s", exc)
            continue
    return ""


def discover_business_website_bing(
    business_name: str,
    location: str = "",
    niche: str    = "",
) -> str:
    if "care" in niche.lower():
        query = (
            f'"{business_name}" {location}'
            " -site:carehome.co.uk -site:cqc.org.uk"
            " -site:caring.com -site:homecare.co.uk"
        ).strip()
    else:
        query = (
            f'"{business_name}" {location} '
            f'official website'.strip()
        )
    try:
        url  = (
            "https://www.bing.com/search?q="
            + query.replace(" ", "+")
        )
        human_pause(0.8, 1.5)
        resp = requests.get(
            url, headers=get_headers(), timeout=15,
        )
        if resp.status_code != 200:
            return ""
        domains = extract_domains_from_html(resp.text)
        scored  = []
        for domain in domains:
            if is_bad_domain(domain, niche):
                continue
            s = score_domain_relevance(domain, business_name)
            if s > 0:
                scored.append((domain, s))
        if not scored:
            return ""
        scored.sort(key=lambda x: x[1], reverse=True)
        return "https://" + scored[0][0]
    except Exception as exc:
        logger.debug("discover_website_bing: %s", exc)
        return ""


def discover_business_website(
    business_name: str,
    location: str = "",
    niche: str    = "",
    postcode: str = "",
) -> Tuple[str, str]:
    """Always returns (url, source) tuple — never raises."""
    try:
        website = discover_business_website_duckduckgo(
            business_name, location, niche, postcode=postcode,
        )
        if website:
            return website, "duckduckgo"
        website = discover_business_website_bing(
            business_name, location, niche,
        )
        if website:
            return website, "bing"
        if postcode and postcode.strip():
            website = discover_business_website_bing(
                business_name, postcode.strip(), niche,
            )
            if website:
                return website, "bing_postcode"
    except Exception as exc:
        logger.debug("discover_business_website: %s", exc)
    return "", ""


# ─────────────────────────────────────────────────────────
# Multi-Source Public Finders
# ─────────────────────────────────────────────────────────

def find_email_multi_source(
    business_name: str,
    website: str      = None,
    location: str     = "",
    progress_callback = None,
) -> Optional[str]:
    if website:
        if progress_callback:
            progress_callback("Checking website...")
        email = find_email_on_website(website, business_name)
        if email:
            return email
    if progress_callback:
        progress_callback("Searching DDGS...")
    email = search_email_duckduckgo(business_name, location, website)
    if email:
        return email
    if progress_callback:
        progress_callback("Searching Bing...")
    email = search_email_bing(business_name, location, website)
    if email:
        return email
    return None


def find_phone_multi_source(
    business_name: str,
    website: str  = None,
    location: str = "",
) -> Optional[str]:
    if website:
        phone = find_phone_on_website(website)
        if phone:
            return phone
    phone = search_phone_duckduckgo(business_name, location)
    if phone:
        return phone
    return None


# ─────────────────────────────────────────────────────────
# BUG-016: CQC context guards
# ─────────────────────────────────────────────────────────

def _is_uk_context(country: str) -> bool:
    if not country:
        return False
    c = country.strip().lower()
    return c in {
        "uk", "united kingdom", "england", "gb",
        "great britain", "wales", "scotland", "northern ireland",
    }


def _is_care_context(niche: str) -> bool:
    if not niche:
        return False
    n = niche.lower()
    return "care" in n or "nursing" in n or "residential" in n


def _enrich_from_cqc(lead: Dict) -> int:
    if not CQC_AVAILABLE:
        return 0
    country = str(lead.get("country") or "")
    niche   = str(
        lead.get("niche") or lead.get("industry") or ""
    )
    if not _is_uk_context(country):
        return 0
    if not _is_care_context(niche):
        return 0
    fields_added = 0
    try:
        from engine.uk_care_enricher import (  # noqa
            enrich_uk_care_home_lead,
        )
        old_dm    = lead.get("decision_maker_name")
        old_email = lead.get("email")
        old_phone = lead.get("phone")
        old_ch    = lead.get("company_registration_number")
        lead.update(enrich_uk_care_home_lead(dict(lead)))
        if (
            lead.get("decision_maker_name")
            and lead.get("decision_maker_name") != old_dm
        ):
            cleaned = clean_decision_maker_name(
                lead["decision_maker_name"]
            )
            lead["decision_maker_name"] = cleaned or old_dm
            if cleaned:
                lead["dm_source"] = "cqc_v10.2"
                fields_added += 1
        if (
            lead.get("email")
            and lead.get("email") != old_email
        ):
            fields_added += 1
        if (
            lead.get("phone")
            and lead.get("phone") != old_phone
        ):
            fields_added += 1
        if (
            lead.get("company_registration_number")
            and lead.get("company_registration_number") != old_ch
        ):
            fields_added += 1
        return fields_added
    except Exception as exc:
        logger.warning("CQC enrichment exception: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────
# B2BHint & Companies House Deep Officer Hook
# ─────────────────────────────────────────────────────────

def _enrich_from_b2bhint_and_ch(lead: Dict) -> int:
    fields_added = 0
    try:
        notes   = str(lead.get("enrichment_notes") or "")
        b2b_url = ""
        if "b2bhint_detail:" in notes:
            for part in notes.split("|"):
                if "b2bhint_detail:" in part:
                    b2b_url = part.split(
                        "b2bhint_detail:"
                    )[1].strip()
                    break
        elif lead.get("source_url") and "b2bhint.com" in str(
            lead.get("source_url")
        ):
            b2b_url = str(lead.get("source_url")).strip()

        if b2b_url and not lead.get("decision_maker_name"):
            try:
                from engine.b2bhint_scraper import (  # noqa
                    scrape_b2bhint_company_page,
                )
                deep_info = scrape_b2bhint_company_page(b2b_url)
                if deep_info:
                    raw_dm = deep_info.get(
                        "decision_maker_name", ""
                    )
                    if raw_dm and not lead.get(
                        "decision_maker_name"
                    ):
                        cleaned = clean_decision_maker_name(raw_dm)
                        if cleaned:
                            lead["decision_maker_name"] = cleaned
                            lead["decision_maker_title"] = (
                                deep_info.get(
                                    "decision_maker_title",
                                    "Director / Owner",
                                )
                            )
                            lead["dm_source"] = "b2bhint_deep"
                            fields_added += 1
                    if deep_info.get(
                        "company_registration_number"
                    ) and not lead.get(
                        "company_registration_number"
                    ):
                        lead["company_registration_number"] = (
                            deep_info["company_registration_number"]
                        )
                        fields_added += 1
                    if deep_info.get("email") and not lead.get(
                        "email"
                    ):
                        lead["email"] = deep_info["email"]
                        fields_added += 1
                    if deep_info.get("phone") and not lead.get(
                        "phone"
                    ):
                        lead["phone"] = deep_info["phone"]
                        fields_added += 1
                    if deep_info.get("website") and not lead.get(
                        "website_url"
                    ):
                        lead["website_url"] = deep_info["website"]
                        fields_added += 1
            except Exception as exc:
                logger.debug("B2BHint deep enrich error: %s", exc)

        ch_num = str(
            lead.get("company_registration_number")
            or lead.get("company_number")
            or ""
        ).strip()
        country      = str(
            lead.get("country") or ""
        ).strip().lower()
        uk_countries = {
            "uk", "united kingdom", "england",
            "scotland", "wales", "gb", "great britain",
        }
        if ch_num and country in uk_countries:
            if not lead.get(
                "decision_maker_name"
            ) or lead.get(
                "decision_maker_name"
            ) in GENERIC_MANAGER_VALUES:
                try:
                    from engine.companies_house_scraper import (  # noqa
                        get_company_officers,
                        title_name,
                    )
                    officers = get_company_officers(ch_num)
                    if officers:
                        for off in officers:
                            if off.get(
                                "resigned_on"
                            ) or "resigned" in str(
                                off.get("status", "")
                            ).lower():
                                continue
                            role = str(
                                off.get("role") or ""
                            ).lower()
                            if any(
                                r in role
                                for r in [
                                    "director",
                                    "managing-director",
                                    "owner", "proprietor",
                                    "partner", "secretary",
                                ]
                            ):
                                name = str(
                                    off.get("name") or ""
                                ).strip()
                                if name:
                                    raw_tn  = title_name(name)
                                    cleaned = (
                                        clean_decision_maker_name(
                                            raw_tn
                                        )
                                    )
                                    if cleaned:
                                        lead[
                                            "decision_maker_name"
                                        ] = cleaned
                                        lead[
                                            "decision_maker_title"
                                        ] = off.get(
                                            "role", "Director"
                                        ).title()
                                        lead["dm_source"] = (
                                            "companies_house"
                                        )
                                        fields_added += 1
                                        break
                except Exception as exc:
                    logger.debug(
                        "CH officer lookup error: %s", exc,
                    )
        return fields_added
    except Exception as exc:
        logger.warning(
            "B2BHint/CH enrichment exception: %s", exc,
        )
        return 0


# ─────────────────────────────────────────────────────────
# Personal Email Helper
# ─────────────────────────────────────────────────────────

def _generate_personal_email(
    manager_name: str,
    website_url: str,
    location_id: str = "",
) -> str:
    if not manager_name or not website_url:
        return ""
    try:
        from engine.email_finder import find_personal_email  # noqa
        email = find_personal_email(
            full_name=manager_name,
            website_url=website_url,
            location_id=location_id,
        )
        return email or ""
    except Exception as exc:
        logger.debug(
            "_generate_personal_email('%s'): %s",
            manager_name, exc,
        )
        return ""


# ─────────────────────────────────────────────────────────
# Social URL Discovery
# ─────────────────────────────────────────────────────────

def _discover_social_urls_via_search(
    business_name: str, location: str = ""
) -> Dict:
    social_results: Dict[str, Any] = {
        "social_facebook_url":  None,
        "social_instagram_url": None,
        "social_linkedin_url":  None,
    }
    platforms = [
        ("social_facebook_url",  "site:facebook.com"),
        ("social_instagram_url", "site:instagram.com"),
        ("social_linkedin_url",  "site:linkedin.com/company"),
    ]
    for field, site_op in platforms:
        try:
            query = (
                f'{site_op} "{business_name}" {location}'.strip()
            )
            if DDGS_AVAILABLE and DDGS_CLASS is not None:
                try:
                    with DDGS_CLASS() as ddgs:
                        results = list(
                            ddgs.text(query, max_results=5)
                        )
                        for r in results:
                            url = r.get("href", "")
                            platform_domain = (
                                site_op.replace("site:", "")
                                .split("/")[0]
                            )
                            if platform_domain in url:
                                path = (
                                    urlparse(url).path.strip("/")
                                )
                                if (
                                    path
                                    and len(path) > 2
                                    and "search" not in path
                                ):
                                    social_results[field] = url
                                    break
                        if social_results[field]:
                            continue
                except Exception:
                    pass
            url  = (
                "https://html.duckduckgo.com/html/?q="
                + query.replace(" ", "+")
            )
            human_pause(0.5, 1.0)
            resp = requests.get(
                url, headers=get_headers(), timeout=10,
            )
            if resp.status_code != 200:
                continue
            platform_domain = (
                site_op.replace("site:", "").split("/")[0]
            )
            pattern = (
                r'https?://(?:www\.)?'
                + re.escape(platform_domain)
                + r'/[A-Za-z0-9_./-]+'
            )
            matches = re.findall(
                pattern, resp.text, re.IGNORECASE,
            )
            for match in matches:
                path = urlparse(match).path.strip("/")
                if (
                    path
                    and len(path) > 2
                    and "search" not in path
                ):
                    social_results[field] = match
                    break
        except Exception as exc:
            logger.debug(
                "_discover_social_urls_via_search %s: %s",
                field, exc,
            )
    return social_results


# ─────────────────────────────────────────────────────────
# MAIN ENRICHMENT PIPELINE — v10.2
# ─────────────────────────────────────────────────────────

def enrich_lead(
    lead: Dict,
    find_email: bool   = True,
    find_phone: bool   = True,
    pre_existing: Dict = None,
) -> Dict:
    """
    Full lead enrichment pipeline — v10.2
    """
    if not lead:
        return lead

    working = dict(lead)
    if pre_existing:
        working = safe_merge(pre_existing, working)

    business_name = working.get("business_name", "")
    city          = (
        working.get("city") or working.get("location") or ""
    )
    country       = working.get("country") or ""
    postcode      = (working.get("postcode") or "").strip()
    location      = ", ".join(p for p in [city, country] if p)
    niche         = str(
        working.get("niche") or working.get("industry") or ""
    ).lower()

    step_errors: List[str] = []
    fields_improved        = 0
    crawl_budget           = [8]

    def _run_step(
        step_name: str, func, *args, **kwargs
    ) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            err_msg = f"{step_name}: {exc}"
            logger.warning(
                "Enrichment step failed: %s", err_msg,
            )
            step_errors.append(err_msg)
            return _STEP_EXCEPTION

    # ── Step 0a: CQC ──────────────────────────────────────────────
    cqc_result = _run_step("CQC", _enrich_from_cqc, working)
    if (
        cqc_result not in (None, _STEP_EXCEPTION)
        and cqc_result > 0
    ):
        fields_improved += cqc_result

    # ── Step 0b: B2BHint / CH deep ────────────────────────────────
    b2b_result = _run_step(
        "B2BHint/CH", _enrich_from_b2bhint_and_ch, working,
    )
    if (
        b2b_result not in (None, _STEP_EXCEPTION)
        and b2b_result > 0
    ):
        fields_improved += b2b_result

    # ── Step 1: Website discovery ─────────────────────────────────
    website = (working.get("website") or "").strip()

    if not website or website.lower() in ("", "none", "null"):
        raw_result = _run_step(
            "WebsiteDiscovery",
            discover_business_website,
            business_name,
            location,
            niche=niche,
            postcode=postcode,
        )
        discovered_url, discovered_from = safe_unpack(
            raw_result, ("", ""),
        )
        if (
            discovered_url
            and _validate_website_url(discovered_url)
        ):
            working["website"]                      = discovered_url
            working["website_discovered"]           = 1
            working["website_discovery_source"]     = discovered_from
            working["website_discovery_confidence"] = (
                "high"
                if discovered_from == "duckduckgo"
                else "medium"
            )
            fields_improved += 1
        else:
            working["website_discovered"]           = 0
            working["website_discovery_source"]     = "not_found"
            working["website_discovery_confidence"] = "low"
    else:
        if not working.get("website_discovered"):
            working["website_discovered"] = 0

    # NEW-02: Re-read after Step 1
    website = (working.get("website") or "").strip()
    if website and not website.startswith("http"):
        website            = "https://" + website
        working["website"] = website

    website_is_valid = (
        bool(website) and _validate_website_url(website)
    )

    # ── Step 2: Personal email from existing manager ───────────────
    if (
        find_email
        and not working.get("email", "").strip()
        and website_is_valid
    ):
        manager_name = working.get(
            "decision_maker_name", ""
        ).strip()
        loc_id = working.get("cqc_location_id", "").strip()
        if (
            manager_name
            and manager_name not in GENERIC_MANAGER_VALUES
        ):
            personal_email = _run_step(
                "PersonalEmail",
                _generate_personal_email,
                manager_name,
                website,
                loc_id,
            )
            if (
                personal_email not in (None, _STEP_EXCEPTION)
                and personal_email
            ):
                working["email"]            = personal_email
                working["email_confidence"] = 65
                fields_improved += 1

    # ── Step 3: Website crawl ──────────────────────────────────────
    if website_is_valid:
        health = _run_step(
            "WebsiteHealth", check_website_health, website,
        )
        site_active = (
            health not in (None, _STEP_EXCEPTION)
            and isinstance(health, dict)
            and health.get("active")
        )
        if site_active:
            if crawl_budget[0] > 0:
                crawl_budget[0] -= 1
                crawl_result = _run_step(
                    "CrawlHomepage", crawl_page, website,
                )
                if crawl_result not in (None, _STEP_EXCEPTION):
                    html_content, soup = crawl_result
                else:
                    html_content, soup = None, None
            else:
                html_content, soup = None, None

            if html_content and soup:
                # Social links
                social_from_html = extract_social_links(
                    html_content
                )
                for field, url_val in social_from_html.items():
                    if url_val and not working.get(field):
                        working[field] = url_val
                        fields_improved += 1

                # Decision maker from homepage
                if not working.get(
                    "decision_maker_name", ""
                ).strip():
                    dm_name = _run_step(
                        "DMFromHTML",
                        _extract_decision_maker_from_html,
                        html_content,
                        soup,
                    )
                    if (
                        dm_name in (None, _STEP_EXCEPTION)
                        or not dm_name
                    ):
                        dm_name = _run_step(
                            "DMFromWebsite",
                            _extract_decision_maker_from_website,
                            website,
                            business_name,
                            crawl_budget,
                        )
                    if (
                        dm_name not in (None, _STEP_EXCEPTION)
                        and dm_name
                    ):
                        cleaned_dm = clean_decision_maker_name(
                            dm_name
                        )
                        if cleaned_dm:
                            working["decision_maker_name"] = (
                                cleaned_dm
                            )
                            working["dm_source"] = "website"
                            fields_improved += 1
                            if (
                                find_email
                                and not working.get(
                                    "email", ""
                                ).strip()
                            ):
                                personal_email = _run_step(
                                    "PersonalEmailFromDM",
                                    _generate_personal_email,
                                    cleaned_dm,
                                    website,
                                )
                                if (
                                    personal_email
                                    not in (None, _STEP_EXCEPTION)
                                    and personal_email
                                ):
                                    working["email"] = (
                                        personal_email
                                    )
                                    working["email_confidence"] = 60
                                    fields_improved += 1

                # Phone from homepage
                if find_phone and not working.get("phone"):
                    tel_phones = extract_phones_from_tel_links(
                        soup
                    )
                    if tel_phones:
                        working["phone"]            = tel_phones[0]
                        working["phone_confidence"] = 90
                        fields_improved += 1
                    else:
                        structured = extract_from_json_ld(soup)
                        if structured.get("phone"):
                            working["phone"] = structured["phone"]
                            working["phone_confidence"] = 85
                            fields_improved += 1
                        else:
                            phones = extract_phones_from_html(
                                html_content
                            )
                            if phones:
                                working["phone"]            = phones[0]
                                working["phone_confidence"] = 60
                                fields_improved += 1

                # Email from homepage
                if find_email and not working.get("email"):
                    for email in extract_emails_from_mailto_links(
                        soup
                    ):
                        if not is_junk_email(email):
                            working["email"]            = email
                            working["email_confidence"] = 95
                            fields_improved += 1
                            break
                    if not working.get("email"):
                        structured = extract_from_json_ld(soup)
                        if structured.get(
                            "email"
                        ) and not is_junk_email(
                            structured["email"]
                        ):
                            working["email"] = structured["email"]
                            working["email_confidence"] = 90
                            fields_improved += 1
                    if not working.get("email"):
                        domain = urlparse(
                            website
                        ).netloc.replace("www.", "")
                        emails = extract_emails_from_html(
                            html_content,
                            domain=domain,
                            business_name=business_name,
                            source="website",
                            min_confidence=40,
                        )
                        if emails:
                            working["email"]            = emails[0]
                            working["email_confidence"] = 75
                            fields_improved += 1
        else:
            status_code = (
                health.get("status_code", "unknown")
                if isinstance(health, dict)
                else "check_failed"
            )
            step_errors.append(
                f"Website inactive ({status_code})"
            )

    # ── Step 4: Companies House director fallback (UK only) ────────
    # FIX-CH-COUNTRY-01: country now passed through so
    # _lookup_companies_house_director() can hard-gate to UK leads
    # only — see function docstring for the Dallas contamination
    # this was causing before the fix.
    if not working.get("decision_maker_name", "").strip():
        ch_name = _run_step(
            "CompaniesHouse",
            _lookup_companies_house_director,
            business_name,
            postcode,
            country,
        )
        if ch_name not in (None, _STEP_EXCEPTION) and ch_name:
            working["decision_maker_name"] = ch_name
            working["dm_source"]           = "companies_house"
            fields_improved += 1
            if (
                find_email
                and not working.get("email", "").strip()
                and website_is_valid
            ):
                personal_email = _run_step(
                    "PersonalEmailFromCH",
                    _generate_personal_email,
                    ch_name,
                    website,
                )
                if (
                    personal_email not in (None, _STEP_EXCEPTION)
                    and personal_email
                ):
                    working["email"]            = personal_email
                    working["email_confidence"] = 55
                    fields_improved += 1

    # ── Step 5: Social search ──────────────────────────────────────
    has_all_social = (
        working.get("social_facebook_url")
        and working.get("social_instagram_url")
        and working.get("social_linkedin_url")
    )
    if not has_all_social and business_name:
        social_from_search = _run_step(
            "SocialSearch",
            _discover_social_urls_via_search,
            business_name,
            location,
        )
        if (
            social_from_search not in (None, _STEP_EXCEPTION)
            and isinstance(social_from_search, dict)
        ):
            for field, url_val in social_from_search.items():
                if url_val and not working.get(field):
                    working[field] = url_val
                    fields_improved += 1

    # ── Step 6: Phone search fallback ─────────────────────────────
    if find_phone and not working.get("phone"):
        phone = _run_step(
            "PhoneSearch",
            search_phone_duckduckgo,
            business_name,
            location,
        )
        if phone not in (None, _STEP_EXCEPTION) and phone:
            working["phone"]            = phone
            working["phone_confidence"] = 50
            fields_improved += 1

    # ── Step 7: Email search fallback ─────────────────────────────
    if find_email and not working.get("email"):
        email = _run_step(
            "EmailSearch",
            find_email_multi_source,
            business_name=business_name,
            website=website or None,
            location=location,
        )
        if email not in (None, _STEP_EXCEPTION) and email:
            working["email"] = email
            working["email_confidence"] = (
                working.get("email_confidence") or 50
            )
            fields_improved += 1

    # ── Step 8: dm_contact_finder fallback ────────────────────────
    if find_email and not working.get("email") and website_is_valid:
        try:
            from engine.dm_contact_finder import (  # noqa
                find_dm_contact,
            )
            dm_result = _run_step(
                "DMContactFinder",
                find_dm_contact,
                website_url=website,
                business_name=business_name,
                manager_name=working.get(
                    "decision_maker_name", ""
                ),
            )
            if (
                dm_result not in (None, _STEP_EXCEPTION)
                and isinstance(dm_result, dict)
            ):
                if dm_result.get("email"):
                    working["email"] = dm_result["email"]
                    working["email_confidence"] = dm_result.get(
                        "confidence", 50,
                    )
                    fields_improved += 1
                if dm_result.get("name") and not working.get(
                    "decision_maker_name", ""
                ).strip():
                    cleaned_dm = clean_decision_maker_name(
                        dm_result["name"]
                    )
                    if cleaned_dm:
                        working["decision_maker_name"] = cleaned_dm
                        working["dm_source"] = "dm_contact_finder"
                        fields_improved += 1
        except ImportError:
            pass
        except Exception as exc:
            logger.debug(
                "dm_contact_finder outer error: %s", exc,
            )

    # ── Step 9: Final safe_merge ───────────────────────────────────
    final = safe_merge(lead, working)
    if pre_existing:
        final = safe_merge(pre_existing, final)

    # ── Step 10: Status + meta ─────────────────────────────────────
    if fields_improved > 0:
        final["enrichment_status"] = "complete"
    elif step_errors:
        final["enrichment_status"] = "failed"
    else:
        final["enrichment_status"] = "attempted"

    final["last_enriched_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    if step_errors:
        final["enrichment_notes"] = " | ".join(step_errors)
    else:
        existing_notes = final.get("enrichment_notes")
        if not existing_notes:
            final["enrichment_notes"] = (
                f"Fields improved: {fields_improved}"
            )

    logger.info(
        "Enrichment complete '%s': %d fields improved, "
        "%d step errors",
        business_name,
        fields_improved,
        len(step_errors),
    )
    return final


# ─────────────────────────────────────────────────────────
# Re-enrichment Engine
# ─────────────────────────────────────────────────────────

def reenrich_leads_missing_emails(
    limit: int = 50, progress_callback=None
) -> Dict:
    """Full enrichment written back for each lead."""
    if not _DB_AVAILABLE:
        logger.error("database.py not available")
        return {
            "processed":      0,
            "emails_found":   0,
            "websites_found": 0,
            "phones_found":   0,
        }

    leads = get_leads_missing_emails(limit=limit)
    results = {
        "processed":      0,
        "emails_found":   0,
        "websites_found": 0,
        "phones_found":   0,
    }
    total = len(leads)

    for i, lead in enumerate(leads):
        name = lead.get("business_name", "...")
        if progress_callback:
            progress_callback(i, total, name)

        had_website = bool(lead.get("website"))
        had_phone   = bool(lead.get("phone"))
        original    = dict(lead)

        enriched = enrich_lead(
            lead,
            find_email=True,
            find_phone=True,
            pre_existing=original,
        )
        results["processed"] += 1

        try:
            if insert_lead is not None:
                insert_lead(enriched)
            elif (
                update_lead_email is not None
                and enriched.get("email")
            ):
                update_lead_email(
                    enriched["id"], enriched["email"]
                )
        except Exception as exc:
            logger.warning(
                "reenrich write-back error for '%s': %s",
                name, exc,
            )

        if enriched.get("email"):
            results["emails_found"] += 1
        if not had_website and enriched.get("website"):
            results["websites_found"] += 1
        if not had_phone and enriched.get("phone"):
            results["phones_found"] += 1

        human_pause(0.3, 0.8)

    return results


# ─────────────────────────────────────────────────────────
# Contact Completeness
# ─────────────────────────────────────────────────────────

def get_contact_completeness(lead: Dict) -> Dict:
    score = 0
    if lead.get("phone"):
        score += 40
    if lead.get("email"):
        score += 35
    if lead.get("website"):
        score += 15
    if lead.get("address"):
        score += 10
    pct = min(score, 100)
    return {
        "percentage":  pct,
        "score":       pct,
        "has_phone":   bool(lead.get("phone")),
        "has_email":   bool(lead.get("email")),
        "has_website": bool(lead.get("website")),
        "has_address": bool(lead.get("address")),
    }