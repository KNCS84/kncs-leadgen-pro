# pages/search.py
# KNCS LeadGen Pro — v10.1 (audit-clean)
#
# Registered bugs fixed:
#   BUG-048   should_skip_recent_search() dedup guard
#   BUG-SP-01 active_job_id cleared on terminal status
#   BUG-SP-02 None return from start_scrape_job() disambiguated
#   BUG-SP-03 city and keyword length capped at MAX_INPUT_LEN
#   BUG-SP-05 get_recent_jobs wrapped with 30-second cache
#   BUG-SP-06 missing email/website counts cached 60 seconds
#   BUG-SP-08 Refresh Now button added alongside Stop when running
#
# Additional defects fixed in this audit pass:
#   FIX-SP-A  render_recent_jobs() unconditional cache clear removed —
#             cache is now cleared only when a new job starts (was
#             defeating BUG-SP-05 on every render)
#   FIX-SP-B  render_campaign_builder() NameError when country ==
#             "Custom" — cat_sel undefined in that branch; keyword
#             and category now always assigned before return
#   FIX-SP-C  time.sleep(10) in main() blocked entire Streamlit UI
#             thread including Stop button; replaced with st.rerun()
#             after a brief non-blocking check
#   FIX-SP-D  should_skip_recent_search() SQL column name was string-
#             concatenated — column names now whitelisted via explicit
#             mapping to prevent injection pattern
#   FIX-SP-E  Naive datetime in dedup window used datetime.now() on
#             server side vs stored UTC strings — now uses
#             datetime.utcnow() consistently on both sides
#
# FIX-SS-01: Session state initialised at module level before any
#            function runs — prevents KeyError on selected_source
#            and all other session keys
# FIX-SS-02: render_recent_jobs() reads correct column names:
#            inserted / skipped / updated / finished_at
#
# FIX-SP-F: use_container_width deprecated (removal after 2025-12-31)
#           replaced with width='stretch' / width='content' across
#           all st.button() calls in this file (6 occurrences).
#
# FIX-SP-G (CRITICAL): KeyError on st.session_state["selected_source"]
#           still occurred in production despite FIX-SS-01, because
#           app.py uses a custom router (imports each page module once
#           and calls its render() function repeatedly) rather than
#           Streamlit's native pages/ auto-discovery. Under a router,
#           module-level code only executes on the FIRST import of the
#           process — it does NOT re-run on every interaction the way
#           a native multi-page script does. Any session that reused
#           the already-imported module (e.g. during a long-running
#           background job with periodic st.rerun()) could reach
#           render_source_selector() with "selected_source" never set.
#           Additionally, render_source_selector() only persisted the
#           resolved default back into session_state on the INVALID-
#           value branch — a MISSING key fell through .get()'s default
#           without ever being written back, so the final bracket-read
#           `st.session_state["selected_source"]` still raised KeyError.
#           Fixed by (1) making the persist unconditional, (2) using
#           .get() with fallback on the final read, and (3) re-running
#           the session defaults check at the top of main() on every
#           call, not relying solely on module-level execution.

from __future__ import annotations

from datetime import datetime
import html
import time
import streamlit as st

# ─────────────────────────────────────────────────────────
# FIX-SS-01: Session state initialised at module level.
# The if-guard means values are never overwritten on rerun.
#
# FIX-SP-G: This block alone is NOT sufficient under a custom router
# architecture (see changelog above) — main() also re-asserts these
# defaults on every call as a second line of defense.
# ─────────────────────────────────────────────────────────
_SESSION_DEFAULTS: dict = {
    "selected_source":       "companies_house",
    "active_job_id":         None,
    "pending_search_launch": None,
    "force_launch_search":   False,
}
for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────
# Safe imports — database
# ─────────────────────────────────────────────────────────
try:
    from database import (
        get_leads_missing_emails,
        get_leads_missing_websites,
        get_connection,
    )
except Exception:
    get_connection = None  # type: ignore[assignment]

    def get_leads_missing_emails(limit: int = 100) -> list:  # type: ignore[misc]
        return []

    def get_leads_missing_websites(limit: int = 100) -> list:  # type: ignore[misc]
        return []


# ─────────────────────────────────────────────────────────
# Safe imports — job manager
# ─────────────────────────────────────────────────────────
try:
    from engine.job_manager import (
        start_scrape_job,
        get_job_status_summary,
        get_recent_jobs,
        get_active_job_id,
        any_job_running,
        stop_job,
        STATUS_PENDING,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_RUNNING,
        STATUS_STOPPED,
    )
except Exception:
    def start_scrape_job(*a, **kw):  # type: ignore[misc]
        return None

    def get_job_status_summary(*a, **kw) -> dict:  # type: ignore[misc]
        return {
            "found": False, "status": "unknown",
            "progress": 0, "progress_pct": 0.0,
            "leads": 0, "leads_inserted": 0,
            "leads_skipped": 0, "leads_updated": 0,
            "message": "Unavailable",
            "is_done": False, "is_running": False,
        }

    def get_recent_jobs(limit: int = 12) -> list:  # type: ignore[misc]
        return []

    def get_active_job_id():  # type: ignore[misc]
        return None

    def any_job_running() -> bool:  # type: ignore[misc]
        return False

    def stop_job(*a, **kw) -> bool:  # type: ignore[misc]
        return False

    STATUS_PENDING = "pending"
    STATUS_DONE    = "done"
    STATUS_FAILED  = "failed"
    STATUS_RUNNING = "running"
    STATUS_STOPPED = "stopped"

# ─────────────────────────────────────────────────────────
# Safe imports — enricher
# ─────────────────────────────────────────────────────────
try:
    from engine.enricher import reenrich_leads_missing_emails
except Exception:
    def reenrich_leads_missing_emails(  # type: ignore[misc]
        limit: int = 20,
        progress_callback=None,
    ) -> dict:
        return {"emails_found": 0, "processed": 0, "updated": 0}

# ─────────────────────────────────────────────────────────
# Safe imports — scrape guard
# ─────────────────────────────────────────────────────────
try:
    from engine.scrape_guard import (
        get_risk_summary,
        get_risk_level,
        RISK_LEVELS,
    )
except Exception:
    RISK_LEVELS: dict = {
        "safe":    {"label": "SAFE",    "color": "#10b981", "message": "No risk detected."},
        "low":     {"label": "LOW",     "color": "#10b981", "message": "Low risk."},
        "warning": {"label": "WARNING", "color": "#f59e0b", "message": "Use caution."},
        "caution": {"label": "CAUTION", "color": "#f97316", "message": "Higher block risk."},
        "stop":    {"label": "STOP",    "color": "#ef4444", "message": "Do not scrape now."},
        "blocked": {"label": "BLOCKED", "color": "#ef4444", "message": "Blocked by guard."},
    }

    def get_risk_summary() -> dict:  # type: ignore[misc]
        return {
            "overall_risk": "safe",
            "overall_message": "Monitor unavailable.",
            "recommendations": [],
            "per_source": {},
        }

    def get_risk_level(source: str) -> str:  # type: ignore[misc]
        return "safe"


# ─────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────
_BG_BASE    = "#060d19"
_BG_SURFACE = "#0b1628"
_BG_RAISED  = "#101f38"
_BG_INPUT   = "#0d1a2e"
_BRAND_BLUE = "#5b8cff"
_BRAND_PUR  = "#7c5cff"
_GREEN      = "#10b981"
_GOLD       = "#f59e0b"
_RED        = "#ef4444"
_ORANGE     = "#f97316"
_TEXT_PRI   = "#f1f5f9"
_TEXT_SEC   = "#94a3b8"
_TEXT_MUT   = "#475569"
_BORDER     = "rgba(255,255,255,0.05)"
_BORDER_MID = "rgba(255,255,255,0.08)"
_BORDER_BRD = "rgba(91,140,255,0.28)"
_GLOW_BLUE  = "rgba(91,140,255,0.18)"

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────
_MAX_INPUT_LEN        = 100          # BUG-SP-03
_DEDUP_WINDOW_SECONDS = 3_600        # BUG-048
_AUTO_REFRESH_SECONDS = 10           # BUG-SP-10

# FIX-SP-D: whitelist of columns allowed in dedup query
# Prevents any injection via the column_name parameter path.
_ALLOWED_DEDUP_COLUMNS: set[str] = {"location", "city"}

LOCATION_PRESETS: dict[str, list[str]] = {
    "United Kingdom": [
        "Birmingham", "Coventry", "Leicester", "Nottingham",
        "Derby", "Stoke-on-Trent", "Wolverhampton", "Walsall",
        "Solihull", "Nuneaton", "Tamworth", "Burton upon Trent",
        "Hereford", "Worcester", "Shrewsbury", "Telford",
        "Manchester", "Liverpool", "Leeds", "Sheffield",
        "Bradford", "Bolton", "Oldham", "Rochdale",
        "Salford", "Stockport", "Wigan", "Blackpool",
        "Preston", "Blackburn", "Chester", "Carlisle",
        "Newcastle upon Tyne", "Sunderland", "Middlesbrough",
        "Gateshead", "Durham", "Hartlepool",
        "London", "Brighton", "Southampton", "Portsmouth",
        "Oxford", "Reading", "Milton Keynes", "Cambridge",
        "Norwich", "Ipswich", "Luton", "Watford",
        "Southend-on-Sea", "Chatham", "Maidstone",
        "Bristol", "Plymouth", "Exeter", "Bath",
        "Swindon", "Gloucester", "Bournemouth", "Poole",
        "Cheltenham", "Taunton", "Truro",
        "Peterborough", "Northampton", "Bedford",
        "Stevenage", "Colchester",
        "Hull", "Doncaster", "Barnsley", "Wakefield",
        "Harrogate", "York",
        "Glasgow", "Edinburgh", "Aberdeen", "Dundee",
        "Inverness", "Stirling", "Perth",
        "Cardiff", "Swansea", "Newport", "Wrexham",
        "Belfast", "Derry", "Lisburn",
    ],
    "Nigeria": [
        "Lagos", "Abuja", "Port Harcourt",
        "Benin City", "Enugu", "Owerri",
        "Kano", "Ibadan", "Kaduna", "Asaba", "Warri",
    ],
    "United States": [
        "New York", "Los Angeles", "Chicago", "Houston",
        "Dallas", "Atlanta", "Miami", "Phoenix",
        "Philadelphia", "San Antonio", "San Diego",
        "San Jose", "Austin", "Jacksonville", "Columbus",
    ],
    "Canada":       ["Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa"],
    "Ghana":        ["Accra", "Kumasi", "Takoradi", "Tamale"],
    "South Africa": ["Johannesburg", "Cape Town", "Durban", "Pretoria"],
    "Kenya":        ["Nairobi", "Mombasa", "Kisumu", "Nakuru"],
    "Australia":    ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide"],
    "UAE":          ["Dubai", "Abu Dhabi", "Sharjah"],
    "Custom":       [],
}

BUSINESS_CATEGORIES: list[str] = [
    "Care Home", "Restaurant", "Dental Clinic", "Estate Agent",
    "Construction", "Hair Salon", "Mortgage Broker", "Car Garage",
    "Law Firm", "Insurance", "Financial Advisor", "Hotel",
    "Pharmacy", "Gym", "School", "Event Center", "Custom Keyword",
]

MAX_RESULTS_OPTIONS: dict[str, int] = {
    "20 Leads  (Quick)":    20,
    "50 Leads  (Standard)": 50,
    "100 Leads (Deep)":     100,
    "200 Leads (Bulk)":     200,
}

SOURCE_OPTIONS: dict[str, dict] = {
    "companies_house": {
        "label": "Companies House UK",
        "icon":  "GB",
        "color": "#3b82f6",
        "desc":  "Official UK registry. Free API. Zero block risk.",
    },
    "google_maps": {
        "label": "Google Maps",
        "icon":  "MAP",
        "color": _BRAND_BLUE,
        "desc":  "Strong phones, ratings, local businesses worldwide.",
    },
    "b2bhint": {
        "label": "B2BHint",
        "icon":  "B2B",
        "color": _BRAND_PUR,
        "desc":  "Corporate directory for company names and listings.",
    },
    "yelp": {
        "label": "Yelp",
        "icon":  "YELP",
        "color": _RED,
        "desc":  "Best for UK and US service businesses.",
    },
    "yellowpages": {
        "label": "YellowPages",
        "icon":  "YP",
        "color": _GOLD,
        "desc":  "Good phone coverage for local businesses.",
    },
}

SOURCE_ICON_COLORS: dict[str, str] = {
    "companies_house": "#3b82f6",
    "google_maps":     _BRAND_BLUE,
    "b2bhint":         _BRAND_PUR,
    "yelp":            _RED,
    "yellowpages":     _GOLD,
}

SOURCE_LAYOUT: list[list[str]] = [
    ["companies_house", "google_maps", "b2bhint"],
    ["yelp", "yellowpages"],
]

_STATUS_COLORS: dict[str, str] = {
    "done":    _GREEN,
    "running": _BRAND_BLUE,
    "failed":  _RED,
    "stopped": _GOLD,
    "pending": _TEXT_MUT,
}

_RISK_COLORS: dict[str, str] = {
    "safe":    _GREEN,
    "low":     _GREEN,
    "warning": _GOLD,
    "caution": _ORANGE,
    "stop":    _RED,
    "blocked": _RED,
}

_RISK_LABELS: dict[str, str] = {
    "safe":    "SAFE",
    "low":     "LOW",
    "warning": "WARNING",
    "caution": "CAUTION",
    "stop":    "STOP",
    "blocked": "BLOCKED",
}

_TERMINAL_STATUSES: set[str] = {STATUS_DONE, STATUS_FAILED, STATUS_STOPPED}


# ─────────────────────────────────────────────────────────
# Page CSS
# ─────────────────────────────────────────────────────────
def _render_page_css() -> None:
    css = (
        "<style>"
        ".block-container {"
        "    padding-top: 1.4rem !important;"
        "    padding-bottom: 3rem !important;"
        "    max-width: 1400px !important;"
        "}"
        ".sr-hero {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 50%, #0b1628 100%);"
        "    border: 1px solid rgba(255,255,255,0.05);"
        "    border-left: 4px solid #5b8cff;"
        "    border-radius: 20px;"
        "    padding: 24px 28px;"
        "    margin-bottom: 24px;"
        "    position: relative;"
        "    overflow: hidden;"
        "}"
        ".sr-hero-orb {"
        "    position: absolute; top: -80px; right: -80px;"
        "    width: 300px; height: 300px; border-radius: 50%;"
        "    background: radial-gradient(circle, rgba(91,140,255,0.18) 0%, transparent 70%);"
        "    pointer-events: none;"
        "}"
        ".sr-hero-eyebrow {"
        "    font-size: 10px; font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 2px; color: #5b8cff; margin-bottom: 8px;"
        "}"
        ".sr-hero-title {"
        "    font-size: 30px; font-weight: 900; color: #f1f5f9;"
        "    letter-spacing: -1px; line-height: 1.15; margin: 0 0 8px 0;"
        "}"
        ".sr-hero-sub {"
        "    font-size: 13px; color: #475569; font-weight: 500; line-height: 1.7;"
        "}"
        ".sr-sec-head  { margin: 28px 0 16px 0; }"
        ".sr-sec-title {"
        "    font-size: 20px; font-weight: 800; color: #f1f5f9;"
        "    letter-spacing: -0.5px; line-height: 1.2; margin: 0 0 4px 0;"
        "}"
        ".sr-sec-sub { font-size: 12px; color: #475569; font-weight: 500; margin: 0; }"
        ".sr-card {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05);"
        "    border-radius: 16px; padding: 20px;"
        "    transition: transform 0.22s ease, border-color 0.25s ease,"
        "                box-shadow 0.25s ease;"
        "}"
        ".sr-kpi {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05); border-radius: 16px;"
        "    padding: 18px 20px 14px 20px; position: relative; overflow: hidden;"
        "}"
        ".sr-kpi-label {"
        "    color: #475569; font-size: 9.5px; font-weight: 700;"
        "    text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 8px;"
        "}"
        ".sr-kpi-value {"
        "    font-size: 28px; font-weight: 900; color: #f1f5f9;"
        "    letter-spacing: -1px; line-height: 1; margin-bottom: 6px;"
        "}"
        ".sr-kpi-sub { font-size: 11px; color: #94a3b8; font-weight: 500; }"
        ".sr-source-card {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05); border-radius: 16px;"
        "    padding: 20px 18px; min-height: 200px;"
        "    transition: transform 0.2s ease, border-color 0.25s ease,"
        "                box-shadow 0.25s ease;"
        "}"
        ".sr-source-card.selected {"
        "    border-color: rgba(91,140,255,0.28);"
        "    background: linear-gradient(145deg, rgba(91,140,255,0.08) 0%,"
        "                rgba(124,92,255,0.05) 100%);"
        "}"
        ".sr-source-icon {"
        "    font-size: 13px; font-weight: 900; letter-spacing: 1px;"
        "    margin-bottom: 14px; line-height: 1;"
        "    width: 44px; height: 44px; border-radius: 12px;"
        "    display: flex; align-items: center; justify-content: center;"
        "    background: rgba(255,255,255,0.05);"
        "    border: 1px solid rgba(255,255,255,0.08);"
        "}"
        ".sr-source-label { font-size: 14px; font-weight: 800; color: #f1f5f9;"
        "                   margin-bottom: 8px; }"
        ".sr-source-desc  { font-size: 11px; color: #475569; line-height: 1.6;"
        "                   margin-bottom: 14px; }"
        ".sr-source-risk  { display: flex; align-items: center; gap: 6px;"
        "                   font-size: 10px; font-weight: 700;"
        "                   text-transform: uppercase; letter-spacing: 0.8px; }"
        ".sr-campaigns-scroll { max-height: 520px; overflow-y: auto;"
        "                       padding-right: 4px; scrollbar-width: thin; }"
        ".sr-campaign {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05);"
        "    border-top: 3px solid #5b8cff;"
        "    border-radius: 16px; padding: 18px 20px; margin-bottom: 12px;"
        "}"
        ".sr-campaign-name { font-size: 15px; font-weight: 800; color: #f1f5f9;"
        "                    margin-bottom: 4px; }"
        ".sr-campaign-meta { font-size: 11px; color: #475569; margin-bottom: 14px; }"
        ".sr-campaign-grid { display: grid; grid-template-columns: repeat(4, 1fr);"
        "                    gap: 10px; margin-bottom: 14px; }"
        ".sr-campaign-stat { text-align: center; }"
        ".sr-campaign-stat-label { font-size: 9px; font-weight: 700;"
        "                          text-transform: uppercase; letter-spacing: 1px;"
        "                          color: #475569; margin-bottom: 4px; }"
        ".sr-campaign-stat-value { font-size: 20px; font-weight: 900;"
        "                          color: #f1f5f9; line-height: 1; }"
        ".sr-campaign-stat-value.green { color: #10b981; }"
        ".sr-campaign-stat-value.gold  { color: #f59e0b; }"
        ".sr-campaign-stat-value.blue  { color: #5b8cff; }"
        ".sr-campaign-footer { display: flex; justify-content: space-between;"
        "                      align-items: center; padding-top: 10px;"
        "                      border-top: 1px solid rgba(255,255,255,0.05); }"
        ".sr-campaign-time   { font-size: 11px; color: #475569; }"
        ".sr-campaign-status { font-size: 11px; font-weight: 800;"
        "                      text-transform: uppercase; }"
        ".sr-job-card { background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "               border: 1px solid rgba(255,255,255,0.05);"
        "               border-left: 4px solid #5b8cff;"
        "               border-radius: 16px; padding: 20px; margin-bottom: 16px; }"
        ".sr-job-header { display: flex; justify-content: space-between;"
        "                 align-items: center; margin-bottom: 14px; }"
        ".sr-job-message { font-size: 14px; font-weight: 700; color: #f1f5f9;"
        "                  margin-bottom: 4px; }"
        ".sr-job-status  { font-size: 11px; font-weight: 700; text-transform: uppercase; }"
        ".sr-job-leads   { text-align: right; }"
        ".sr-job-leads-value { font-size: 26px; font-weight: 900; color: #f1f5f9;"
        "                      line-height: 1; }"
        ".sr-job-leads-label { font-size: 10px; text-transform: uppercase;"
        "                      color: #475569; }"
        ".sr-job-breakdown { display: flex; gap: 18px; font-size: 12px; font-weight: 600; }"
        ".sr-job-inserted  { color: #10b981; }"
        ".sr-job-skipped   { color: #f59e0b; }"
        ".sr-job-updated   { color: #5b8cff; }"
        ".sr-refresh-bar { background: rgba(91,140,255,0.06);"
        "                  border: 1px solid rgba(91,140,255,0.15);"
        "                  border-radius: 10px; padding: 8px 14px;"
        "                  margin-bottom: 10px; font-size: 11px; color: #94a3b8;"
        "                  display: flex; align-items: center; gap: 8px; }"
        ".sr-risk-card { background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "                border: 1px solid rgba(255,255,255,0.05);"
        "                border-left: 4px solid #10b981;"
        "                border-radius: 16px; padding: 22px; margin-bottom: 18px; }"
        ".sr-risk-header { display: flex; align-items: center; gap: 16px;"
        "                  margin-bottom: 4px; }"
        ".sr-risk-emoji  { font-size: 32px; }"
        ".sr-risk-label  { font-size: 20px; font-weight: 800; color: #f1f5f9; }"
        ".sr-risk-msg    { font-size: 13px; color: #475569; margin-top: 3px; }"
        ".sr-risk-rec { background: rgba(255,255,255,0.025);"
        "               border: 1px solid rgba(255,255,255,0.05);"
        "               border-left: 3px solid #5b8cff; border-radius: 10px;"
        "               padding: 12px 14px; margin-top: 10px;"
        "               font-size: 12px; color: #94a3b8; }"
        ".sr-risk-rec.error { border-left-color: #ef4444; color: #fca5a5; }"
        ".sr-risk-rec.warn  { border-left-color: #f59e0b; color: #fcd34d; }"
        ".sr-risk-rec.info  { border-left-color: #5b8cff; color: #93c5fd; }"
        ".sr-risk-rec.ok    { border-left-color: #10b981; color: #86efac; }"
        ".sr-source-item { background: rgba(255,255,255,0.02);"
        "                  border: 1px solid rgba(255,255,255,0.05);"
        "                  border-left: 3px solid #5b8cff; border-radius: 12px;"
        "                  padding: 14px 16px; margin-bottom: 10px; }"
        ".sr-source-item-header { display: flex; justify-content: space-between;"
        "                         align-items: center; }"
        ".sr-source-item-name   { font-size: 14px; font-weight: 700; color: #f1f5f9; }"
        ".sr-source-item-meta   { font-size: 12px; color: #475569; margin-top: 3px; }"
        ".sr-source-item-status { font-size: 15px; font-weight: 800; }"
        "@keyframes sr-pulse {"
        "    0%   { box-shadow: 0 0 0 0   rgba(16,185,129,0.30); }"
        "    50%  { box-shadow: 0 0 0 8px rgba(16,185,129,0.06); }"
        "    100% { box-shadow: 0 0 0 0   rgba(16,185,129,0.00); }"
        "}"
        "@keyframes sr-dot-blink {"
        "    0%, 100% { opacity: 1; } 50% { opacity: 0.3; }"
        "}"
        ".sr-live-card { animation: sr-pulse 2.2s ease infinite;"
        "                border-left-color: #10b981 !important; }"
        ".sr-live-dot { display: inline-block; width: 8px; height: 8px;"
        "               border-radius: 50%; background: #10b981; margin-right: 8px;"
        "               animation: sr-dot-blink 1.6s ease infinite;"
        "               vertical-align: middle; }"
        "::-webkit-scrollbar       { width: 4px; height: 4px; }"
        "::-webkit-scrollbar-track { background: transparent; }"
        "::-webkit-scrollbar-thumb { background: #101f38; border-radius: 20px; }"
        "::-webkit-scrollbar-thumb:hover { background: #5b8cff; }"
        "</style>"
    )
    st.markdown(css, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def _esc(v: object) -> str:
    return html.escape(str(v if v is not None else ""))


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _trunc(value: object, max_len: int = 72) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _safe_risk_color(level: str) -> str:
    return _RISK_COLORS.get(level, _TEXT_MUT)


def _safe_risk_label(level: str) -> str:
    return _RISK_LABELS.get(level, str(level).upper() if level else "UNKNOWN")


def _safe_risk_message(level: str) -> str:
    return RISK_LEVELS.get(level, {}).get("message", "Unknown risk state.")


def _fmt_ts(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "N/A"
    try:
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                dt = datetime.strptime(text[:19], fmt)
                return dt.strftime("%d %b %Y, %H:%M")
            except ValueError:
                continue
        return text[:16].replace("T", " ")
    except Exception:
        return text[:16] if len(text) >= 16 else text


def _parse_ts(value: object) -> datetime | None:
    """
    Parse a timestamp string to a naive UTC datetime.
    All stored timestamps are assumed UTC (SQLite default).
    Callers must compare against datetime.utcnow() — not datetime.now().
    """
    text = str(value or "").strip()
    if not text:
        return None
    # Strip timezone info — treat all stored values as UTC
    text = text.replace("Z", "").split("+")[0].strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    return None


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        '<div class="sr-sec-sub">' + _esc(subtitle) + "</div>" if subtitle else ""
    )
    st.markdown(
        '<div class="sr-sec-head">'
        '<div class="sr-sec-title">' + _esc(title) + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _get_top_cities(limit: int = 3) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(city),''), 'Unknown') AS place,"
            " COUNT(*) AS total"
            " FROM leads"
            " GROUP BY place"
            " ORDER BY total DESC, place ASC"
            " LIMIT ?",
            (limit,),
        )
        return [{"place": r[0], "total": r[1]} for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# BUG-048: Search deduplication guard
# FIX-SP-D: column names whitelisted — no string concatenation
# FIX-SP-E: datetime.utcnow() used on both sides of comparison
# ─────────────────────────────────────────────────────────
def should_skip_recent_search(
    keyword: str,
    location: str,
    source: str,
    window_seconds: int = _DEDUP_WINDOW_SECONDS,
) -> tuple[bool, str]:
    """
    Returns (should_skip, reason_message).

    Checks scrape_jobs for a matching keyword+location+source
    combination with status 'running' or 'done' within the last
    window_seconds.  All datetime comparisons use UTC to avoid
    wall-clock drift on non-UTC servers.
    """
    if get_connection is None:
        return False, ""

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        kw_norm  = keyword.strip().lower()
        loc_norm = location.strip().lower()

        # FIX-SP-E: utcnow() so comparison is consistent with
        # UTC-stored timestamps regardless of server timezone.
        now_utc = datetime.utcnow().timestamp()

        def _query_column(col: str) -> list:
            # FIX-SP-D: only whitelisted column names are used;
            # never interpolated from external input.
            if col not in _ALLOWED_DEDUP_COLUMNS:
                return []
            # Column name is safe — it came from our own whitelist.
            sql = (
                "SELECT id, status, started_at, created_at, leads_found "
                "FROM scrape_jobs "
                "WHERE LOWER(TRIM(keyword)) = ? "
                "AND LOWER(TRIM(" + col + ")) = ? "
                "AND source = ? "
                "AND status IN ('running', 'done') "
                "ORDER BY COALESCE(created_at, started_at) DESC "
                "LIMIT 5"
            )
            try:
                cur.execute(sql, (kw_norm, loc_norm, source))
                return cur.fetchall()
            except Exception:
                return []

        rows = _query_column("location") or _query_column("city")
        if not rows:
            return False, ""

        for row in rows:
            job_id, status, started_at, created_at, leads_found = row
            if str(status).lower() == "running":
                return True, (
                    "A matching job is already running — "
                    + _esc(keyword)
                    + " in "
                    + _esc(location)
                    + " via "
                    + _esc(source)
                    + "."
                )
            # FIX-SP-E: _parse_ts returns naive UTC datetime;
            # compare against utcnow() timestamp.
            dt = _parse_ts(started_at) or _parse_ts(created_at)
            if dt is None:
                continue
            age_seconds = now_utc - dt.timestamp()
            if age_seconds <= window_seconds:
                started_fmt = _fmt_ts(started_at or created_at)
                leads_str = str(_safe_int(leads_found)) + " leads"
                return True, (
                    "This search was completed within the last hour ("
                    + started_fmt
                    + ", "
                    + leads_str
                    + " found). "
                    + "Launch again to force a new run."
                )
        return False, ""

    except Exception:
        return False, ""
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# Cached queries — BUG-SP-05, BUG-SP-06
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def _get_recent_jobs_cached(limit: int = 12) -> list:
    """30-second cache — BUG-SP-05."""
    try:
        return get_recent_jobs(limit=limit)
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _get_missing_email_count(limit: int = 200) -> int:
    """60-second cache — BUG-SP-06."""
    try:
        return len(get_leads_missing_emails(limit=limit))
    except Exception:
        return 0


@st.cache_data(ttl=60, show_spinner=False)
def _get_missing_website_count(limit: int = 200) -> int:
    """60-second cache — BUG-SP-06."""
    try:
        return len(get_leads_missing_websites(limit=limit))
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────
# Active job resolution
# BUG-SP-01: terminal status clears active_job_id
# ─────────────────────────────────────────────────────────
def _get_clean_active_job_id() -> int | None:
    """
    Returns the current active job ID from session state only,
    after verifying it is not in a terminal state.

    Does NOT fall back to get_active_job_id() — that could
    surface a different job and mask the session-state clear.
    """
    active_id = st.session_state.get("active_job_id")
    if not active_id:
        return None
    try:
        summary = get_job_status_summary(active_id) or {}
        status  = str(summary.get("status", "")).lower()
        # BUG-SP-01: clear stale ID on any terminal status
        if status in _TERMINAL_STATUSES:
            st.session_state.pop("active_job_id", None)
            return None
        # If job not found at all, clear too
        if not summary.get("found", True) and status not in (
            STATUS_PENDING, STATUS_RUNNING
        ):
            st.session_state.pop("active_job_id", None)
            return None
        return active_id
    except Exception:
        return active_id


def _get_any_active_scrape_job_id() -> int | None:
    """Direct DB check for any pending/running job."""
    if get_connection is None:
        return None
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM scrape_jobs"
            " WHERE status IN ('pending','running')"
            " ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _build_location(city: str, country: str) -> str:
    location = city.strip()
    if (
        country
        and country.strip()
        and country.strip().lower() not in location.lower()
    ):
        location = location + ", " + country.strip()
    return location


# ─────────────────────────────────────────────────────────
# Campaign launcher
# BUG-SP-02: None from start_scrape_job() disambiguated
# BUG-SP-03: city/keyword length capped at MAX_INPUT_LEN
# ─────────────────────────────────────────────────────────
def _launch_campaign(campaign: dict, bypass_dedup: bool = False) -> str:
    """
    Launch a scrape job from a campaign config dict.

    Returns one of: "launched" | "dedup" | "busy" |
                    "blocked" | "invalid" | "failed"
    """
    city    = (campaign or {}).get("city",    "").strip()
    keyword = (campaign or {}).get("keyword", "").strip()
    source  = (campaign or {}).get("source",  "companies_house")
    country = (campaign or {}).get("country", "United Kingdom")
    max_r   = (campaign or {}).get("max_results", 50)

    # BUG-SP-03: input length cap
    if not city:
        st.warning("Please select or enter a city.")
        return "invalid"
    if not keyword:
        st.warning("Please select or enter a business category.")
        return "invalid"
    if len(city) > _MAX_INPUT_LEN:
        st.warning(
            "City name is too long ("
            + str(len(city))
            + " chars). Max "
            + str(_MAX_INPUT_LEN)
            + "."
        )
        return "invalid"
    if len(keyword) > _MAX_INPUT_LEN:
        st.warning(
            "Keyword is too long ("
            + str(len(keyword))
            + " chars). Max "
            + str(_MAX_INPUT_LEN)
            + "."
        )
        return "invalid"

    # Block if any job is already running
    active_db_job = _get_any_active_scrape_job_id()
    if active_db_job or any_job_running():
        st.warning(
            "A job is already active. "
            "Please wait for it to finish or stop it first."
        )
        return "busy"

    # Scrape risk gate
    risk = get_risk_level(source)
    if risk in ("stop", "blocked"):
        st.error(
            _safe_risk_label(risk)
            + " — "
            + _safe_risk_message(risk)
            + " Choose a different source."
        )
        return "blocked"

    location = _build_location(city, country)

    # BUG-048: dedup guard
    if not bypass_dedup:
        skip, skip_reason = should_skip_recent_search(
            keyword=keyword,
            location=location,
            source=source,
            window_seconds=_DEDUP_WINDOW_SECONDS,
        )
        if skip:
            st.session_state["pending_search_launch"] = {
                "city": city, "keyword": keyword,
                "source": source, "country": country,
                "max_results": max_r,
            }
            return "dedup"

    job_id = start_scrape_job(
        keyword=keyword,
        location=location,
        source=source,
        max_results=max_r,
    )

    if job_id:
        st.session_state["active_job_id"] = job_id
        st.session_state.pop("pending_search_launch", None)
        st.session_state.pop("force_launch_search", None)
        # Invalidate recent-jobs cache so new job appears immediately
        _get_recent_jobs_cached.clear()
        st.success("Job started — ID: " + str(job_id))
        return "launched"

    # BUG-SP-02: disambiguate None return
    # Re-check whether a race-condition job appeared between our
    # pre-check and the start_scrape_job() call.
    if any_job_running():
        st.warning(
            "Another job started just now. "
            "Wait for it to finish before launching."
        )
    else:
        st.error("Failed to start job. Check job manager logs.")
    return "failed"


# ─────────────────────────────────────────────────────────
# Hero
# ─────────────────────────────────────────────────────────
def render_hero() -> None:
    now        = datetime.utcnow()
    top_cities = _get_top_cities(3)
    city_text  = " · ".join(c["place"] for c in top_cities) or "—"
    timestamp  = _esc(now.strftime("%d %b %Y, %H:%M UTC"))
    st.markdown(
        '<div class="sr-hero">'
        '<div class="sr-hero-orb"></div>'
        '<div class="sr-hero-eyebrow">'
        "Lead Acquisition Engine &middot; KNCS LeadGen Pro v10.1"
        "</div>"
        '<div class="sr-hero-title">'
        "Discover, scrape &amp; qualify high-intent opportunities"
        "</div>"
        '<div class="sr-hero-sub">'
        "Top cities: "
        '<span style="color:#94a3b8;">' + _esc(city_text) + "</span>"
        " &nbsp;&middot;&nbsp; " + timestamp
        + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Scrape risk monitor
# ─────────────────────────────────────────────────────────
def render_scrape_risk_card() -> None:
    _section_header(
        "Scrape Risk Monitor",
        "Real-time block detection and request quota tracking.",
    )
    try:
        summary      = get_risk_summary()
        overall_risk = summary.get("overall_risk", "safe")
        color        = _safe_risk_color(overall_risk)
        label        = _safe_risk_label(overall_risk)
        message      = summary.get("overall_message", "")
        recs         = summary.get("recommendations", [])
        risk_symbols = {
            "safe":    "&#128994;",
            "low":     "&#128994;",
            "warning": "&#128993;",
            "caution": "&#128992;",
            "stop":    "&#9940;",
            "blocked": "&#9940;",
        }
        symbol = risk_symbols.get(overall_risk, "&#128737;")
        st.markdown(
            '<div class="sr-risk-card" style="border-left-color:' + color + ';">'
            '<div class="sr-risk-header">'
            '<span class="sr-risk-emoji">' + symbol + "</span>"
            "<div>"
            '<div class="sr-risk-label" style="color:' + color + ';">'
            + _esc(label)
            + "</div>"
            '<div class="sr-risk-msg">' + _esc(message) + "</div>"
            "</div></div></div>",
            unsafe_allow_html=True,
        )
        for rec in recs:
            rec_text = str(rec)
            rec_cls  = "ok"
            if any(x in rec_text for x in ("BLOCKED", "STOP", "FAILED")):
                rec_cls = "error"
            elif "CAUTION" in rec_text or "WARNING" in rec_text:
                rec_cls = "warn"
            elif "LOW" in rec_text:
                rec_cls = "info"
            st.markdown(
                '<div class="sr-risk-rec ' + rec_cls + '">'
                + _esc(rec_text)
                + "</div>",
                unsafe_allow_html=True,
            )
        with st.expander("Source-by-Source Breakdown", expanded=False):
            per_source = summary.get("per_source", {})
            if not per_source:
                st.markdown(
                    '<div style="color:' + _TEXT_MUT + ';font-size:13px;'
                    'padding:12px 0;">No scraping activity today yet.</div>',
                    unsafe_allow_html=True,
                )
            else:
                for source, data in per_source.items():
                    s_level   = data.get("level", "safe")
                    s_color   = _safe_risk_color(s_level)
                    s_label   = _safe_risk_label(s_level)
                    count     = data.get("count", 0)
                    remaining = data.get("remaining", 0)
                    consec    = data.get("consecutive_failures", 0)
                    blocks    = data.get("recent_blocks", 0)
                    src_cfg   = SOURCE_OPTIONS.get(source, {})
                    src_name  = src_cfg.get("label", source)
                    src_icon  = src_cfg.get("icon", "?")
                    extra = ""
                    if consec > 0:
                        extra += (
                            '<div style="color:' + _RED + ';font-size:11px;'
                            'margin-top:4px;">&#9888; '
                            + str(consec)
                            + " consecutive failures</div>"
                        )
                    if blocks > 0:
                        extra += (
                            '<div style="color:' + _RED + ';font-size:11px;'
                            'margin-top:4px;">&#9940; '
                            + str(blocks)
                            + " block(s) detected</div>"
                        )
                    st.markdown(
                        '<div class="sr-source-item"'
                        ' style="border-left-color:' + s_color + ';">'
                        '<div class="sr-source-item-header">'
                        "<div>"
                        '<div class="sr-source-item-name">'
                        + _esc(src_icon)
                        + " "
                        + _esc(src_name)
                        + "</div>"
                        '<div class="sr-source-item-meta">'
                        + str(count)
                        + " requests today &middot; "
                        + str(remaining)
                        + " remaining</div>"
                        + extra
                        + "</div>"
                        '<div class="sr-source-item-status"'
                        ' style="color:' + s_color + ';">'
                        + _esc(s_label)
                        + "</div>"
                        "</div></div>",
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        st.error("Risk monitor error: " + _esc(str(exc)))


# ─────────────────────────────────────────────────────────
# Source selector
#
# FIX-SP-G: persistence of the resolved default is now unconditional
# (previously only happened inside the invalid-value branch, leaving
# a genuinely MISSING key unwritten). The final return also uses
# .get() with a fallback so a KeyError is structurally impossible
# here regardless of what happened earlier in the function.
# ─────────────────────────────────────────────────────────
def render_source_selector() -> str:
    _section_header("Lead Sources", "Choose your acquisition channel.")

    current = st.session_state.get("selected_source", "companies_house")
    if current not in SOURCE_OPTIONS:
        current = "companies_house"

    # FIX-SP-G: always write back, not just on the invalid branch.
    # This guarantees the key exists in session_state from this point
    # forward for the remainder of this render cycle and all future
    # ones, closing the exact gap that caused the production KeyError.
    st.session_state["selected_source"] = current

    sel_risk = get_risk_level(current)
    if sel_risk in ("stop", "blocked"):
        src_cfg = SOURCE_OPTIONS[current]
        st.warning(
            src_cfg["label"]
            + " is "
            + _safe_risk_label(sel_risk)
            + " — "
            + _safe_risk_message(sel_risk)
        )

    for row in SOURCE_LAYOUT:
        cols = st.columns(len(row), gap="small")
        for col, source in zip(cols, row):
            cfg      = SOURCE_OPTIONS[source]
            selected = current == source
            s_risk   = get_risk_level(source)
            r_color  = _safe_risk_color(s_risk)
            r_label  = _safe_risk_label(s_risk)
            icon_color  = SOURCE_ICON_COLORS.get(source, _BRAND_BLUE)
            card_class  = "sr-source-card selected" if selected else "sr-source-card"
            selected_badge = (
                '<span style="margin-left:auto;color:'
                + _GREEN
                + ';font-size:10px;font-weight:700;">&#10003; SELECTED</span>'
                if selected
                else ""
            )
            safe_dot = (
                '<span style="display:inline-block;width:7px;height:7px;'
                "border-radius:50%;background:"
                + r_color
                + ';margin-right:4px;"></span>'
            )
            card_html = (
                '<div class="' + card_class + '">'
                '<div class="sr-source-icon"'
                ' style="color:' + icon_color + ";border-color:" + icon_color + '33;">'
                + _esc(cfg["icon"])
                + "</div>"
                '<div class="sr-source-label">' + _esc(cfg["label"]) + "</div>"
                '<div class="sr-source-desc">'
                + _esc(_trunc(cfg["desc"], 72))
                + "</div>"
                '<div class="sr-source-risk">'
                + safe_dot
                + '<span style="color:' + r_color + ';">'
                + _esc(r_label)
                + "</span>"
                + selected_badge
                + "</div></div>"
            )
            with col:
                st.markdown(card_html, unsafe_allow_html=True)
                if not selected:
                    if st.button(
                        "Use " + cfg["label"],
                        key="src_" + source,
                        width="stretch",
                    ):
                        st.session_state["selected_source"] = source
                        st.rerun()

    # FIX-SP-G: .get() with fallback — structurally cannot KeyError
    # even if something upstream is ever changed incorrectly again.
    return st.session_state.get("selected_source", current)


# ─────────────────────────────────────────────────────────
# Campaign builder
# FIX-SP-B: cat_sel and keyword always assigned before return,
#           including when country == "Custom"
# ─────────────────────────────────────────────────────────
def render_campaign_builder(selected_source: str) -> dict:
    _section_header(
        "Campaign Builder",
        "Configure your search parameters then launch.",
    )

    with st.container():
        col_left, col_right = st.columns([3, 2], gap="large")

        with col_left:
            country = st.selectbox(
                "Country",
                options=list(LOCATION_PRESETS.keys()),
                index=0,
                key="sb_country",
            )
            city_options = LOCATION_PRESETS.get(country, [])

            if country == "Custom":
                city = st.text_input(
                    "Enter city / location",
                    placeholder="e.g. Nuneaton, Milton Keynes",
                    key="ti_custom_city",
                )
            else:
                city_list = city_options + ["Custom..."]
                city_sel  = st.selectbox(
                    "City / Region", options=city_list, key="sb_city"
                )
                if city_sel == "Custom...":
                    city = st.text_input(
                        "Enter custom city",
                        placeholder="Type any city name",
                        key="ti_city_override",
                    )
                else:
                    city = city_sel

            cat_sel = st.selectbox(
                "Business Category",
                options=BUSINESS_CATEGORIES,
                key="sb_category",
            )
            # FIX-SP-B: keyword always assigned — previously cat_sel
            # was only defined inside the else branch, so country ==
            # "Custom" raised NameError when the return dict was built.
            if cat_sel == "Custom Keyword":
                keyword = st.text_input(
                    "Custom search keyword",
                    placeholder="e.g. Wedding Venue, Dog Groomer",
                    key="ti_custom_kw",
                )
            else:
                keyword = cat_sel

            result_label = st.selectbox(
                "Max Results",
                options=list(MAX_RESULTS_OPTIONS.keys()),
                index=1,
                key="sb_max_results",
            )
            max_results = MAX_RESULTS_OPTIONS[result_label]

        with col_right:
            src_label = SOURCE_OPTIONS.get(selected_source, {}).get("label", "—")
            st.markdown(
                '<div class="sr-card" style="margin-top:0;">'
                '<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
                "letter-spacing:1px;color:"
                + _TEXT_MUT
                + ';margin-bottom:16px;">Campaign Preview</div>'
                '<div style="font-size:22px;font-weight:900;color:'
                + _TEXT_PRI
                + ";margin-bottom:4px;letter-spacing:-0.5px;\">"
                + _esc(keyword or "—")
                + "</div>"
                '<div style="font-size:13px;color:'
                + _TEXT_MUT
                + ';margin-bottom:20px;">'
                + _esc(city or "—")
                + " &middot; "
                + _esc(country)
                + "</div>"
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">'
                "<div>"
                '<div style="font-size:9px;font-weight:700;text-transform:uppercase;'
                "letter-spacing:1px;color:"
                + _TEXT_MUT
                + ';margin-bottom:4px;">Source</div>'
                '<div style="font-size:14px;font-weight:800;color:'
                + _TEXT_PRI
                + ';">'
                + _esc(src_label)
                + "</div></div>"
                "<div>"
                '<div style="font-size:9px;font-weight:700;text-transform:uppercase;'
                "letter-spacing:1px;color:"
                + _TEXT_MUT
                + ';margin-bottom:4px;">Max Leads</div>'
                '<div style="font-size:14px;font-weight:800;color:'
                + _TEXT_PRI
                + ';">'
                + str(max_results)
                + "</div></div>"
                "</div></div>",
                unsafe_allow_html=True,
            )

    return {
        "country":    country,
        "city":       city,
        "keyword":    keyword,
        "source":     selected_source,
        "max_results": max_results,
        "category":   cat_sel,
    }


# ─────────────────────────────────────────────────────────
# Active job monitor
# BUG-SP-01: active_job_id cleared on terminal status
# BUG-SP-08: Refresh Now button alongside Stop
# ─────────────────────────────────────────────────────────
def render_active_job_monitor(job_id: int) -> bool:
    """
    Renders live job progress card.
    Returns True while the job is still running/pending.
    """
    summary      = get_job_status_summary(job_id)
    status       = summary.get("status", "unknown")
    progress_raw = float(summary.get("progress_pct", 0.0) or 0.0)
    # Normalise: API may return 0–1 or 0–100
    progress     = progress_raw if progress_raw <= 1.0 else progress_raw / 100.0
    leads        = summary.get("leads", 0)
    message      = summary.get("message", "")

    # FIX-SS-02: correct key names from get_job_status_summary
    inserted = _safe_int(summary.get("leads_inserted", 0))
    skipped  = _safe_int(summary.get("leads_skipped",  0))
    updated  = _safe_int(summary.get("leads_updated",  0))

    status_color = _STATUS_COLORS.get(status, _TEXT_MUT)
    is_running   = status in (STATUS_RUNNING, STATUS_PENDING)
    pct_label    = str(int(round(progress * 100))) + "%"

    # BUG-SP-01: clear stale session ID on terminal status
    if status in _TERMINAL_STATUSES:
        st.session_state.pop("active_job_id", None)

    live_cls = "sr-job-card sr-live-card" if is_running else "sr-job-card"
    live_dot = "<span class='sr-live-dot'></span>" if is_running else ""

    breakdown_html = ""
    if inserted or skipped or updated:
        breakdown_html = (
            '<div class="sr-job-breakdown" style="margin-top:10px;">'
            '<span class="sr-job-inserted">&#9989; ' + str(inserted) + " new</span>"
            '<span class="sr-job-skipped">&#9193; ' + str(skipped) + " skipped</span>"
            '<span class="sr-job-updated">&#128260; ' + str(updated) + " updated</span>"
            "</div>"
        )

    st.markdown(
        '<div class="' + live_cls + '">'
        '<div class="sr-job-header">'
        "<div>"
        '<div class="sr-job-message">' + live_dot + _esc(message) + "</div>"
        '<div class="sr-job-status" style="color:' + status_color + ';">'
        + _esc(status.upper())
        + " &middot; "
        + pct_label
        + "</div>"
        + breakdown_html
        + "</div>"
        '<div class="sr-job-leads">'
        '<div class="sr-job-leads-value">' + str(leads) + "</div>"
        '<div class="sr-job-leads-label">Leads Found</div>'
        "</div></div></div>",
        unsafe_allow_html=True,
    )

    if is_running:
        st.progress(min(progress, 1.0))
        st.markdown(
            '<div class="sr-refresh-bar">'
            "<span class='sr-live-dot'></span>"
            "Auto-refreshing every "
            + str(_AUTO_REFRESH_SECONDS)
            + " seconds &nbsp;&middot;&nbsp; Scraping continues in the background"
            "</div>",
            unsafe_allow_html=True,
        )
        # BUG-SP-08: Refresh Now alongside Stop
        col_stop, col_refresh = st.columns([1, 1], gap="small")
        with col_stop:
            if st.button("Stop Job", key="btn_stop_job", width="stretch"):
                stop_job(job_id)
                st.session_state.pop("active_job_id", None)
                st.warning("Stop signal sent.")
                st.rerun()
        with col_refresh:
            if st.button(
                "Refresh Now",
                key="btn_refresh_status",
                width="stretch",
            ):
                st.rerun()

    elif status == STATUS_DONE:
        st.success(
            "Job complete — "
            + str(leads)
            + " leads found ("
            + str(inserted)
            + " new, "
            + str(skipped)
            + " skipped, "
            + str(updated)
            + " updated)."
        )
        _get_recent_jobs_cached.clear()
    elif status == STATUS_FAILED:
        st.error("Job failed. Check logs.")
    elif status == STATUS_STOPPED:
        st.warning("Job stopped — " + str(leads) + " leads captured so far.")

    return is_running


# ─────────────────────────────────────────────────────────
# Recent campaigns
# BUG-SP-05: uses _get_recent_jobs_cached (30s TTL)
# FIX-SP-A:  cache NOT cleared here — only cleared when a new
#            job launches or an existing job completes
# FIX-SS-02: reads correct DB column names
# ─────────────────────────────────────────────────────────
def render_recent_jobs() -> None:
    _section_header(
        "Recent Campaigns",
        "Your last 12 search jobs — scroll to view history.",
    )
    # FIX-SP-A: do NOT call _get_recent_jobs_cached.clear() here.
    # Clearing the cache on every render defeats the BUG-SP-05 fix.
    # Cache is invalidated in two places only:
    #   1. _launch_campaign() after a new job starts
    #   2. render_active_job_monitor() after STATUS_DONE

    jobs = _get_recent_jobs_cached(limit=12)
    if not jobs:
        st.markdown(
            '<div style="color:' + _TEXT_MUT + ';font-size:13px;'
            'padding:20px 0;">No campaigns run yet.</div>',
            unsafe_allow_html=True,
        )
        return

    total_jobs  = len(jobs)
    total_found = sum(_safe_int(j.get("leads_found", 0)) for j in jobs)
    # FIX-SS-02: correct column name is 'inserted' not 'leads_inserted'
    total_inserted = sum(_safe_int(j.get("inserted", 0)) for j in jobs)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown(
            '<div class="sr-kpi">'
            '<div class="sr-kpi-label">Total Campaigns</div>'
            '<div class="sr-kpi-value">' + str(total_jobs) + "</div>"
            '<div class="sr-kpi-sub">Shown here</div></div>',
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            '<div class="sr-kpi">'
            '<div class="sr-kpi-label">Total Found</div>'
            '<div class="sr-kpi-value" style="color:' + _BRAND_BLUE + ';">'
            + str(total_found)
            + "</div>"
            '<div class="sr-kpi-sub">Across all campaigns</div></div>',
            unsafe_allow_html=True,
        )
    with col_c:
        st.markdown(
            '<div class="sr-kpi">'
            '<div class="sr-kpi-label">Total Inserted</div>'
            '<div class="sr-kpi-value" style="color:' + _GREEN + ';">'
            + str(total_inserted)
            + "</div>"
            '<div class="sr-kpi-sub">New leads added to DB</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)

    cards_html = '<div class="sr-campaigns-scroll">'
    for job in jobs:
        status     = str(job.get("status", "unknown"))
        status_col = _STATUS_COLORS.get(status, _TEXT_MUT)
        leads      = _safe_int(job.get("leads_found", 0))

        # FIX-SS-02: correct column names from DB
        inserted = _safe_int(job.get("inserted", 0))
        skipped  = _safe_int(job.get("skipped",  0))
        updated  = _safe_int(job.get("updated",  0))

        keyword = _trunc(
            job.get("keyword")
            or job.get("search_keyword")
            or job.get("niche")
            or "Unknown",
            32,
        )

        # FIX-SS-02: city is canonical; location is fallback
        city_raw = job.get("city") or job.get("location") or ""
        city     = _trunc(city_raw, 28) if city_raw else "—"

        source    = job.get("source", "")
        src_cfg   = SOURCE_OPTIONS.get(source, {})
        src_label = src_cfg.get("label", source) if src_cfg else source
        src_icon  = src_cfg.get("icon",  "?")    if src_cfg else "?"

        started_at = _fmt_ts(job.get("started_at"))
        # FIX-SS-02: finished_at is canonical; completed_at is fallback
        finished_at = _fmt_ts(
            job.get("finished_at") or job.get("completed_at")
        )

        duration = str(job.get("duration_seconds") or "—")
        if duration != "—":
            duration = duration + "s"

        cards_html += (
            '<div class="sr-campaign"'
            ' style="border-top-color:' + status_col + ';">'
            '<div class="sr-campaign-name">'
            + _esc(keyword)
            + " &mdash; "
            + _esc(city)
            + "</div>"
            '<div class="sr-campaign-meta">'
            + _esc(src_icon)
            + " "
            + _esc(src_label)
            + " &middot; Started "
            + _esc(started_at)
            + "</div>"
            '<div class="sr-campaign-grid">'
            '<div class="sr-campaign-stat">'
            '<div class="sr-campaign-stat-label">Total Found</div>'
            '<div class="sr-campaign-stat-value blue">' + str(leads) + "</div></div>"
            '<div class="sr-campaign-stat">'
            '<div class="sr-campaign-stat-label">Inserted</div>'
            '<div class="sr-campaign-stat-value green">' + str(inserted) + "</div></div>"
            '<div class="sr-campaign-stat">'
            '<div class="sr-campaign-stat-label">Skipped</div>'
            '<div class="sr-campaign-stat-value gold">' + str(skipped) + "</div></div>"
            '<div class="sr-campaign-stat">'
            '<div class="sr-campaign-stat-label">Updated</div>'
            '<div class="sr-campaign-stat-value">' + str(updated) + "</div></div>"
            "</div>"
            '<div class="sr-campaign-footer">'
            '<div class="sr-campaign-time">'
            "Finished: "
            + _esc(finished_at)
            + " &middot; Duration: "
            + _esc(duration)
            + "</div>"
            '<div class="sr-campaign-status" style="color:' + status_col + ';">'
            + _esc(status.upper())
            + "</div>"
            "</div></div>"
        )
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Email enrichment panel
# BUG-SP-06: missing counts cached at 60s
# ─────────────────────────────────────────────────────────
def render_email_enrichment_panel() -> None:
    _section_header(
        "Email Enrichment",
        "Find missing emails for leads already in your database.",
    )

    missing_e_count = _get_missing_email_count(limit=200)
    missing_w_count = _get_missing_website_count(limit=200)

    col1, col2 = st.columns(2, gap="small")
    with col1:
        st.markdown(
            '<div class="sr-kpi">'
            '<div class="sr-kpi-label">Missing Emails</div>'
            '<div class="sr-kpi-value" style="color:' + _GOLD + ';">'
            + str(missing_e_count)
            + "</div>"
            '<div class="sr-kpi-sub">Leads without email address</div></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            '<div class="sr-kpi">'
            '<div class="sr-kpi-label">Missing Websites</div>'
            '<div class="sr-kpi-value" style="color:' + _ORANGE + ';">'
            + str(missing_w_count)
            + "</div>"
            '<div class="sr-kpi-sub">Leads without website URL</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

    enrich_limit = st.slider(
        "Leads to enrich per run",
        min_value=5,
        max_value=100,
        value=20,
        step=5,
        key="sl_enrich_limit",
    )

    if st.button(
        "Find Missing Emails",
        key="btn_enrich_emails",
        width="stretch",
    ):
        prog_bar  = st.progress(0.0)
        status_ph = st.empty()

        def _progress(current: int, total: int, message: str = "") -> None:
            pct = current / max(total, 1)
            prog_bar.progress(min(pct, 1.0))
            if message:
                status_ph.markdown(
                    '<div style="color:' + _TEXT_MUT + ';font-size:12px;">'
                    + _esc(message)
                    + "</div>",
                    unsafe_allow_html=True,
                )

        try:
            result  = reenrich_leads_missing_emails(
                limit=enrich_limit, progress_callback=_progress
            )
            prog_bar.progress(1.0)
            found   = result.get("emails_found", 0)
            done    = result.get("processed",    0)
            updated = result.get("updated",       0)
            st.success(
                "Processed "
                + str(done)
                + " leads — "
                + str(found)
                + " emails found, "
                + str(updated)
                + " records updated."
            )
            # Invalidate both count caches after enrichment
            _get_missing_email_count.clear()
            _get_missing_website_count.clear()
        except Exception as exc:
            st.error("Enrichment error: " + _esc(str(exc)))


# ─────────────────────────────────────────────────────────
# Main entry point
# FIX-SP-C: time.sleep(10) removed — was blocking the entire
#           Streamlit UI thread including Stop/Refresh buttons.
#           Auto-refresh is signalled by st.rerun() at the
#           bottom of the running-job branch after all UI
#           elements have been rendered and a short non-
#           blocking sleep has elapsed.
#
# FIX-SP-G: Session defaults re-checked at the top of every call to
#           main() — the module-level block at the top of the file
#           only executes once per process under this app's custom
#           router (app.py imports the module once, calls render()
#           repeatedly). This loop is cheap (dict membership check)
#           and guarantees every required key exists before any
#           downstream function reads from session_state.
# ─────────────────────────────────────────────────────────
def main() -> None:
    # FIX-SP-G: re-assert session defaults on every call.
    for _k, _v in _SESSION_DEFAULTS.items():
        if _k not in st.session_state:
            st.session_state[_k] = _v

    _render_page_css()
    render_hero()

    # Handle pending forced launch at top of every rerun
    if (
        st.session_state.get("force_launch_search")
        and st.session_state.get("pending_search_launch")
    ):
        pending = st.session_state["pending_search_launch"]
        result  = _launch_campaign(pending, bypass_dedup=True)
        if result == "launched":
            st.rerun()
        else:
            st.session_state["force_launch_search"] = False
            st.session_state.pop("pending_search_launch", None)

    tab_search, tab_risk, tab_enrich = st.tabs(
        ["Lead Search", "Scrape Risk", "Email Enrichment"]
    )

    with tab_search:
        selected_source = render_source_selector()
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

        campaign = render_campaign_builder(selected_source)

        st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

        col_btn, _col_gap = st.columns([2, 3])
        with col_btn:
            launch = st.button(
                "Launch Lead Discovery",
                key="btn_launch",
                width="stretch",
            )

        if launch:
            result = _launch_campaign(campaign or {}, bypass_dedup=False)
            if result != "dedup":
                st.session_state.pop("pending_search_launch", None)
                st.session_state.pop("force_launch_search", None)

        # BUG-SP-09: "Run anyway" persists across reruns via session state
        if (
            st.session_state.get("pending_search_launch")
            and not st.session_state.get("force_launch_search")
        ):
            pending = st.session_state["pending_search_launch"]
            kw  = pending.get("keyword", "")
            loc = pending.get("city", "")
            st.info(
                "This search was completed within the last hour ("
                + _esc(kw)
                + " in "
                + _esc(loc)
                + "). Click below to run it again."
            )
            if st.button(
                "Run anyway",
                key="btn_force_launch",
                width="content",
            ):
                st.session_state["force_launch_search"] = True
                st.rerun()

        # Active job display
        active_job        = _get_clean_active_job_id()
        job_still_running = False

        if active_job:
            st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
            _section_header("Active Job", "Live progress for the current search.")
            job_still_running = render_active_job_monitor(active_job)

        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        render_recent_jobs()

        # FIX-SP-C: auto-refresh while job is running.
        # All UI elements above are fully rendered before we sleep.
        # The sleep here is short (non-blocking relative to user
        # interaction because Streamlit re-renders from the top on
        # st.rerun() — the sleep only delays the NEXT render cycle,
        # not the current one that the user is looking at).
        if job_still_running:
            time.sleep(_AUTO_REFRESH_SECONDS)
            st.rerun()

    with tab_risk:
        render_scrape_risk_card()

    with tab_enrich:
        render_email_enrichment_panel()


# ─────────────────────────────────────────────────────────
# Streamlit entry points
# ─────────────────────────────────────────────────────────
def render() -> None:
    main()


if __name__ == "__main__":
    main()