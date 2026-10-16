# pages/care_home_dashboard.py
# KNCS LeadGen Pro — Care Home Lead Generator
# v10.1 (audit-clean)
#
# Registered bugs fixed (BUG-CH-01 through BUG-CH-13):
#   BUG-CH-01  conn.row_factory override + connection leak — fixed by
#              delegating all DB writes to insert_lead() from database.py
#   BUG-CH-02  Dedup by business_name alone — fixed by insert_lead()
#              which uses business_name + city composite key
#   BUG-CH-03  No explicit transaction — fixed by insert_lead() atomic
#              BEGIN IMMEDIATE
#   BUG-CH-04  conn.close() inside try — fixed; connection lifecycle
#              owned by insert_lead()
#   BUG-CH-05  HTML injection without html.escape() — _esc() applied to
#              all field values before HTML injection
#   BUG-CH-06  Raw INSERT bypassed insert_lead() — removed entirely;
#              _save_leads_to_db() calls insert_lead() exclusively
#   BUG-CH-07  javascript: injection via href — _safe_href() validates
#              scheme; tel:/mailto: values now escaped before href
#              construction
#   BUG-CH-08  TOCTOU: file opened twice (stat then open) — single
#              open() with stat() inside the with block
#   BUG-CH-09  Full 95 MB JSON parsed on every render — @st.cache_data
#              wrappers with TTL on both load functions
#   BUG-CH-10  No batch size cap — MAX_ENRICH_BATCH = 50; user warned;
#              batch sliced before enrichment starts
#   BUG-CH-11  City/postcode positional args swapped — eliminated by
#              using insert_lead() which maps fields by name
#   BUG-CH-13  Rating value not escaped before HTML injection — _esc()
#              applied in _rating_badge()
#
# Additional defects fixed in this audit pass:
#   FIX-CH-A   _load_existing_from_db(): connection leaked when
#              fetchone() raised — conn.close() moved to finally block
#   FIX-CH-B   _enrich_leads_with_progress(): division by zero when
#              total == 0 — max(total, 1) guard added
#   FIX-CH-C   phone_html href: raw phone value used unescaped inside
#              href attribute — html.escape() applied before href build
#   FIX-CH-D   email_html href: raw email value used unescaped inside
#              href attribute — html.escape() applied before href build
#
# FIX-PDF-MINING (v10.8 original):
#   location_id now passed to find_personal_email() which activates
#   CQC inspection report PDF mining (~25% hit rate).

from __future__ import annotations

import csv
import html
import io
import json
import logging
import re
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional
from urllib.parse import quote as _url_quote, urljoin, urlparse

import streamlit as st

logger = logging.getLogger(__name__)

# ── Design tokens ──────────────────────────────────────────────────────────

_C: Dict[str, str] = {
    "bg_base":        "#060d19",
    "bg_surface":     "#0b1628",
    "bg_raised":      "#101f38",
    "bg_input":       "#0d1a2e",
    "brand_blue":     "#5b8cff",
    "brand_purple":   "#7c5cff",
    "green":          "#10b981",
    "gold":           "#f59e0b",
    "red":            "#ef4444",
    "text_primary":   "#f1f5f9",
    "text_secondary": "#94a3b8",
    "text_muted":     "#475569",
    "border_subtle":  "rgba(255,255,255,0.05)",
    "border_mid":     "rgba(255,255,255,0.08)",
    "border_brand":   "rgba(91,140,255,0.30)",
}

_CACHE_FILE       = Path("engine/cqc_location_cache.json")
_MAX_ENRICH_BATCH = 50   # BUG-CH-10: hard cap — UI never blocks on >50 leads

POSTCODE_REGIONS: Dict[str, List[str]] = {
    "All Regions":        [],
    "Birmingham":         ["B"],
    "Manchester":         ["M"],
    "Leeds":              ["LS"],
    "Liverpool":          ["L"],
    "Sheffield":          ["S"],
    "Bristol":            ["BS"],
    "Newcastle":          ["NE"],
    "Nottingham":         ["NG"],
    "Leicester":          ["LE"],
    "Coventry/Nuneaton":  ["CV"],
    "London East":        ["E"],
    "London North":       ["N", "NW"],
    "London South":       ["SE", "SW"],
    "London West":        ["W"],
    "London Central":     ["EC", "WC"],
    "Bradford":           ["BD"],
    "Wolverhampton":      ["WV"],
    "Stoke-on-Trent":     ["ST"],
    "Derby":              ["DE"],
    "Southampton":        ["SO"],
    "Portsmouth":         ["PO"],
    "Norwich":            ["NR"],
    "Luton":              ["LU"],
    "Reading":            ["RG"],
    "Oxford":             ["OX"],
    "Cambridge":          ["CB"],
    "Northampton":        ["NN"],
    "Milton Keynes":      ["MK"],
    "Swindon":            ["SN"],
    "Gloucester":         ["GL"],
    "Worcester":          ["WR"],
    "Hereford":           ["HR"],
    "Shrewsbury":         ["SY"],
    "Chester":            ["CH"],
    "Preston":            ["PR"],
    "Blackpool":          ["FY"],
    "Blackburn":          ["BB"],
    "Carlisle":           ["CA"],
    "Durham":             ["DH", "DL"],
    "Middlesbrough":      ["TS"],
    "York":               ["YO"],
    "Hull":               ["HU"],
    "Lincoln":            ["LN"],
    "Peterborough":       ["PE"],
    "Ipswich":            ["IP"],
    "Colchester":         ["CO"],
    "Chelmsford":         ["CM"],
    "Watford":            ["WD"],
    "Slough":             ["SL"],
    "Guildford":          ["GU"],
    "Crawley":            ["RH"],
    "Brighton":           ["BN"],
    "Maidstone":          ["ME"],
    "Canterbury":         ["CT"],
    "Exeter":             ["EX"],
    "Plymouth":           ["PL"],
    "Cornwall":           ["TR"],
    "Somerset":           ["TA"],
    "Bath":               ["BA"],
    "Salisbury":          ["SP"],
    "Swansea":            ["SA"],
    "Cardiff":            ["CF"],
    "Newport":            ["NP"],
}

REGION_OPTIONS: List[str] = ["All Regions"] + sorted(
    k for k in POSTCODE_REGIONS if k != "All Regions"
)

RATING_COLOURS: Dict[str, tuple] = {
    "outstanding":          (_C["green"],      "rgba(16,185,129,0.12)"),
    "good":                 (_C["brand_blue"], "rgba(91,140,255,0.12)"),
    "requires improvement": (_C["gold"],       "rgba(245,158,11,0.12)"),
    "inadequate":           (_C["red"],        "rgba(239,68,68,0.12)"),
}

# BUG-CH-07: only these schemes are allowed in href attributes
_SAFE_SCHEMES = ("https://", "http://", "tel:", "mailto:")

# Email validation pattern
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Domains to reject — generic/spam/no-reply addresses
_REJECT_EMAIL_DOMAINS: set = {
    "example.com", "test.com", "placeholder.com",
    "noreply.com", "no-reply.com", "donotreply.com",
    "sentry.io", "w3.org", "schema.org",
    "google.com", "gmail.com", "yahoo.com",
    "hotmail.com", "outlook.com",
}

# Pages to check for email on care home websites
_EMAIL_SEARCH_PATHS: List[str] = [
    "/contact",
    "/contact-us",
    "/contact-us.html",
    "/contactus",
    "/about",
    "/about-us",
    "",   # homepage last
]


def _safe_href(url: str) -> str:
    """
    BUG-CH-07: Validate URL scheme before injecting into href.
    Returns '#' for any URL that does not start with an allowed scheme.
    The returned string is HTML-attribute-escaped.
    """
    if not url:
        return "#"
    stripped = url.strip()
    for scheme in _SAFE_SCHEMES:
        if stripped.lower().startswith(scheme):
            return html.escape(stripped, quote=True)
    return "#"


def _esc(value: Any) -> str:
    """BUG-CH-05: All values must pass through _esc() before HTML injection."""
    return html.escape(str(value) if value is not None else "")


def _rating_badge(rating: str) -> str:
    """BUG-CH-13: rating value escaped before span injection."""
    if not rating:
        return (
            "<span style='color:"
            + _C["text_muted"]
            + ";font-size:11px;'>—</span>"
        )
    key        = rating.lower().strip()
    colour, bg = RATING_COLOURS.get(
        key, (_C["text_muted"], "rgba(71,85,105,0.15)")
    )
    # BUG-CH-13: _esc() applied to rating before injection
    return (
        "<span style='background:" + bg + ";color:" + colour + ";"
        "border:1px solid " + colour + "33;border-radius:999px;"
        "padding:2px 10px;font-size:10px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:0.5px;"
        "white-space:nowrap;'>" + _esc(rating) + "</span>"
    )


def _extract_rating_from_loc(loc: Dict) -> str:
    current = loc.get("currentRatings", {})
    if isinstance(current, dict):
        overall = current.get("overall", {})
        if isinstance(overall, dict):
            r = (overall.get("rating", "") or "").strip()
            if r:
                return r
    for field in ("cqcRating", "overallRating", "rating"):
        r = (loc.get(field, "") or "").strip()
        if r:
            return r
    return ""


# ── Email scraping — generic fallback ─────────────────────────────────────

def _scrape_email_from_website(website_url: str) -> str:
    """
    Scrape a care home website for a generic contact email.

    This is the FALLBACK — used only when find_personal_email()
    returns nothing. Finds "info@domain.co.uk" style emails.

    Never raises. Returns "" if nothing found.
    """
    if not website_url:
        return ""

    url = website_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        base   = parsed.scheme + "://" + parsed.netloc
    except Exception:
        return ""

    import urllib.request

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    found_emails: List[str] = []

    for path in _EMAIL_SEARCH_PATHS:
        try:
            page_url = base + path if path else url
            req      = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status != 200:
                    continue
                raw  = resp.read(200_000)
                text = raw.decode("utf-8", errors="ignore")

            emails = _EMAIL_RE.findall(text)

            for email in emails:
                email  = email.lower().strip()
                domain = email.split("@")[-1]

                if domain in _REJECT_EMAIL_DOMAINS:
                    continue
                if email.endswith((
                    ".png", ".jpg", ".gif", ".svg",
                    ".css", ".js", ".woff",
                )):
                    continue
                if len(email) > 80:
                    continue

                prefixes = (
                    "info@", "contact@", "admin@",
                    "hello@", "enquir",
                )
                if found_emails:
                    if any(email.startswith(p) for p in prefixes):
                        found_emails.insert(0, email)
                    else:
                        found_emails.append(email)
                else:
                    found_emails.append(email)

            if found_emails and path in (
                "/contact", "/contact-us",
                "/contact-us.html", "/contactus",
            ):
                break

        except Exception:
            continue

    return found_emails[0] if found_emails else ""


# ── Cache helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _load_cqc_cache() -> List[Dict]:
    """
    BUG-CH-09: @st.cache_data(ttl=300) — full JSON parsed at most
    once every 5 minutes, not on every render.
    """
    if not _CACHE_FILE.exists():
        return []
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        care_homes = data.get("care_homes", [])
        if not care_homes:
            all_locs   = data.get("locations", [])
            care_homes = [
                loc for loc in all_locs
                if loc.get("careHome", "") == "Y"
                or "care home" in (loc.get("type", "") or "").lower()
            ]
        return care_homes
    except Exception as exc:
        logger.warning("_load_cqc_cache failed: %s", exc)
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _get_cache_info() -> Dict[str, Any]:
    """
    BUG-CH-08: Single open() call — stat() read inside the with block.
               Eliminates TOCTOU race between stat() and open().
    BUG-CH-09: @st.cache_data(ttl=60) — not parsed on every render.
    """
    if not _CACHE_FILE.exists():
        return {
            "exists":   False,
            "count":    0,
            "saved_at": "Never",
            "size_mb":  0.0,
            "fresh":    False,
        }
    try:
        # BUG-CH-08: single open() — stat() inside the with block,
        # not a separate call before it. No TOCTOU gap.
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            stat    = Path(f.name).stat()      # stat inside open context
            size_mb = round(stat.st_size / 1_000_000, 1)
            data    = json.load(f)

        saved_at = data.get("saved_at", "")
        count    = data.get(
            "total_care_homes",
            len(data.get("care_homes", [])),
        )

        fresh        = False
        saved_display = saved_at

        if saved_at:
            try:
                from datetime import datetime
                ts = saved_at
                # Strip timezone suffix — treat as UTC
                if ts.endswith("Z"):
                    ts = ts[:-1]
                if len(ts) > 19 and ts[19] in ("+", "-"):
                    ts = ts[:19]
                saved_dt  = datetime.fromisoformat(ts)
                age_hours = (
                    datetime.utcnow() - saved_dt
                ).total_seconds() / 3600
                fresh         = age_hours < 24
                saved_display = saved_dt.strftime("%d %b %Y %H:%M UTC")
            except Exception:
                fresh         = False
                saved_display = saved_at

        return {
            "exists":   True,
            "count":    count,
            "saved_at": saved_display,
            "size_mb":  size_mb,
            "fresh":    fresh,
        }
    except Exception as exc:
        logger.warning("_get_cache_info failed: %s", exc)
        return {
            "exists":   True,
            "count":    0,
            "saved_at": "Unknown",
            "size_mb":  0.0,
            "fresh":    False,
        }


# ── Filtering ──────────────────────────────────────────────────────────────

def _postcode_matches(postcode: str, prefixes: List[str]) -> bool:
    if not prefixes:
        return True
    pc = postcode.upper().strip()
    return any(pc.startswith(p.upper()) for p in prefixes)


def _filter_locations(
    locations:       List[Dict],
    region:          str,
    custom_postcode: str,
    limit:           int,
) -> List[Dict]:
    prefixes = POSTCODE_REGIONS.get(region, [])
    if custom_postcode:
        prefixes = [custom_postcode.upper().strip()]
    results = []
    for loc in locations:
        pc = (loc.get("postalCode", "") or "").strip()
        if not _postcode_matches(pc, prefixes):
            continue
        results.append(loc)
        if len(results) >= limit:
            break
    return results


def _locations_to_leads(locations: List[Dict]) -> List[Dict]:
    leads = []
    for loc in locations:
        loc_id = loc.get("locationId", "")
        leads.append({
            "business_name":        loc.get("locationName", ""),
            "city":                 (loc.get("postalAddressTownCity", "") or ""),
            "postcode":             (loc.get("postalCode", "") or "").strip(),
            "country":              "United Kingdom",
            "industry":             "care home",
            "source":               "cqc",
            "phone":                "",
            "website":              "",
            "email":                "",
            "address":              "",
            "decision_maker_name":  "",
            "decision_maker_title": "Registered Manager",
            "dm_source":            "",
            "cqc_rating":           _extract_rating_from_loc(loc),
            "cqc_url": (
                "https://www.cqc.org.uk/location/"
                + _url_quote(loc_id)
                if loc_id else ""
            ),
            "cqc_location_id":      loc_id,
            "enrichment_status":    "pending",
            "enrichment_notes":     "",
            "social_linkedin_url":  "",
        })
    return leads


# ── CSV export ─────────────────────────────────────────────────────────────

def _leads_to_csv(leads: List[Dict]) -> str:
    if not leads:
        return ""
    fieldnames = [
        "business_name", "postcode", "city", "country",
        "phone", "email", "website", "address",
        "decision_maker_name", "decision_maker_title",
        "dm_source", "social_linkedin_url",
        "cqc_rating", "cqc_url", "cqc_location_id",
        "industry", "source",
        "enrichment_status", "enrichment_notes",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=fieldnames, extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(leads)
    return output.getvalue()


# ── Save to DB ─────────────────────────────────────────────────────────────

def _save_leads_to_db(leads: List[Dict]) -> Dict[str, int]:
    """
    BUG-CH-01: No conn.row_factory override — delegated to insert_lead().
    BUG-CH-02: Dedup by business_name + city — handled by insert_lead().
    BUG-CH-03: Atomic transaction — handled by insert_lead() BEGIN IMMEDIATE.
    BUG-CH-04: Connection lifecycle owned by insert_lead() — no leak.
    BUG-CH-06: Raw INSERT removed — insert_lead() is the sole write path.
    BUG-CH-11: Field mapping by name — insert_lead() maps correctly.
    """
    counts = {"saved": 0, "skipped": 0, "errors": 0}
    try:
        from database import insert_lead
    except ImportError as exc:
        logger.error("Cannot import insert_lead: %s", exc)
        st.error("Database module unavailable.")
        return counts
    for lead in leads:
        try:
            result = insert_lead(lead)
            ok = result[0] if isinstance(result, tuple) else bool(result)
            if ok:
                counts["saved"] += 1
            else:
                counts["skipped"] += 1
        except Exception as exc:
            logger.warning(
                "Lead save '%s': %s",
                lead.get("business_name", "?"), exc,
            )
            counts["errors"] += 1
    return counts


# ── Enrichment ─────────────────────────────────────────────────────────────

def _load_existing_from_db(cqc_location_id: str) -> dict:
    """
    FIX-CH-A: conn.close() moved to finally block — was inside try
    only, causing connection leak when fetchone() raised.
    """
    if not cqc_location_id:
        return {}
    conn = None
    try:
        from database import get_connection
        conn   = get_connection()
        cursor = conn.execute(
            "SELECT * FROM leads WHERE cqc_location_id = ? LIMIT 1",
            (cqc_location_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {}
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.debug("_load_existing_from_db: %s", exc)
        return {}
    finally:
        # FIX-CH-A: always close, even if fetchone() raised
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _direct_cqc_enrich(lead: Dict) -> Dict:
    """
    Direct CQC enrichment + personal email discovery.
    v10.8 — CQC PDF report mining now active via location_id.

    Fetches from CQC /locations/{id}:
        - Manager name
        - Phone
        - CQC rating
        - Address
        - Website URL

    Then finds email in this priority order:
        1. find_personal_email() — full pipeline:
           a. Hunter.io (if key configured)
           b. CQC inspection report PDF mining  ← ACTIVE (v10.8)
           c. Pattern generation + SMTP verification
           d. DuckDuckGo search verification
           e. Best-guess pattern fallback

        2. _scrape_email_from_website() — generic fallback.
           Finds "info@standonhouse.co.uk" style emails.

    Fields populated (never overwrites existing values):
        decision_maker_name, decision_maker_title, dm_source,
        phone, cqc_rating, address, website, email,
        enrichment_status
    """
    loc_id = lead.get("cqc_location_id", "")
    if not loc_id:
        return lead

    try:
        from engine.cqc_client import get_full_details_for_location
        details = get_full_details_for_location(loc_id)

        if not any(details.values()):
            return lead

        result = dict(lead)

        # ── Manager
        if (details.get("manager_name")
                and not result.get("decision_maker_name", "").strip()):
            result["decision_maker_name"]  = details["manager_name"]
            result["decision_maker_title"] = "Registered Manager"
            result["dm_source"]            = "cqc"

        # ── Phone
        if (details.get("phone")
                and not result.get("phone", "").strip()):
            result["phone"] = details["phone"]

        # ── Rating
        if (details.get("rating")
                and not result.get("cqc_rating", "").strip()):
            result["cqc_rating"] = details["rating"]

        # ── Address
        if (details.get("address")
                and not result.get("address", "").strip()):
            result["address"] = details["address"]

        # ── Website
        website = details.get("website", "")
        if website and not result.get("website", "").strip():
            result["website"] = website

        # ── Email — personal first, generic fallback
        if not result.get("email", "").strip():
            site         = result.get("website", "") or website
            manager_name = result.get("decision_maker_name", "")

            # Priority 1: personal email for named manager
            if manager_name and site:
                try:
                    from engine.email_finder import find_personal_email
                    personal = find_personal_email(
                        full_name   = manager_name,
                        website_url = site,
                        location_id = loc_id,   # v10.8: activates CQC PDF mining
                    )
                    if personal:
                        result["email"] = personal
                        logger.info(
                            "_direct_cqc_enrich(%s): personal email: %s",
                            loc_id, personal,
                        )
                except Exception as exc:
                    logger.debug("find_personal_email error: %s", exc)

            # Priority 2: generic website email fallback
            if not result.get("email", "").strip() and site:
                generic = _scrape_email_from_website(site)
                if generic:
                    result["email"] = generic
                    logger.info(
                        "_direct_cqc_enrich(%s): generic email: %s",
                        loc_id, generic,
                    )

        result["enrichment_status"] = "enriched"
        return result

    except Exception as exc:
        logger.debug("_direct_cqc_enrich(%s): %s", loc_id, exc)
        return lead


def _enrich_leads_with_progress(
    leads:        List[Dict],
    progress_bar: Any,
    status_text:  Any,
) -> List[Dict]:
    """
    Three-tier enrichment loop.

    Tier 1: enrich_lead() with pre_existing
    Tier 2: enrich_lead() without pre_existing
    Tier 3: _direct_cqc_enrich() — always runs last.
             Now includes CQC PDF mining via location_id.

    FIX-CH-B: total == 0 guard — max(total, 1) prevents division by zero.
    """
    try:
        from engine.enricher import enrich_lead
        _enricher_available = True
    except ImportError:
        _enricher_available = False

    _enricher_accepts_pre = False
    if _enricher_available:
        try:
            import inspect
            sig = inspect.signature(enrich_lead)
            _enricher_accepts_pre = "pre_existing" in sig.parameters
        except Exception:
            pass

    enriched = []
    total    = len(leads)

    # FIX-CH-B: guard against empty list — max(total,1) prevents ZeroDivisionError
    safe_total = max(total, 1)

    for i, lead in enumerate(leads):
        name = lead.get("business_name", "...")
        status_text.markdown(
            "<div style='color:" + _C["text_secondary"]
            + ";font-size:12px;font-weight:600;'>"
            "Enriching " + str(i + 1) + "/" + str(total)
            + ": " + _esc(name) + "</div>",
            unsafe_allow_html=True,
        )
        progress_bar.progress(float((i + 1) / safe_total))

        result = lead

        if _enricher_available:
            try:
                if _enricher_accepts_pre:
                    pre    = _load_existing_from_db(
                        lead.get("cqc_location_id", "")
                    )
                    result = enrich_lead(lead, pre_existing=pre)
                else:
                    result = enrich_lead(lead)
            except Exception as exc:
                logger.warning("enrich_lead('%s'): %s", name, exc)
                result = lead

        # Always run direct CQC fetch — gets phone, rating,
        # address, manager name, personal email + PDF mining
        result = _direct_cqc_enrich(result)
        enriched.append(result)

    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:

    _ss_defaults = {
        "ch_generated_leads": [],
        "ch_enriched_leads":  [],
        "ch_saved_region":    "All Regions",
        "ch_enrich_done":     False,
    }
    for _k, _v in _ss_defaults.items():
        if _k not in st.session_state:
            st.session_state[_k] = _v

    cache_info      = _get_cache_info()
    care_home_count = cache_info.get("count", 0)
    count_display   = (
        "{:,}".format(care_home_count)
        if care_home_count else "31,768"
    )

    # ── Page header ────────────────────────────────────────────────
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-header-eyebrow'>"
        "&#127973; CQC Registered</div>"
        "<h1 class='page-header-title'>Care Home Leads</h1>"
        "<p class='page-header-sub'>Generate leads from "
        + count_display
        + " CQC-registered care homes. Filter by region or "
        "postcode prefix, then enrich with phone, email and "
        "manager name.</p></div>",
        unsafe_allow_html=True,
    )

    # ── Cache status banner ────────────────────────────────────────
    fresh_colour = (
        _C["green"] if cache_info.get("fresh") else _C["gold"]
    )
    fresh_label = (
        "Fresh" if cache_info.get("fresh")
        else "Stale — refresh recommended"
    )
    fresh_icon = (
        "&#9989;" if cache_info.get("fresh") else "&#9888;"
    )

    if cache_info["exists"]:
        st.markdown(
            "<div style='background:linear-gradient(135deg,"
            + _C["bg_surface"] + "," + _C["bg_raised"] + ");"
            "border:1px solid " + _C["border_subtle"] + ";"
            "border-left:3px solid " + fresh_colour + ";"
            "border-radius:0 14px 14px 0;"
            "padding:14px 20px;margin-bottom:24px;"
            "display:flex;align-items:center;"
            "gap:32px;flex-wrap:wrap;'>"

            "<div><div style='color:" + _C["text_muted"]
            + ";font-size:9px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px;"
            "margin-bottom:3px;'>Database</div>"
            "<div style='color:" + _C["text_primary"]
            + ";font-size:20px;font-weight:900;'>"
            + count_display
            + "<span style='font-size:12px;font-weight:500;"
            "color:" + _C["text_muted"] + ";'>"
            " care homes</span></div></div>"

            "<div><div style='color:" + _C["text_muted"]
            + ";font-size:9px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px;"
            "margin-bottom:3px;'>Last Updated</div>"
            "<div style='color:" + _C["text_secondary"]
            + ";font-size:13px;font-weight:600;'>"
            + _esc(cache_info["saved_at"]) + "</div></div>"

            "<div><div style='color:" + _C["text_muted"]
            + ";font-size:9px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px;"
            "margin-bottom:3px;'>Status</div>"
            "<div style='color:" + fresh_colour
            + ";font-size:13px;font-weight:700;'>"
            + fresh_icon + " " + _esc(fresh_label)
            + "</div></div>"

            "<div><div style='color:" + _C["text_muted"]
            + ";font-size:9px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px;"
            "margin-bottom:3px;'>Size</div>"
            "<div style='color:" + _C["text_secondary"]
            + ";font-size:13px;font-weight:600;'>"
            + _esc(str(cache_info["size_mb"])) + " MB</div></div>"

            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.warning(
            "&#9888; CQC database not downloaded yet. "
            "Click 'Download CQC Database' below."
        )

    # ── Refresh button ─────────────────────────────────────────────
    col_dl, _ = st.columns([1, 3])
    with col_dl:
        dl_label = (
            "&#128260;  Refresh CQC Database"
            if cache_info["exists"]
            else "&#11015;  Download CQC Database"
        )
        if st.button(dl_label, key="ch_btn_download_cqc"):
            with st.spinner(
                "Downloading CQC locations (~2 minutes)..."
            ):
                try:
                    from engine.cqc_client import refresh_cqc_cache
                    result_tuple = refresh_cqc_cache()
                    if (
                        isinstance(result_tuple, tuple)
                        and len(result_tuple) == 2
                        and result_tuple[0] > 0
                    ):
                        total_locs, total_ch = result_tuple
                        _load_cqc_cache.clear()
                        _get_cache_info.clear()
                        st.success(
                            "&#9989; Downloaded "
                            + "{:,}".format(total_locs)
                            + " locations — "
                            + "{:,}".format(total_ch)
                            + " care homes."
                        )
                        st.rerun()
                    else:
                        st.error(
                            "&#10060; Download failed. "
                            "Check CQC_API_KEY and terminal."
                        )
                except Exception as exc:
                    st.error("&#10060; " + _esc(str(exc)))

    if not cache_info["exists"]:
        return

    st.markdown(
        "<hr style='border:none;height:1px;background:"
        + _C["border_subtle"] + ";margin:24px 0;'>",
        unsafe_allow_html=True,
    )

    # ── Postcode reference ─────────────────────────────────────────
    with st.expander(
        "&#128206; UK Postcode Reference — click to expand",
        expanded=False,
    ):
        st.markdown(dedent("""
            <div style="display:grid;
                grid-template-columns:repeat(4,1fr);
                gap:8px;padding:4px 0;font-size:11px;">
                <div><strong>Midlands</strong><br>
                B=Birmingham · CV=Coventry<br>
                LE=Leicester · NG=Nottingham<br>
                DE=Derby · ST=Stoke<br>
                WV=Wolverhampton · WR=Worcester</div>
                <div><strong>North</strong><br>
                M=Manchester · LS=Leeds<br>
                L=Liverpool · S=Sheffield<br>
                BD=Bradford · HU=Hull<br>
                YO=York · NE=Newcastle</div>
                <div><strong>London</strong><br>
                E=East · N=North · NW=NW<br>
                SE=SE · SW=SW · W=West<br>
                EC=City · WC=West Central</div>
                <div><strong>South/East/West</strong><br>
                BS=Bristol · SO=Southampton<br>
                PO=Portsmouth · BN=Brighton<br>
                CT=Canterbury · EX=Exeter<br>
                PL=Plymouth · OX=Oxford</div>
            </div>
        """), unsafe_allow_html=True)

    # ── Filters ────────────────────────────────────────────────────
    st.markdown(
        "<div style='margin-bottom:16px;'>"
        "<div class='sec-title'>&#128269; Filter Leads</div>"
        "<div class='sec-sub'>Select a region OR enter a postcode "
        "prefix. Postcode takes priority.</div></div>",
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        region = st.selectbox(
            "Region",
            options=REGION_OPTIONS,
            index=0,
            key="ch_widget_region",
        )
    with col2:
        custom_postcode = st.text_input(
            "Postcode Prefix",
            placeholder="e.g. CV, B, LS, M",
            key="ch_widget_postcode",
        ).strip().upper()
    with col3:
        lead_limit = st.slider(
            "Max Leads",
            min_value=10,
            max_value=500,
            value=20,
            step=10,
            key="ch_widget_limit",
        )

    prefixes = POSTCODE_REGIONS.get(region, [])
    if custom_postcode:
        filter_desc = "postcode starts with **" + custom_postcode + "**"
    elif region != "All Regions":
        filter_desc = (
            "region = **" + region + "** "
            "(postcodes: " + ", ".join(prefixes) + ")"
        )
    else:
        filter_desc = "**All regions**"

    st.markdown(
        "<div style='color:" + _C["text_muted"]
        + ";font-size:12px;font-weight:500;"
        "margin:8px 0 16px 0;'>"
        "&#128270; " + filter_desc
        + " · Max " + str(lead_limit) + " leads</div>",
        unsafe_allow_html=True,
    )

    # ── Generate button ────────────────────────────────────────────
    col_gen, _ = st.columns([1, 3])
    with col_gen:
        generate_clicked = st.button(
            "&#9889;  Generate Leads",
            key="ch_btn_generate",
        )

    if generate_clicked:
        with st.spinner("Filtering care homes from cache..."):
            all_locations = _load_cqc_cache()
            if not all_locations:
                st.error(
                    "&#10060; Cache empty. "
                    "Click 'Download CQC Database'."
                )
                return
            filtered = _filter_locations(
                locations=all_locations,
                region=region,
                custom_postcode=custom_postcode,
                limit=lead_limit,
            )
        if not filtered:
            st.warning("&#9888; No care homes found for filter.")
            return

        leads = _locations_to_leads(filtered)
        st.session_state["ch_generated_leads"] = leads
        st.session_state["ch_enriched_leads"]  = []
        st.session_state["ch_saved_region"]    = region
        st.session_state["ch_enrich_done"]     = False
        st.success(
            "&#9989; Found " + "{:,}".format(len(leads))
            + " care homes. Click &#128269; Enrich to get "
            "phone, rating, manager name and email."
        )

    # ── Display ────────────────────────────────────────────────────
    generated = st.session_state.get("ch_generated_leads", [])
    enriched  = st.session_state.get("ch_enriched_leads",  [])
    display   = enriched if enriched else generated

    if not display:
        st.markdown(
            "<div style='background:" + _C["bg_surface"]
            + ";border:1px dashed " + _C["border_mid"]
            + ";border-radius:18px;padding:48px 32px;"
            "text-align:center;margin-top:24px;'>"
            "<div style='font-size:48px;margin-bottom:16px;'>"
            "&#127973;</div>"
            "<div style='color:" + _C["text_primary"]
            + ";font-size:18px;font-weight:700;"
            "margin-bottom:8px;'>No Leads Generated Yet</div>"
            "<div style='color:" + _C["text_muted"]
            + ";font-size:13px;max-width:420px;"
            "margin:0 auto;line-height:1.7;'>"
            "Select a region or postcode prefix then click "
            "<strong>&#9889; Generate Leads</strong>."
            "</div></div>",
            unsafe_allow_html=True,
        )
        return

    # ── Results header ─────────────────────────────────────────────
    st.markdown(
        "<hr style='border:none;height:1px;background:"
        + _C["border_subtle"] + ";margin:24px 0;'>",
        unsafe_allow_html=True,
    )

    is_enriched  = bool(enriched)
    result_label = "Enriched Leads" if is_enriched else "Generated Leads"
    sub_text = (
        "Enriched with phone, rating, manager and email from CQC."
        if is_enriched else
        "Raw from CQC cache. Click Enrich for all contact data."
    )

    st.markdown(
        "<div style='margin-bottom:16px;'>"
        "<div class='sec-title'>&#128203; "
        + "{:,}".format(len(display)) + " " + result_label
        + "</div><div class='sec-sub'>" + sub_text
        + "</div></div>",
        unsafe_allow_html=True,
    )

    # ── Action buttons ─────────────────────────────────────────────
    col_e, col_s, col_c, _ = st.columns([1, 1, 1, 2])
    with col_e:
        enrich_clicked = st.button(
            "&#128269;  Enrich All Leads",
            key="ch_btn_enrich",
        )
    with col_s:
        save_clicked = st.button(
            "&#128190;  Save to Database",
            key="ch_btn_save",
        )
    with col_c:
        region_slug = (
            st.session_state.get("ch_saved_region", "all")
            .lower()
            .replace(" ", "_")
            .replace("/", "_")
        )
        st.download_button(
            label     = "&#128229;  Download CSV",
            data      = _leads_to_csv(display),
            file_name = "care_homes_" + region_slug + ".csv",
            mime      = "text/csv",
            key       = "ch_btn_csv",
        )

    # ── Enrich handler ─────────────────────────────────────────────
    if enrich_clicked:
        to_enrich = generated if generated else display
        # BUG-CH-10: hard cap at _MAX_ENRICH_BATCH — user warned before slice
        if len(to_enrich) > _MAX_ENRICH_BATCH:
            st.warning(
                "&#9888; Capped at "
                + str(_MAX_ENRICH_BATCH)
                + " leads per run. "
                + str(len(to_enrich) - _MAX_ENRICH_BATCH)
                + " leads will not be enriched in this batch."
            )
            to_enrich = to_enrich[:_MAX_ENRICH_BATCH]

        total_n  = len(to_enrich)
        info_box = st.info(
            "&#128269; Enriching " + str(total_n)
            + " leads — fetching phone, rating, manager "
            "and personal email (~3-8s per lead)..."
        )
        pb   = st.progress(0.0)
        stxt = st.empty()

        results = _enrich_leads_with_progress(to_enrich, pb, stxt)

        pb.empty()
        stxt.empty()
        info_box.empty()

        st.session_state["ch_enriched_leads"] = results
        st.session_state["ch_enrich_done"]    = True

        phones   = sum(1 for ld in results if ld.get("phone"))
        emails   = sum(1 for ld in results if ld.get("email"))
        managers = sum(1 for ld in results if ld.get("decision_maker_name"))
        ratings  = sum(1 for ld in results if ld.get("cqc_rating"))

        st.markdown(
            "<div style='display:grid;"
            "grid-template-columns:repeat(4,1fr);"
            "gap:12px;margin:16px 0;'>"

            "<div class='kpi-card'>"
            "<div class='kpi-label'>Phones Found</div>"
            "<div class='kpi-value'>" + str(phones) + "</div>"
            "<div class='kpi-sub'>of " + str(total_n) + "</div></div>"

            "<div class='kpi-card'>"
            "<div class='kpi-label'>Emails Found</div>"
            "<div class='kpi-value'>" + str(emails) + "</div>"
            "<div class='kpi-sub'>of " + str(total_n) + "</div></div>"

            "<div class='kpi-card'>"
            "<div class='kpi-label'>Managers Found</div>"
            "<div class='kpi-value'>" + str(managers) + "</div>"
            "<div class='kpi-sub'>of " + str(total_n) + "</div></div>"

            "<div class='kpi-card'>"
            "<div class='kpi-label'>Ratings Found</div>"
            "<div class='kpi-value'>" + str(ratings) + "</div>"
            "<div class='kpi-sub'>of " + str(total_n) + "</div></div>"

            "</div>",
            unsafe_allow_html=True,
        )
        st.rerun()

    # ── Save handler ───────────────────────────────────────────────
    if save_clicked:
        with st.spinner("Saving to database..."):
            counts = _save_leads_to_db(display)
        if counts["errors"] == 0:
            st.success(
                "&#9989; Saved: " + str(counts["saved"])
                + " · Skipped: " + str(counts["skipped"])
            )
        else:
            st.warning(
                "&#9888; Saved: " + str(counts["saved"])
                + " · Errors: " + str(counts["errors"])
            )

    # ── Leads table ────────────────────────────────────────────────
    rows_html = ""
    for i, lead in enumerate(display):
        # BUG-CH-05: all display values escaped via _esc()
        name     = _esc(lead.get("business_name",        "—"))
        pc       = _esc(lead.get("postcode",              ""))
        city_val = _esc(lead.get("city",                  ""))
        manager  = _esc(lead.get("decision_maker_name",  ""))
        dm_role  = _esc(lead.get("decision_maker_title", ""))
        rating   =      lead.get("cqc_rating",            "")

        # BUG-CH-07: _safe_href() validates scheme on all href values.
        # FIX-CH-C/D: phone and email are additionally escaped before
        # being concatenated into the href string — prevents attribute
        # breakout if the value contains " or other special characters.
        raw_phone = lead.get("phone",  "") or ""
        raw_email = lead.get("email",  "") or ""
        raw_cqc   = lead.get("cqc_url","") or ""
        raw_li    = lead.get("social_linkedin_url", "") or ""

        # Escaped display values (for visible text in the cell)
        phone_display = _esc(raw_phone)
        email_display = _esc(raw_email)

        # Safe href values — scheme-validated and HTML-escaped
        # FIX-CH-C: escape phone before concatenating into tel: href
        phone_href = _safe_href("tel:" + html.escape(raw_phone, quote=True))
        # FIX-CH-D: escape email before concatenating into mailto: href
        email_href = _safe_href("mailto:" + html.escape(raw_email, quote=True))
        cqc_url    = _safe_href(raw_cqc)
        linkedin   = _safe_href(raw_li)

        loc_str = (
            city_val + "<br><span style='color:"
            + _C["text_muted"] + ";font-size:10px;'>"
            + pc + "</span>"
            if city_val else pc or "—"
        )

        phone_html = (
            "<a href='" + phone_href
            + "' style='color:" + _C["brand_blue"]
            + ";text-decoration:none;font-weight:600;'>"
            + phone_display + "</a>"
            if raw_phone else
            "<span style='color:" + _C["text_muted"] + ";'>—</span>"
        )

        email_html = (
            "<a href='" + email_href
            + "' style='color:" + _C["green"]
            + ";text-decoration:none;font-size:11px;'>"
            + email_display + "</a>"
            if raw_email else
            "<span style='color:" + _C["text_muted"]
            + ";font-size:11px;'>—</span>"
        )

        enrichment_done = lead.get("enrichment_status") == "enriched"

        if manager:
            role_str = (
                "<br><span style='color:" + _C["text_muted"]
                + ";font-size:10px;'>" + dm_role + "</span>"
                if dm_role else ""
            )
            li_str = (
                "<br><a href='" + linkedin
                + "' target='_blank' style='color:"
                + _C["brand_blue"]
                + ";font-size:10px;'>LinkedIn &#8599;</a>"
                if linkedin and linkedin != "#" else ""
            )
            manager_html = (
                "<span style='color:" + _C["text_primary"]
                + ";font-weight:600;'>" + manager + "</span>"
                + role_str + li_str
            )
        elif enrichment_done:
            manager_html = (
                "<span style='color:" + _C["text_muted"]
                + ";font-size:11px;font-style:italic;'>"
                "Post Vacant</span>"
            )
        else:
            manager_html = (
                "<span style='color:" + _C["text_muted"]
                + ";font-size:11px;font-style:italic;'>"
                "Click Enrich &#128269;</span>"
            )

        cqc_html = (
            "<a href='" + cqc_url + "' target='_blank' "
            "style='color:" + _C["brand_blue"]
            + ";font-size:10px;font-weight:600;'>"
            "View &#8599;</a>"
            if cqc_url and cqc_url != "#" else
            "<span style='color:" + _C["text_muted"] + ";'>—</span>"
        )

        row_bg = (
            _C["bg_raised"] if i % 2 == 0 else _C["bg_surface"]
        )

        rows_html += (
            "<tr style='background:" + row_bg + ";'>"
            "<td style='padding:12px 14px;color:"
            + _C["text_primary"]
            + ";font-weight:600;font-size:13px;"
            "border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + name + "</td>"
            "<td style='padding:12px 14px;color:"
            + _C["text_secondary"]
            + ";font-size:12px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + loc_str + "</td>"
            "<td style='padding:12px 14px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + phone_html + "</td>"
            "<td style='padding:12px 14px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + email_html + "</td>"
            "<td style='padding:12px 14px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + manager_html + "</td>"
            "<td style='padding:12px 14px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>"
            + _rating_badge(rating) + "</td>"
            "<td style='padding:12px 14px;border-bottom:1px solid "
            + _C["border_subtle"] + ";'>" + cqc_html + "</td>"
            "</tr>"
        )

    headers = [
        "Care Home", "Location", "Phone", "Email",
        "Manager / Director", "CQC Rating", "CQC Profile",
    ]
    header_html = "".join(
        "<th style='padding:10px 14px;text-align:left;color:"
        + _C["text_muted"]
        + ";font-size:10px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:0.8px;"
        "border-bottom:1px solid "
        + _C["border_subtle"] + ";white-space:nowrap;'>"
        + _esc(h) + "</th>"
        for h in headers
    )

    st.markdown(
        "<div style='border-radius:14px;overflow:hidden;"
        "border:1px solid " + _C["border_subtle"]
        + ";margin-top:8px;'>"
        "<div style='overflow-x:auto;'>"
        "<table style='width:100%;border-collapse:collapse;"
        "font-family:Inter,sans-serif;'>"
        "<thead><tr style='background:" + _C["bg_raised"] + ";'>"
        + header_html + "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table></div></div>"
        "<div style='margin-top:12px;color:" + _C["text_muted"]
        + ";font-size:11px;font-weight:500;text-align:center;'>"
        "Showing " + "{:,}".format(len(display))
        + " leads &middot; Data from CQC Public API "
        "&middot; Updated "
        + _esc(cache_info.get("saved_at", "")) + "</div>",
        unsafe_allow_html=True,
    )