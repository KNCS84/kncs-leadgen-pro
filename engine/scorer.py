# engine/scorer.py
# KNCS LeadGen Pro — v10.1 (Audit Compliant)
# Matches Master Technical Audit & Project Handover Document v1.0
#
# Fixes applied in this revision:
#   BUG-SC-01: normalize_phone() called before DB write in score_and_save_lead()
#   BUG-SC-02: Circuit breaker reads/writes system_state (survives restarts)
#   BUG-SC-03: _validate_ai_result() rejects empty/N/A/out-of-range
#   BUG-SC-04: SHA-256 in _prompt_hash()
#   BUG-SC-05: Enrichment gate — only 'complete'/'enriched' enter scoring
#   BUG-SC-06: _record_ai_success() fires on 'cached' status too
#   BUG-SC-07: random.uniform(0.5, 1.5) — minimum 50% of base delay
#   BUG-SC-08: No sqlite3.connect() — all via get_connection()
#   BUG-SC-09: CF token tries all three known env var names
#   BUG-SC-10: validate_environment() called at batch entry points
#   BUG-SC-11: _is_blocked_domain() checks website + source_url
#   BUG-SC-12: User-agent rotation lives in enricher/scraper layer
#   ISSUE-A:   Connection closed before score_lead() — no long-held conn during AI
#   ISSUE-B:   deep_enriched/deep_enriched_at removed from UPDATE (not in schema)
#   ISSUE-C:   _verify_schema_ready() checks 'fit_reason' not 'fit_score_reason'
#   ISSUE-D:   Cached results validated through _validate_ai_result()
#   ISSUE-E:   CRITICAL — cached path now populates fit_score_raw correctly
#   ISSUE-F:   _priority_label() — score > 0 always returns at least 'low'
#   ISSUE-G:   Documented: score_lead_full() callers handle urgency/reachability

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────
# Imports — database.py is SOLE schema authority
# BUG-SC-08: No direct sqlite3.connect() anywhere in this file
# ─────────────────────────────────────────────────────────
from database import (
    get_connection,
    get_system_state,
    set_system_state,
    normalize_phone,
)

try:
    from engine.ai_client import call_ai, get_last_ai_error
    AI_CLIENT_AVAILABLE = True
except ImportError:
    AI_CLIENT_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "engine.ai_client not found — AI scoring disabled."
    )

try:
    from engine.router import assign_channel
    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False

try:
    from engine.search_intent import get_search_phrase, get_search_intent_line
    INTENT_AVAILABLE = True
except ImportError:
    INTENT_AVAILABLE = False

try:
    from engine.enricher import get_contact_completeness
except ImportError:
    def get_contact_completeness(lead: Dict) -> Dict:
        fields = {
            "Phone":   bool(lead.get("phone")),
            "Email":   bool(lead.get("email")),
            "Website": bool(lead.get("website")),
            "Address": bool(lead.get("address")),
        }
        filled = sum(1 for v in fields.values() if v)
        return {
            "percentage": (filled / len(fields) * 100),
            "fields":     fields,
            "filled":     filled,
            "total":      len(fields),
        }

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────
BANNED_PHRASES = [
    "strong online presence",
    "get more customers automatically",
    "businesses like yours",
    "digital marketing",
    "take your business to the next level",
    "in today's digital world",
    "online visibility",
    "brand awareness",
    "social media presence",
    "cutting-edge",
    "state-of-the-art",
]

MIN_BATCH_DELAY   = 1.5
SHORT_BATCH_DELAY = 1.5

NO_OVERWRITE_WITH_EMPTY = {
    "phone", "email", "website",
    "decision_maker_name", "decision_maker_title",
}

VALID_WA_STATUSES = {
    "verified", "estimated", "opted_in", "business_confirmed", "unknown"
}

# BUG-SC-11: Domain blocklist — competitors, spam, directories
BLOCKED_DOMAINS: set = {
    "google.com", "facebook.com", "linkedin.com", "instagram.com",
    "twitter.com", "x.com", "yelp.com", "yellowpages.com", "yell.com",
    "b2bhint.com", "companieshouse.gov.uk", "cqc.org.uk", "gov.uk",
    "nhs.uk", "wikipedia.org", "bing.com", "yahoo.com",
    "maps.google.com", "maps.apple.com", "apple.com", "microsoft.com",
    "amazon.com",
}

# Circuit breaker keys in system_state (BUG-SC-02)
CB_STATE_KEY     = "circuit_breaker_state"
CB_REASON_KEY    = "circuit_breaker_reason"
CB_OPENED_AT_KEY = "circuit_breaker_opened_at"
BATCH_LOCK_KEY   = "batch_lock"

_cb_lock = threading.Lock()


# ─────────────────────────────────────────────────────────
# Safety Helpers
# ─────────────────────────────────────────────────────────

def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _contains_banned(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in BANNED_PHRASES)


def _sanitise_opener(opener: str, lead: Dict) -> str:
    if not opener or _contains_banned(opener):
        return _build_specific_opener(lead)
    return opener.strip()


def _prompt_hash(prompt: str) -> str:
    """BUG-SC-04: SHA-256 — not MD5"""
    return hashlib.sha256(
        str(prompt or "").encode("utf-8", errors="ignore")
    ).hexdigest()


def _clean_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _no_website(value: str) -> bool:
    return (
        not value
        or value.lower() in {
            "", "none", "n/a", "not found", "no website", "no website found"
        }
        or len(value) <= 4
    )


def _get_wa_status(lead: Dict) -> str:
    wa = str(lead.get("wa_status") or "").strip().lower()
    return wa if wa in VALID_WA_STATUSES else "unknown"


# ─────────────────────────────────────────────────────────
# BUG-SC-11: Domain Block Check
# Also checks source_url so directory-scraped leads are caught
# even when they have no website field
# ─────────────────────────────────────────────────────────

def _is_blocked_domain(domain: str) -> bool:
    """
    Must be called before enrichment begins (Ch 4.2, 6.2).
    Checks both website domain and source_url domain.
    """
    if not domain:
        return False
    domain = domain.lower().replace("www.", "")
    return any(blocked in domain for blocked in BLOCKED_DOMAINS)


def _lead_has_blocked_domain(lead: Dict) -> bool:
    """
    Full lead-level check — inspects both website and source_url.
    BUG-SC-11: source_url path catches yelp/yellowpages scraped leads
    that have no website field.
    """
    website_domain   = _domain_from_url(lead.get("website") or "")
    source_domain    = _domain_from_url(lead.get("source_url") or "")
    return _is_blocked_domain(website_domain) or _is_blocked_domain(source_domain)


# ─────────────────────────────────────────────────────────
# BUG-SC-10: Environment Validation
# Called at every batch entry point before pipeline runs
# ─────────────────────────────────────────────────────────

def validate_environment() -> None:
    """
    Checks all required env vars before pipeline runs.
    BUG-SC-09: CF token tries all three known env var names.
    Raises ValueError with clear message on missing vars.
    """
    # BUG-SC-09: canonical CF token resolution — tries all three names
    cf_token = (
        os.getenv("CLOUDFLARE_API_TOKEN")
        or os.getenv("CF_API_TOKEN")
        or os.getenv("CLOUDFLARE_API_KEY")
    )

    missing: List[str] = []

    if not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not cf_token:
        missing.append(
            "Cloudflare API Token "
            "(CLOUDFLARE_API_TOKEN or CF_API_TOKEN or CLOUDFLARE_API_KEY)"
        )
    if not os.getenv("COMPANIES_HOUSE_API_KEY"):
        missing.append("COMPANIES_HOUSE_API_KEY")

    optional_one_of = [
        "HUNTER_API_KEY",
        "SNOV_CLIENT_ID",
        "SNOV_CLIENT_SECRET",
    ]
    has_email_provider = any(os.getenv(e) for e in optional_one_of)
    if not has_email_provider:
        missing.append(
            "At least one email provider "
            "(HUNTER_API_KEY or SNOV_CLIENT_ID+SECRET)"
        )

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


# ─────────────────────────────────────────────────────────
# Circuit Breaker — Persistent via system_state
# BUG-SC-02: State survives server restarts
# BUG-AI-01: Sync from DB on every check (not module globals)
# ─────────────────────────────────────────────────────────

def _is_circuit_open_persistent() -> bool:
    """Reads circuit breaker state from system_state (survives restarts)"""
    state = get_system_state(CB_STATE_KEY)
    return state == "open"


def _open_circuit(reason: str) -> None:
    """Opens circuit breaker and persists to system_state"""
    set_system_state(CB_STATE_KEY, "open")
    set_system_state(CB_REASON_KEY, reason)
    set_system_state(CB_OPENED_AT_KEY, datetime.now(timezone.utc).isoformat())
    logger.warning("Circuit breaker OPENED: %s", reason)


def _close_circuit() -> None:
    """Closes circuit breaker and persists to system_state"""
    set_system_state(CB_STATE_KEY, "closed")
    set_system_state(CB_REASON_KEY, "")
    set_system_state(CB_OPENED_AT_KEY, "")
    logger.info("Circuit breaker CLOSED")


def _record_ai_success() -> None:
    """
    BUG-SC-06: Called on every successful AI result (live OR cached).
    Resets ai_fail_count and closes circuit breaker.
    """
    _close_circuit()
    # Lead-level ai_fail_count is reset in score_and_save_lead() via UPDATE


def is_circuit_open() -> bool:
    """Public wrapper — checks persistent circuit breaker"""
    return _is_circuit_open_persistent()


# ─────────────────────────────────────────────────────────
# Batch Lock — Prevents duplicate scoring runs
# ─────────────────────────────────────────────────────────

def _acquire_batch_lock() -> bool:
    """Returns True if lock acquired, False if already held"""
    with _cb_lock:
        existing = get_system_state(BATCH_LOCK_KEY)
        if existing == "1":
            return False
        set_system_state(BATCH_LOCK_KEY, "1")
        return True


def _release_batch_lock() -> None:
    with _cb_lock:
        set_system_state(BATCH_LOCK_KEY, "0")


# ─────────────────────────────────────────────────────────
# Jitter Sleep — Minimum delay enforced (BUG-SC-07)
# ─────────────────────────────────────────────────────────

def _jitter_sleep(base: float, max_jitter: float = 1.0) -> None:
    """
    BUG-SC-07: random.uniform(0.5, 1.5) * base
    Minimum delay is always 50% of base — near-zero delay impossible.
    """
    delay = base * random.uniform(0.5, 1.5)
    time.sleep(delay)


# ─────────────────────────────────────────────────────────
# Schema Verification — read-only check, no ALTER TABLE
# ISSUE-C: checks 'fit_reason' not 'fit_score_reason'
# ─────────────────────────────────────────────────────────

def _verify_schema_ready() -> bool:
    """
    Confirms required columns exist.
    init_database() is the sole schema authority — this never alters.
    ISSUE-C fix: 'fit_reason' not 'fit_score_reason'.
    """
    try:
        conn = get_connection()
        cur = conn.execute("PRAGMA table_info(leads)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        required = {
            "fit_score", "fit_reason", "enrichment_status",
            "ai_fail_count", "ai_prompt_hash", "ai_output_cached",
            "scoring_status", "wa_status", "lead_priority",
        }
        return required.issubset(cols)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────
# AI Response Validation (BUG-SC-03)
# ─────────────────────────────────────────────────────────

def _validate_ai_result(
    result: Dict,
    lead: Dict,
    audit_score: int,
) -> Tuple[int, str, List[str], str]:
    """
    BUG-SC-03: Validates AI response — rejects empty, N/A, out-of-range.
    Also applied to cached results (ISSUE-D).
    Returns (fit_score_raw, fit_reason, pain_points, opener)
    """
    if not result:
        raise ValueError("Empty AI result")

    fit_score_raw = _safe_int(result.get("fit_score", 0))
    if not 1 <= fit_score_raw <= 10:
        raise ValueError(f"fit_score out of range: {fit_score_raw}")

    fit_reason = str(result.get("fit_reason") or "").strip()
    if not fit_reason or fit_reason.lower() in {"n/a", "none", "null"}:
        raise ValueError("Missing fit_reason")

    pain_points = result.get("pain_points", [])
    if not isinstance(pain_points, list) or not pain_points:
        pain_points = _default_pain_points(lead, audit_score)
    pain_points = [str(p).strip() for p in pain_points if str(p).strip()][:3]

    opener = str(result.get("recommended_opener") or "").strip()
    if not opener:
        opener = _build_specific_opener(lead)
    opener = _sanitise_opener(opener, lead)

    return fit_score_raw, fit_reason, pain_points, opener


# ─────────────────────────────────────────────────────────
# Website Audit Score
# ─────────────────────────────────────────────────────────

def compute_website_audit_score(lead: Dict) -> int:
    website              = str(lead.get("website") or "").strip().lower()
    rating               = _safe_float(lead.get("rating"))
    reviews              = _safe_int(lead.get("reviews"))
    phone                = str(lead.get("phone") or "").strip()
    website_discovered   = bool(lead.get("website_discovered", False))
    discovery_confidence = str(
        lead.get("website_discovery_confidence") or ""
    ).lower()

    has_website = not _no_website(website)

    if not has_website:
        score = 0
        if reviews and reviews > 10:
            score = 1
        if rating and rating >= 4.0 and reviews and reviews > 20:
            score = 2
        return max(0, min(10, score))

    if website_discovered:
        score = 3 if discovery_confidence == "high" else 2
    else:
        score = 4

    if reviews:
        if reviews >= 50:   score += 2
        elif reviews >= 20: score += 1
        elif reviews < 5:   score -= 1

    if rating:
        if rating >= 4.5:   score += 2
        elif rating >= 4.0: score += 1
        elif rating < 3.5:  score -= 1

    if phone and len(phone) >= 7:
        score += 1

    return max(0, min(10, score))


def _audit_explanation(score: int, lead: Optional[Dict] = None) -> str:
    ld               = lead or {}
    website_discovered = bool(ld.get("website_discovered", False))
    website_url        = str(ld.get("website") or "").strip()
    discovery_source   = str(ld.get("website_discovery_source") or "").lower()

    if score == 0:
        return (
            f"Website Audit Score: {score}/10 — No website found after web "
            "search. This business is completely invisible online."
        )
    if score <= 2:
        if website_discovered:
            return (
                f"Website Audit Score: {score}/10 — Website found at "
                f"{website_url} (discovered via {discovery_source}) but not "
                "promoted; very weak online signals."
            )
        return (
            f"Website Audit Score: {score}/10 — No website found. "
            "Completely invisible to online searchers."
        )
    if score <= 4:
        if website_discovered:
            return (
                f"Website Audit Score: {score}/10 — Website exists at "
                f"{website_url} (found via web search, not listed in GMB). "
                "Basic site, not optimised, low trust signals."
            )
        return (
            f"Website Audit Score: {score}/10 — Website exists but weak. "
            "Very few reviews and low trust signals."
        )
    if score <= 6:
        return (
            f"Website Audit Score: {score}/10 — Website exists but lacks "
            "optimisation. Low reviews or missing contact features."
        )
    if score <= 8:
        return (
            f"Website Audit Score: {score}/10 — Decent website with "
            "reasonable reviews. Could be stronger with better local SEO "
            "and WhatsApp integration."
        )
    return (
        f"Website Audit Score: {score}/10 — Relatively strong web presence. "
        "Focus pitch on modernisation and conversion."
    )


# ─────────────────────────────────────────────────────────
# Opener / Prompt Helpers
# ─────────────────────────────────────────────────────────

def _build_specific_opener(lead: Dict) -> str:
    name             = lead.get("business_name") or "there"
    industry         = lead.get("industry") or "your industry"
    city             = lead.get("city") or "your area"
    website          = str(lead.get("website") or "").strip().lower()
    rating           = _safe_float(lead.get("rating"))
    reviews          = _safe_int(lead.get("reviews"))
    audit            = _safe_int(lead.get("website_audit_score"))
    website_discovered = bool(lead.get("website_discovered", False))
    decision_maker   = str(
        lead.get("decision_maker_name")
        or lead.get("decision_maker_first")
        or ""
    ).strip()

    has_website  = not _no_website(website)
    audit_note   = f"(website audit: {audit}/10)"
    intent_phrase = (
        get_search_phrase(industry, city) if INTENT_AVAILABLE else ""
    )
    intent_line  = (
        f'Most customers search "{intent_phrase}" on Google — right now '
        "they may not find you easily. "
        if intent_phrase else ""
    )
    greeting = f"Hi {decision_maker}" if decision_maker else f"Hi {name}"

    if not has_website:
        return (
            f"{greeting}, I noticed {name} doesn't appear to have a website "
            f"yet {audit_note}. {intent_line}I build websites specifically "
            f"for local {industry} businesses in {city}. Worth a quick chat?"
        )

    if website_discovered:
        return (
            f"{greeting}, I found {name}'s website at {website} — but it "
            f"doesn't appear to be bringing in enquiries {audit_note}. "
            f"{intent_line}I help {industry} businesses in {city} turn their "
            "website into a proper lead generator. Worth a 10-minute call?"
        )

    if rating >= 4.0 and reviews >= 20:
        return (
            f"{greeting}, your {rating}★ rating with {reviews} reviews is "
            f"impressive {audit_note} — but your website isn't matching that "
            f"reputation. {intent_line}I design sites for {industry} "
            f"businesses in {city} that convert visitors into paying customers."
        )

    if reviews < 10:
        return (
            f"{greeting}, I ran a quick audit on {name}'s website "
            f"{audit_note}. {intent_line}The site is live but trust signals "
            "are low — I build professional websites for {industry} "
            "businesses. Would you like to see what's possible?"
        )

    return (
        f"{greeting}, I checked {name}'s website {audit_note}. {intent_line}"
        "There's room to improve how it converts visitors. I design sites "
        f"for {industry} businesses in {city}. Worth a quick 10-min call?"
    )


def _build_prompt(lead: Dict, audit_score: int) -> str:
    audit_note = _audit_explanation(audit_score, lead)
    industry   = lead.get("industry") or "Unknown"
    city       = lead.get("city") or "Unknown"
    country    = lead.get("country") or "Unknown"
    market     = lead.get("market") or "Unknown"
    wa_status  = _get_wa_status(lead)

    website_discovered = bool(lead.get("website_discovered", False))
    decision_maker       = str(
        lead.get("decision_maker_name")
        or lead.get("decision_maker_first")
        or ""
    ).strip()
    decision_maker_title = str(lead.get("decision_maker_title") or "").strip()

    intent_block = ""
    if INTENT_AVAILABLE:
        search_phrase = get_search_phrase(industry, city)
        if search_phrase:
            intent_block = (
                f"\nSEARCH INTENT SIGNAL\n"
                f'Customers in {city} search "{search_phrase}" on Google '
                f"before choosing this type of business.\n"
                "Reference this in the opener to show you understand how "
                "their customers find them.\n"
            )

    if website_discovered:
        website_context = (
            "\nWEBSITE DISCOVERY NOTE\n"
            "The website was NOT listed on Google My Business.\n"
            f"It was found via web search at: {lead.get('website', '')}\n"
            "This means the business has a site but does not actively "
            "promote it. Do NOT say 'you have no website'.\n"
            "Instead focus on: the site is not generating enquiries, "
            "not optimised, not visible in local search.\n"
        )
    else:
        website_val = str(lead.get("website") or "").strip()
        if _no_website(website_val):
            website_context = (
                "\nWEBSITE NOTE\n"
                "Web search confirmed: NO website found for this business.\n"
                "It is safe to say they have no website.\n"
            )
        else:
            website_context = (
                f"\nWEBSITE NOTE\nWebsite is listed and known: {website_val}\n"
                "Focus on improvement, not absence.\n"
            )

    decision_maker_block = ""
    if decision_maker:
        decision_maker_block = (
            f"\nDECISION MAKER\nName: {decision_maker}\n"
            f"Title: {decision_maker_title or 'Unknown'}\n"
            "Address the opener to this person by name.\n"
        )

    scoring_instructions = """
SCORING INSTRUCTIONS
────────────────────────────────────────
Score this lead 1–10 for website design fit.
You MUST spread scores across the full range.
Do NOT give every lead a 9 or 10.

SCORE BANDS — use all of them:
9–10  RESERVED: confirmed active business + zero web presence + phone verified + major city hub.
       Maximum 15% of leads should score here.
7–8   No website but contact info missing OR small city.
       Most no-website leads should score here.
5–6   Website DISCOVERED (exists but not promoted) OR lead is unenriched (phone/email/DM all missing).
       Missing data = uncertainty = lower score.
3–4   Website listed and functional. Moderate reviews. Lower priority.
1–2   Strong website, high reviews, active GMB. Do not pitch aggressively.

DIFFERENTIATION FACTORS:
 Phone present?           YES +1  NO -1
 Email present?           YES +0.5
 Decision maker found?    YES +1
 Rating >= 4.0?           YES -1
 Reviews >= 20?           YES -1
 Major city (Lagos/Abuja/PH/London)?  YES +0.5
 Smaller city?            -0.5
 Website discovered?      Cap score at 6 maximum
 Phone+Email+DM all empty? Cap score at 6 maximum

IMPORTANT: Missing data means uncertainty. Score lower when you cannot verify the lead is real.
"""

    return f"""
You are a sales intelligence assistant for KNCS, a professional website design agency run by Amechi. KNCS sells ONE service only: professional website design for local service businesses.

Do NOT suggest:
- AI tools, CRM tools, social media management
- Generic "digital marketing"
- Any banned phrase: "strong online presence", "get more customers automatically", "businesses like yours", "digital marketing", "take your business to the next level"

────────────────────────────────────────
LEAD DETAILS
────────────────────────────────────────
Business Name : {lead.get("business_name", "Unknown")}
Industry      : {industry}
City          : {city}
Country       : {country}
Market        : {market}
Website       : {lead.get("website", "None")}
Rating        : {lead.get("rating", "N/A")}
Reviews       : {lead.get("reviews", "N/A")}
Phone         : {lead.get("phone", "N/A")}
WA Status     : {wa_status}

AUDIT SIGNAL
{audit_note}
{website_context}
{intent_block}
{decision_maker_block}
{scoring_instructions}

────────────────────────────────────────
RESPOND IN THIS EXACT JSON FORMAT — NO OTHER TEXT:
────────────────────────────────────────
{{
  "fit_score": <integer 1-10>,
  "fit_reason": "<one sentence explaining the score>",
  "pain_points": ["<pain 1>", "<pain 2>", "<pain 3>"],
  "recommended_opener": "<your opener here>"
}}
""".strip()


def _parse_ai_response(text: str) -> Dict:
    if not text:
        return {}
    try:
        text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", text, re.DOTALL)
        if not match:
            return json.loads(text)
        return json.loads(match.group())
    except Exception as exc:
        logger.warning(
            "JSON parse failed: %s | text: %s", exc, text[:200]
        )
        return {}


def _default_pain_points(lead: Dict, audit_score: int) -> List[str]:
    points             = []
    website            = str(lead.get("website") or "").strip().lower()
    no_web             = _no_website(website)
    website_discovered = bool(lead.get("website_discovered", False))
    reviews            = _safe_int(lead.get("reviews"))
    rating             = _safe_float(lead.get("rating"))

    if no_web:
        points.append(
            "No website — completely invisible to online searchers"
        )
    elif website_discovered:
        points.append(
            "Website not promoted — not appearing in local Google searches"
        )
    if reviews < 10:
        points.append(
            "Very few reviews — low social proof for new customers"
        )
    if 0 < rating < 4.0:
        points.append(
            f"Low rating ({rating}★) — damaging first impressions"
        )
    if audit_score <= 4:
        points.append(
            "Website does not meet basic professional standards"
        )
    if not points:
        points.append(
            "Website could be significantly improved for conversions"
        )
    return points[:3]


def _priority_label(score: int) -> str:
    """
    BUG-SC-01: Thresholds on 0-100 scale.
    ISSUE-F: score > 0 always returns at minimum 'low' — never 'unscored'
    for a lead that has been scored (even by fallback).
    'unscored' is reserved for leads where fit_score is None/0.
    """
    if score >= 70: return "high"
    if score >= 50: return "medium"
    if score > 0:   return "low"
    return "unscored"


# ─────────────────────────────────────────────────────────
# BUG-SC-03: Contact-Aware Fallback Scoring
# ─────────────────────────────────────────────────────────

def _fallback_score(lead: Dict, audit_score: int) -> int:
    reviews = _safe_int(lead.get("reviews"))
    rating = _safe_float(lead.get("rating"))
    website = str(lead.get("website") or "").strip().lower()
    has_web = not _no_website(website)
    has_phone = bool(str(lead.get("phone") or "").strip())
    has_dm = bool(str(lead.get("decision_maker_name") or "").strip())
    if not has_web:
        base = 10 if reviews >= 10 else 9
    else:
        base = max(1, 10 - audit_score)
        if audit_score >= 8:
            base = min(base, 3)
    if has_phone:
        base += 1
    else:
        base -= 2
    if has_dm:
        base += 1
    return max(1, min(10, base))


# ─────────────────────────────────────────────────────────
# MAIN SCORING FUNCTION
# ─────────────────────────────────────────────────────────

def score_lead(lead: Dict) -> Dict:
    """
    Full scoring pipeline — returns enriched lead dict with 0-100 fit_score.

    ISSUE-E fix: cached path now correctly populates fit_score_raw through
    _validate_ai_result() before the fallback block runs.
    BUG-SC-06 fix: _record_ai_success() fires on both 'ai' and 'cached' paths.
    """
    lead = dict(lead or {})

    # BUG-SC-11: Block check on website AND source_url (ISSUE source_url gap)
    if _lead_has_blocked_domain(lead):
        logger.info(
            "Skipping blocked domain for lead: %s",
            lead.get("business_name", "unknown"),
        )
        return {
            "fit_score":    0,
            "fit_reason":   "Blocked domain",
            "lead_priority":"unscored",
        }

    # Country enrichment
    try:
        from engine.country_router import enrich_lead_with_country
        lead = enrich_lead_with_country(lead)
    except Exception as exc:
        logger.warning("[scorer] Country enrichment failed: %s", exc)

    # Audit score
    audit_score              = compute_website_audit_score(lead)
    lead["website_audit_score"] = audit_score

    # Build prompt + hash
    prompt      = _build_prompt(lead, audit_score)
    prompt_hash = _prompt_hash(prompt)

    # ── Initialise result variables ───────────────────────
    result        : Dict  = {}
    ai_text       : str   = ""
    scoring_status: str   = "fallback"
    last_ai_error : str   = ""

    # Shared mutable output — set in exactly one of the three paths below
    fit_score_raw : int       = 0
    fit_reason    : str       = ""
    pain_points   : List[str] = []
    opener        : str       = ""
    used_good_result          = False  # True when validation succeeds

    # ── Path 1: Cache hit ─────────────────────────────────
    cached_hash   = str(lead.get("ai_prompt_hash") or "").strip()
    cached_output = str(lead.get("ai_output_cached") or "").strip()

    if cached_hash == prompt_hash and cached_output:
        ai_text        = cached_output
        cached_result  = _parse_ai_response(ai_text)
        # ISSUE-D + ISSUE-E: validate cached result before using it
        if cached_result:
            try:
                fit_score_raw, fit_reason, pain_points, opener = (
                    _validate_ai_result(cached_result, lead, audit_score)
                )
                scoring_status  = "cached"
                used_good_result = True
                # BUG-SC-06: fire on cached success too
                _record_ai_success()
                result = cached_result
            except ValueError as exc:
                logger.warning(
                    "[scorer] Cached result invalid (%s) — re-scoring", exc
                )
                # Fall through to live AI call below

    # ── Path 2: Live AI call ──────────────────────────────
    if not used_good_result:
        if _is_circuit_open_persistent():
            logger.warning(
                "[scorer] Circuit breaker open — skipping AI for %s",
                lead.get("business_name", "unknown"),
            )
            last_ai_error  = "circuit_open"
            scoring_status = "circuit_open"
        elif not AI_CLIENT_AVAILABLE:
            last_ai_error  = "ai_client_unavailable"
            scoring_status = "fallback"
        else:
            try:
                ai_text = call_ai(
                    prompt=prompt,
                    max_tokens=600,
                    use_cache=True,
                    fallback="",
                )
                live_result = _parse_ai_response(ai_text)
                if live_result:
                    try:
                        fit_score_raw, fit_reason, pain_points, opener = (
                            _validate_ai_result(live_result, lead, audit_score)
                        )
                        scoring_status   = "ai"
                        used_good_result = True
                        # BUG-SC-06: reset circuit on success
                        _record_ai_success()
                        result = live_result
                    except ValueError as exc:
                        logger.warning(
                            "[scorer] AI validation failed: %s — using fallback",
                            exc,
                        )
                        last_ai_error  = str(exc)
                        scoring_status = "fallback"
            except Exception as exc:
                logger.warning("[scorer] AI call raised exception: %s", exc)
                last_ai_error  = str(exc)
                scoring_status = "fallback"

    # ── Path 3: Fallback ─────────────────────────────────
    if not used_good_result:
        fit_score_raw = _fallback_score(lead, audit_score)
        fit_reason    = _audit_explanation(audit_score, lead)
        pain_points   = _default_pain_points(lead, audit_score)
        opener        = _build_specific_opener(lead)

    # ── Scale and classify ────────────────────────────────
    fit_score = fit_score_raw * 10  # BUG-SC-01: 0-100 scale
    priority  = _priority_label(fit_score)

    # ── Routing ───────────────────────────────────────────
    routing_updates: Dict = {}
    if ROUTER_AVAILABLE:
        try:
            routing_input = dict(lead)
            routing_input.update({
                "fit_score":          fit_score,
                "lead_priority":      priority,
                "website_audit_score":audit_score,
                "recommended_opener": opener,
                "fit_reason":         fit_reason,
                "pain_points":        pain_points,
            })
            raw_routing = assign_channel(routing_input) or {}
            routing_only_fields = {
                "best_channel", "channel_label", "routing_channel",
                "routing_reason", "call_ready", "email_ready",
                "whatsapp_ready", "ai_call_ready", "manual_review",
                "wa_eligible", "wa_status", "whatsapp_check_attempted",
                "whatsapp_check_at", "verification_status", "phone_verified",
            }
            routing_updates = {
                k: v for k, v in raw_routing.items()
                if k in routing_only_fields
            }
        except Exception as exc:
            logger.warning("[scorer] Routing failed: %s", exc)

    # ── AI fail counter ───────────────────────────────────
    ai_fail_count = _safe_int(lead.get("ai_fail_count", 0))
    if scoring_status in {"fallback", "circuit_open"}:
        ai_fail_count += 1
    else:
        ai_fail_count = 0

    ai_output_cached = ai_text or json.dumps(result or {}, ensure_ascii=False)

    def _keep(key: str, new_val: Any, lead_val: Any) -> Any:
        if key in NO_OVERWRITE_WITH_EMPTY:
            if not new_val and lead_val:
                return lead_val
        return new_val if new_val is not None else lead_val

    search_phrase = (
        get_search_phrase(lead.get("industry", ""), lead.get("city", ""))
        if INTENT_AVAILABLE else ""
    )
    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "fit_score":                    fit_score,
        "fit_reason":                   fit_reason,
        "pain_points":                  pain_points,
        "recommended_opener":           opener,
        "website_audit_score":          audit_score,
        "lead_priority":                priority,
        "search_phrase":                search_phrase,
        "website":                      _keep("website", lead.get("website", ""), lead.get("website", "")),
        "website_discovered":           lead.get("website_discovered", False),
        "website_discovery_confidence": lead.get("website_discovery_confidence", "none"),
        "website_discovery_source":     lead.get("website_discovery_source", "none"),
        "decision_maker_name":          lead.get("decision_maker_name", ""),
        "decision_maker_title":         lead.get("decision_maker_title", ""),
        "decision_maker_source":        lead.get("decision_maker_source", ""),
        "phone":                        _keep("phone", lead.get("phone", ""), lead.get("phone", "")),
        "phone_source":                 lead.get("phone_source", ""),
        "email":                        _keep("email", lead.get("email", ""), lead.get("email", "")),
        "email_source":                 lead.get("email_source", ""),
        "country":                      lead.get("country", ""),
        "market":                       lead.get("market", ""),
        "timezone":                     lead.get("timezone", ""),
        "phone_country_code":           lead.get("phone_country_code", ""),
        "country_confidence":           lead.get("country_confidence", "low"),
        "wa_status":                    routing_updates.get("wa_status", lead.get("wa_status", "unknown")),
        "wa_eligible":                  routing_updates.get("wa_eligible", lead.get("wa_eligible", 0)),
        "wa_verified":                  lead.get("wa_verified", 0),
        "wa_opt_in":                    lead.get("wa_opt_in", 0),
        "verification_status":          routing_updates.get("verification_status", lead.get("verification_status", "unverified")),
        "phone_verified":               routing_updates.get("phone_verified", lead.get("phone_verified", 0)),
        "routing_channel":              routing_updates.get("routing_channel", lead.get("routing_channel", "")),
        "best_channel":                 routing_updates.get("best_channel", lead.get("best_channel", "unknown")),
        "channel_label":                routing_updates.get("channel_label", lead.get("channel_label", "unrouted")),
        "routing_reason":               routing_updates.get("routing_reason", lead.get("routing_reason", "")),
        "call_ready":                   routing_updates.get("call_ready", lead.get("call_ready", 0)),
        "email_ready":                  routing_updates.get("email_ready", lead.get("email_ready", 0)),
        "whatsapp_ready":               routing_updates.get("whatsapp_ready", lead.get("whatsapp_ready", 0)),
        "ai_call_ready":                routing_updates.get("ai_call_ready", lead.get("ai_call_ready", 0)),
        "manual_review":                routing_updates.get("manual_review", lead.get("manual_review", 0)),
        "whatsapp_check_attempted":     routing_updates.get("whatsapp_check_attempted", lead.get("whatsapp_check_attempted", 0)),
        "whatsapp_check_at":            routing_updates.get("whatsapp_check_at", lead.get("whatsapp_check_at", "")),
        "ai_scored_at":                 now_iso,
        "ai_prompt_hash":               prompt_hash,
        "ai_output_cached":             ai_output_cached,
        "ai_fail_count":                ai_fail_count,
        "last_ai_error":                last_ai_error,
        "scoring_status":               scoring_status,
        "scored_at":                    now_iso,
        "fit_scored_at":                now_iso,
        "last_contact_channel":         lead.get("last_contact_channel", ""),
    }


# ─────────────────────────────────────────────────────────
# score_lead_full() — Entry point for job_manager.py
# BUG-SC-02: Uses persistent circuit breaker
# NOTE (ISSUE-G): urgency_score and reachability_score are computed here.
# Callers that need these written to DB must handle the UPDATE themselves
# or call score_and_save_lead() and add the fields to the UPDATE there.
# ─────────────────────────────────────────────────────────

def score_lead_full(lead: Dict) -> Dict:
    """Wraps score_lead() and adds urgency_score, reachability_score"""
    result = score_lead(lead)
    if not result:
        return {}

    fit   = _safe_int(result.get("fit_score", 0))
    phone = bool(lead.get("phone"))
    email = bool(lead.get("email"))
    wa    = bool(
        lead.get("whatsapp_number")
        or str(lead.get("wa_status", "")).lower() == "verified"
    )

    result["urgency_score"] = fit
    reachability = 0
    if phone: reachability += 40
    if email: reachability += 30
    if wa:    reachability += 30
    result["reachability_score"] = reachability

    return result


# ─────────────────────────────────────────────────────────
# DATABASE SAVE
# ISSUE-A: Connection closed before score_lead() — no long-held
#          connection during AI call (potentially 10-30 seconds)
# ISSUE-B: deep_enriched/deep_enriched_at removed (not in schema)
# BUG-SC-01: normalize_phone() applied before DB write
# ─────────────────────────────────────────────────────────

def score_and_save_lead(lead_id: int) -> Optional[Dict]:
    """
    Load lead from DB, score it, save result back.
    Returns result dict on success, None on DB failure.

    ISSUE-A: The SELECT connection is closed BEFORE score_lead() runs.
    score_lead() opens its own short-lived connections for circuit breaker
    and cache reads. The UPDATE connection is opened only after scoring
    completes. No long-held connection during AI calls.
    """
    # ── SELECT — close immediately after fetch ────────────
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if not row:
            logger.warning("Lead %s not found in DB", lead_id)
            return None
        lead = dict(row)
    except Exception as exc:
        logger.error("Lead fetch failed for id %s: %s", lead_id, exc)
        return None
    finally:
        conn.close()  # ISSUE-A: closed before scoring starts

    # ── Score (no DB connection held) ────────────────────
    try:
        result = score_lead(lead)
    except Exception as exc:
        logger.error("Scoring failed for lead %s: %s", lead_id, exc)
        return None

    # ── Helpers ───────────────────────────────────────────
    def _safe_write(key: str) -> str:
        new_val = str(result.get(key) or "").strip()
        old_val = str(lead.get(key) or "").strip()
        if key in NO_OVERWRITE_WITH_EMPTY and not new_val and old_val:
            return old_val
        return new_val

    # BUG-SC-01: normalize phone before writing to DB
    raw_phone  = _safe_write("phone")
    norm_phone = normalize_phone(raw_phone) or raw_phone

    now_iso = datetime.now(timezone.utc).isoformat()

    # ── UPDATE — fresh connection ─────────────────────────
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE leads SET
                fit_score                    = ?,
                fit_reason                   = ?,
                pain_points                  = ?,
                recommended_opener           = ?,
                website_audit_score          = ?,
                lead_priority                = ?,
                search_phrase                = ?,
                fit_scored_at                = ?,
                scored_at                    = ?,
                website                      = ?,
                website_discovered           = ?,
                website_discovery_confidence = ?,
                website_discovery_source     = ?,
                decision_maker_name          = ?,
                decision_maker_title         = ?,
                decision_maker_source        = ?,
                phone                        = ?,
                phone_source                 = ?,
                email                        = ?,
                email_source                 = ?,
                ai_scored_at                 = ?,
                ai_prompt_hash               = ?,
                ai_output_cached             = ?,
                ai_fail_count                = ?,
                last_ai_error                = ?,
                scoring_status               = ?,
                country                      = ?,
                market                       = ?,
                timezone                     = ?,
                phone_country_code           = ?,
                country_confidence           = ?,
                wa_status                    = ?,
                wa_eligible                  = ?,
                wa_verified                  = ?,
                wa_opt_in                    = ?,
                verification_status          = ?,
                phone_verified               = ?,
                routing_channel              = ?,
                best_channel                 = ?,
                channel_label                = ?,
                routing_reason               = ?,
                call_ready                   = ?,
                email_ready                  = ?,
                whatsapp_ready               = ?,
                ai_call_ready                = ?,
                manual_review                = ?,
                whatsapp_check_attempted     = ?,
                whatsapp_check_at            = ?,
                last_contact_channel         = ?,
                updated_at                   = ?
            WHERE id = ?
        """, (
            result["fit_score"],
            result["fit_reason"],
            json.dumps(result.get("pain_points", [])),
            result.get("recommended_opener", ""),
            result["website_audit_score"],
            result["lead_priority"],
            result.get("search_phrase", ""),
            result.get("fit_scored_at", now_iso),
            result.get("scored_at", now_iso),
            _safe_write("website"),
            int(bool(result.get("website_discovered", False))),
            result.get("website_discovery_confidence", "none"),
            result.get("website_discovery_source", "none"),
            result.get("decision_maker_name", ""),
            result.get("decision_maker_title", ""),
            result.get("decision_maker_source", ""),
            norm_phone,                              # BUG-SC-01
            result.get("phone_source", ""),
            _safe_write("email"),
            result.get("email_source", ""),
            result.get("ai_scored_at", now_iso),
            result.get("ai_prompt_hash", ""),
            result.get("ai_output_cached", ""),
            result.get("ai_fail_count", 0),
            result.get("last_ai_error", ""),
            result.get("scoring_status", "fallback"),
            result.get("country", ""),
            result.get("market", ""),
            result.get("timezone", ""),
            result.get("phone_country_code", ""),
            result.get("country_confidence", "low"),
            result.get("wa_status", "unknown"),
            result.get("wa_eligible", 0),
            result.get("wa_verified", 0),
            result.get("wa_opt_in", 0),
            result.get("verification_status", "unverified"),
            result.get("phone_verified", 0),
            result.get("routing_channel", ""),
            result.get("best_channel", ""),
            result.get("channel_label", ""),
            result.get("routing_reason", ""),
            result.get("call_ready", 0),
            result.get("email_ready", 0),
            result.get("whatsapp_ready", 0),
            result.get("ai_call_ready", 0),
            result.get("manual_review", 0),
            result.get("whatsapp_check_attempted", 0),
            result.get("whatsapp_check_at", ""),
            result.get("last_contact_channel", ""),
            now_iso,
            lead_id,
        ))
        conn.commit()
    except Exception as exc:
        logger.error("DB save failed for lead %s: %s", lead_id, exc)
        return None
    finally:
        conn.close()

    logger.info(
        "Lead %s scored — fit=%s audit=%s/10 priority=%s "
        "website_discovered=%s dm=%s channel=%s",
        lead_id,
        result["fit_score"],
        result["website_audit_score"],
        result["lead_priority"],
        result.get("website_discovered", False),
        result.get("decision_maker_name") or "—",
        result.get("best_channel") or "—",
    )
    return result


# ─────────────────────────────────────────────────────────
# BATCH SCORING
# BUG-SC-05: Enrichment gate in _get_unscored_ids()
# BUG-SC-10: validate_environment() called at entry points
# ─────────────────────────────────────────────────────────

def _get_unscored_ids(limit: Optional[int] = None) -> List[int]:
    """
    BUG-SC-05: Enrichment gate — only 'complete' or 'enriched' leads
    enter scoring. 'pending' leads are explicitly excluded.
    """
    conn = get_connection()
    try:
        cur   = conn.cursor()
        query = """
            SELECT id FROM leads
            WHERE (
                fit_score IS NULL
                OR lead_priority IS NULL
                OR lead_priority IN ('', 'unscored')
            )
            AND enrichment_status IN ('complete', 'enriched')
            ORDER BY id ASC
        """
        if limit:
            cur.execute(query + " LIMIT ?", (limit,))
        else:
            cur.execute(query)
        return [row["id"] for row in cur.fetchall()]
    finally:
        conn.close()


def score_all_unscored(delay: float = MIN_BATCH_DELAY) -> int:
    """
    Legacy batch — uses jitter sleep.
    BUG-SC-10: validate_environment() called before pipeline runs.
    """
    try:
        validate_environment()
    except ValueError as exc:
        logger.error("[scorer] Environment check failed: %s", exc)
        return 0

    ids = _get_unscored_ids()
    logger.info("[scorer] Batch scoring %s leads", len(ids))
    scored = 0

    for lead_id in ids:
        if _is_circuit_open_persistent():
            logger.warning(
                "[scorer] Circuit open — stopping batch at %d/%d",
                scored, len(ids),
            )
            break
        result = score_and_save_lead(lead_id)
        if result:
            scored += 1
        _jitter_sleep(delay)

    logger.info("[scorer] Batch complete — %s/%s scored", scored, len(ids))
    return scored


def score_leads_batch(
    lead_ids: List[int],
    model: str = "",
    config: Optional[Dict] = None,
) -> Dict:
    """
    Ch 6.2: Scores a batch of leads via Cloudflare AI.
    BUG-SC-10: validate_environment() called before any scoring.
    BUG-SC-05: Enrichment gate enforced in _get_unscored_ids().
    Uses batch lock to prevent duplicate runs.
    """
    # BUG-SC-10: validate before acquiring lock
    try:
        validate_environment()
    except ValueError as exc:
        logger.error("[scorer] Environment check failed: %s", exc)
        return {
            "total": 0, "scored": 0, "high": 0, "medium": 0, "low": 0,
            "circuit_stopped": False,
            "error": f"Environment: {exc}",
        }

    if not _acquire_batch_lock():
        return {
            "total": 0, "scored": 0, "high": 0, "medium": 0, "low": 0,
            "circuit_stopped": False,
            "error": "Batch lock held",
        }

    if _is_circuit_open_persistent():
        _release_batch_lock()
        return {
            "total": 0, "scored": 0, "high": 0, "medium": 0, "low": 0,
            "circuit_stopped": True,
            "error": "Circuit open",
        }

    total          = len(lead_ids)
    scored = high = medium = low = 0
    circuit_stopped = False

    try:
        for lead_id in lead_ids:
            if _is_circuit_open_persistent():
                logger.warning(
                    "[scorer] Circuit open — stopping early. "
                    "%d of %d scored.", scored, total,
                )
                circuit_stopped = True
                break

            result = score_and_save_lead(lead_id)
            if result:
                scored += 1
                fit = _safe_int(result.get("fit_score", 0))
                if fit >= 70:   high   += 1
                elif fit >= 50: medium += 1
                else:           low    += 1

            _jitter_sleep(SHORT_BATCH_DELAY)
    finally:
        _release_batch_lock()

    return {
        "total":          total,
        "scored":         scored,
        "high":           high,
        "medium":         medium,
        "low":            low,
        "circuit_stopped":circuit_stopped,
        "used_ai":        True,
    }


def score_unscored_leads_batch(
    limit: int = 50,
    use_ai: bool = True,
) -> Dict:
    """Legacy wrapper — delegates to score_leads_batch"""
    ids = _get_unscored_ids(limit=limit)
    return score_leads_batch(ids)


# ─────────────────────────────────────────────────────────
# Legacy Helpers
# ─────────────────────────────────────────────────────────

def get_fit_label(fit_score: int) -> Dict:
    """BUG-SC-01: Thresholds on 0-100 scale"""
    if fit_score >= 70:
        return {"label": "⭐ HIGH FIT",    "color": "#2ed573", "bg": "#eafff1"}
    if fit_score >= 50:
        return {"label": "✅ MEDIUM FIT",  "color": "#FFA502", "bg": "#FFF3E0"}
    if fit_score >= 10:
        return {"label": "⚪ LOW FIT",     "color": "#747D8C", "bg": "#F1F2F6"}
    return     {"label": "❓ UNSCORED",    "color": "#a4b0be", "bg": "#f1f2f6"}


def get_channel_label(channel: str) -> Dict:
    return {
        "whatsapp": {"label": "💬 WhatsApp", "color": "#25D366", "bg": "#e8fff2"},
        "ai_call":  {"label": "🤖 AI Call",  "color": "#5352ED", "bg": "#eeeeff"},
        "email":    {"label": "📧 Email",    "color": "#1E90FF", "bg": "#e8f4ff"},
        "manual":   {"label": "👤 Manual",   "color": "#747D8C", "bg": "#F1F2F6"},
        "unknown":  {"label": "❓ Unknown",  "color": "#a4b0be", "bg": "#f1f2f6"},
    }.get(
        channel or "unknown",
        {"label": "❓ Unknown", "color": "#a4b0be", "bg": "#f1f2f6"},
    )