# pages/leads.py
# KNCS LeadGen Pro — v10.5
#
# Fixes applied in v10.5:
#   FIX-NG-SAVE-01 (CRITICAL) — Nigeria lead search + enrichment
#               previously had NO database write path anywhere in
#               this file. Results only ever lived in
#               st.session_state and CSV downloads via
#               st.download_button. This meant every Nigeria batch
#               run through this page was invisible to Call Center,
#               Dashboard, and Reports — regardless of any routing
#               fix — because the leads never reached the leads
#               table at all.
#
#               Added:
#                 - insert_lead import (from database.py)
#                 - engine.router import (assign_channel,
#                   apply_routing_updates)
#                 - _map_ng_lead_for_db(): translates this page's
#                   enrichment field names (niche, dm_name, dm_title,
#                   whatsapp_number) to the exact column names
#                   insert_lead() expects (industry,
#                   decision_maker_name, decision_maker_title,
#                   phone). Without this mapping, insert_lead()
#                   silently drops these fields since it only copies
#                   keys that exactly match its internal allow-list.
#                 - _save_ng_leads_to_db(): persists a list of
#                   enriched leads via insert_lead(), then applies
#                   channel routing immediately after each insert so
#                   best_channel/wa_status/whatsapp_ready are set on
#                   the same row that gets written.
#                 - "Save to Database" button in the Nigeria search
#                   UI, enabled once leads have been enriched.
#
# Fixes applied in v10.4:
#   US-MARKET-01 — Added "US Businesses" as a third market option
#               alongside UK Businesses and Nigeria B2B. Previously
#               the market radio was hardcoded to two options, so
#               US leads (sourced via YellowPages/Yelp/Google Maps)
#               had no dedicated view and were being swept into the
#               UK tab's catch-all "not Nigeria" filter.
#               New _US_COUNTRY_VALUES set added. UK tab's catch-all
#               logic updated to also exclude US country values.
#               Shared rendering logic (KPIs, filters, segment/table/
#               card tabs) extracted into _render_market_leads_section()
#               so UK and US tabs reuse identical code — zero duplication.
#
# Fixes applied in v10.3:
#   NG-FILTER — Nigeria industry filter added to _render_ng_db_leads_section()
#               Selectbox built from live db_ng_leads data (no extra DB query)
#               Session state preserves filter on tab switch
#               Summary banner stays on unfiltered db_ng_leads totals
#               All tabs use filtered_ng_leads
#               Export filename includes industry slug
#               Zero results guard added before tabs
#
# Fixes applied in v10.2:
#   BUG-NG-01 — Nigerian leads in DB now visible in Nigeria B2B tab
#               render_nigeria_section() now loads from database first
#               get_all_leads() filtered by country = 'Nigeria'
#               DB leads shown above the search form as "Saved Nigerian Leads"
#               Session state results (new searches) shown below
#
# Fixes carried from v10.1:
#   FIX-L-01 — opportunity_score replaced with fit_score throughout
#   FIX-L-02 — Market filter — UK shows only UK leads
#   FIX-L-03 — Postcode area grouping for Companies House leads
#
# Fixes carried from v10.0:
#   1. All f-string HTML converted to string concatenation
#   2. CSS block converted to plain string
#   3. Emoji replaced with HTML entity codes in all HTML blocks
#   4. Source icons converted from emoji to safe text labels
#   5. All render functions converted — zero f-string HTML

from datetime import datetime
import html
import re
import time
from io import StringIO
from typing import Optional, Any

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────
# Safe DB imports
# FIX-NG-SAVE-01: insert_lead added to imports/fallback
# ─────────────────────────────────────────────────────────
try:
    from database import get_all_leads, get_connection, insert_lead
    from database_patch import (
        update_lead_status,
        update_lead_email,
        update_lead_enrichment,
    )
    _DB_AVAILABLE = True
except Exception as _db_err:
    import logging as _logging
    _logging.getLogger(__name__).error(
        "DB import failed: %s", _db_err
    )
    _DB_AVAILABLE = False
    def get_all_leads(limit=5000):       return []
    def update_lead_status(*a, **k):     return False
    def update_lead_email(*a, **k):      return False
    def update_lead_enrichment(*a, **k): return False
    def insert_lead(*a, **k):            return (False, "error:db_unavailable", None)
    get_connection = None

# ─────────────────────────────────────────────────────────
# Safe scorer imports
# ─────────────────────────────────────────────────────────
try:
    from engine.scorer import get_contact_completeness
except Exception:
    def get_contact_completeness(lead) -> dict:
        fields = {
            "Phone":   bool(lead.get("phone")),
            "Email":   bool(lead.get("email")),
            "Website": bool(lead.get("website")),
            "Address": bool(lead.get("address")),
        }
        filled = sum(1 for v in fields.values() if v)
        pct    = filled / len(fields) * 100
        return {
            "percentage": pct, "score": pct,
            "fields": fields, "filled": filled,
            "total": len(fields),
        }

# ─────────────────────────────────────────────────────────
# Safe enricher imports
# ─────────────────────────────────────────────────────────
try:
    from engine.enricher import find_email_multi_source, enrich_lead
    _ENRICHER_AVAILABLE = True
except ImportError:
    _ENRICHER_AVAILABLE = False
    def find_email_multi_source(*args, **kwargs): return None
    def enrich_lead(*args, **kwargs):             pass

# ─────────────────────────────────────────────────────────
# Safe job manager imports
# ─────────────────────────────────────────────────────────
try:
    from engine.job_manager import any_job_running
    _JOB_MANAGER_AVAILABLE = True
except ImportError:
    _JOB_MANAGER_AVAILABLE = False
    def any_job_running(): return False

# ─────────────────────────────────────────────────────────
# Safe Nigeria imports
# ─────────────────────────────────────────────────────────
try:
    from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient
    _NG_CLIENT_OK = True
except Exception:
    _NG_CLIENT_OK = False

try:
    from engine.nigeria.nigeria_enricher import NigeriaEnricher
    _NG_ENRICHER_OK = True
except Exception:
    _NG_ENRICHER_OK = False

try:
    from engine.nigeria.decision_maker_finder import DecisionMakerFinder
    _NG_DM_OK = True
except Exception:
    _NG_DM_OK = False

# ─────────────────────────────────────────────────────────
# FIX-NG-SAVE-01: Router import — needed so Nigeria leads get
# best_channel/wa_status routing applied at save time, instead of
# depending on a separate backfill script that has to be re-run
# after every future batch.
# ─────────────────────────────────────────────────────────
try:
    from engine.router import assign_channel, apply_routing_updates
    _ROUTER_AVAILABLE = True
except Exception:
    _ROUTER_AVAILABLE = False
    def assign_channel(lead: dict) -> dict: return {}
    def apply_routing_updates(lead_id, updates) -> bool: return False

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
_TEXT_PRI   = "#f1f5f9"
_TEXT_SEC   = "#94a3b8"
_TEXT_MUT   = "#475569"
_BORDER     = "rgba(255,255,255,0.05)"
_BORDER_MID = "rgba(255,255,255,0.08)"
_BORDER_BRD = "rgba(91,140,255,0.28)"
_GLOW_BLUE  = "rgba(91,140,255,0.18)"
_NG_GREEN   = "#25D366"

# ─────────────────────────────────────────────────────────
# Nigeria constants
# ─────────────────────────────────────────────────────────
_NG_NICHES = {
    "Private Schools":             "private_school",
    "Private Hospitals & Clinics": "private_hospital",
    "Real Estate Companies":       "real_estate",
    "Hotels & Guest Houses":       "hotel",
    "Manufacturing SMEs":          "manufacturing",
    "Construction Companies":      "construction",
    "Law Firms":                   "law_firm",
}

_NG_CITIES = {
    "Lagos — Victoria Island": "lagos_vi",
    "Lagos — Lekki":           "lagos_lekki",
    "Lagos — Ikeja":           "lagos_ikeja",
    "Lagos — Surulere":        "lagos_surulere",
    "Lagos — Yaba":            "lagos_yaba",
    "Abuja — Maitama":         "abuja_maitama",
    "Abuja — Wuse 2":          "abuja_wuse",
    "Abuja — Garki":           "abuja_garki",
    "Port Harcourt — GRA":     "ph_gra",
    "Benin City — GRA":        "benin_gra",
    "Warri — GRA":             "warri_gra",
    "Enugu — GRA":             "enugu_gra",
    "Owerri":                  "owerri_central",
    "Kano":                    "kano_central",
    "Ibadan":                  "ibadan_ring",
    "Calabar":                 "calabar_gra",
    "Abeokuta":                "abeokuta_central",
    "Akure":                   "akure_central",
    "Ilorin":                  "ilorin_central",
    "Kaduna":                  "kaduna_gra",
    "Jos":                     "jos_central",
}

_NG_CITY_DISPLAY = {
    "lagos_vi":          "Victoria Island Lagos",
    "lagos_lekki":       "Lekki Lagos",
    "lagos_ikeja":       "Ikeja Lagos",
    "lagos_surulere":    "Surulere Lagos",
    "lagos_yaba":        "Yaba Lagos",
    "abuja_maitama":     "Maitama Abuja",
    "abuja_wuse":        "Wuse 2 Abuja",
    "abuja_garki":       "Garki Abuja",
    "ph_gra":            "GRA Port Harcourt",
    "benin_gra":         "GRA Benin City",
    "warri_gra":         "GRA Warri",
    "enugu_gra":         "GRA Enugu",
    "owerri_central":    "Owerri",
    "kano_central":      "Kano",
    "ibadan_ring":       "Ibadan",
    "calabar_gra":       "Calabar",
    "abeokuta_central":  "Abeokuta",
    "akure_central":     "Akure",
    "ilorin_central":    "Ilorin",
    "kaduna_gra":        "Kaduna",
    "jos_central":       "Jos",
}

_NG_NICHE_DM_TITLES = {
    "private_school":   "School Proprietor/Principal",
    "private_hospital": "Medical Director",
    "real_estate":      "Managing Director",
    "hotel":            "General Manager",
    "manufacturing":    "Factory Director",
    "construction":     "Managing Director",
    "law_firm":         "Senior Partner",
}

# ─────────────────────────────────────────────────────────
# DM source labels
# ─────────────────────────────────────────────────────────
_DM_SOURCE_LABELS = {
    "cqc":             "CQC Register",
    "companies_house": "Companies House",
    "website":         "Website",
    "google_maps":     "Google Maps",
    "not_found":       "",
}

# ─────────────────────────────────────────────────────────
# UK country values — leads matching these are shown
# under UK Businesses. Nigeria and US leads are excluded.
# ─────────────────────────────────────────────────────────
_UK_COUNTRY_VALUES = {
    "united kingdom",
    "uk",
    "england",
    "wales",
    "scotland",
    "northern ireland",
    "great britain",
    "gb",
    "",        # leads with no country set default to UK
}

_NIGERIA_COUNTRY_VALUES = {
    "nigeria",
    "ng",
}

# ─────────────────────────────────────────────────────────
# US-MARKET-01: US country values — leads matching these are
# shown under US Businesses. Explicit match only (no catch-all,
# unlike UK which defaults blank/unknown country to itself).
# ─────────────────────────────────────────────────────────
_US_COUNTRY_VALUES = {
    "united states",
    "united states of america",
    "us",
    "usa",
    "america",
}

# ─────────────────────────────────────────────────────────
# Postcode pattern — matches UK postcodes stored in city field
# by Companies House scraper e.g. B27 7TL, SW1A 1AA, BA1 3PR
# ─────────────────────────────────────────────────────────
_UK_POSTCODE_RE = re.compile(
    r'^[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}$',
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────
# Page CSS — plain string, zero f-string
# ─────────────────────────────────────────────────────────
def _render_page_css() -> None:
    css = (
        "<style>"

        ".block-container {"
        "    padding-top: 1.4rem !important;"
        "    padding-bottom: 3rem !important;"
        "    max-width: 1400px !important;"
        "}"

        ".lp-hero {"
        "    background: linear-gradient(145deg,"
        "        #0b1628 0%, #101f38 50%, #0b1628 100%);"
        "    border: 1px solid rgba(255,255,255,0.05);"
        "    border-left: 4px solid #5b8cff;"
        "    border-radius: 20px; padding: 24px 28px;"
        "    margin-bottom: 24px; position: relative; overflow: hidden;"
        "}"
        ".lp-hero-orb {"
        "    position: absolute; top: -80px; right: -80px;"
        "    width: 280px; height: 280px; border-radius: 50%;"
        "    background: radial-gradient(circle,"
        "        rgba(91,140,255,0.12) 0%, transparent 70%);"
        "    pointer-events: none;"
        "}"
        ".lp-hero-eyebrow {"
        "    font-size: 10px; font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 2px; color: #5b8cff; margin-bottom: 8px;"
        "}"
        ".lp-hero-title {"
        "    font-size: 30px; font-weight: 900; color: #f1f5f9;"
        "    letter-spacing: -1px; line-height: 1.15; margin: 0 0 8px 0;"
        "}"
        ".lp-hero-sub {"
        "    font-size: 13px; color: #475569; font-weight: 500;"
        "    line-height: 1.8; max-width: 680px;"
        "}"

        ".lp-kpi {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05); border-radius: 18px;"
        "    padding: 18px 18px 14px 18px; position: relative; overflow: hidden;"
        "    transition: transform 0.22s ease, border-color 0.25s ease;"
        "}"
        ".lp-kpi:hover {"
        "    transform: translateY(-3px);"
        "    border-color: rgba(91,140,255,0.28);"
        "    box-shadow: 0 14px 40px rgba(91,140,255,0.14);"
        "}"
        ".lp-kpi-accent {"
        "    position: absolute; top: 0; left: 0; right: 0;"
        "    height: 3px; border-radius: 18px 18px 0 0;"
        "}"
        ".lp-kpi-icon {"
        "    font-size: 11px; font-weight: 900; margin-bottom: 10px;"
        "    width: 36px; height: 36px; border-radius: 10px;"
        "    display: flex; align-items: center; justify-content: center;"
        "    background: rgba(255,255,255,0.05);"
        "    border: 1px solid rgba(255,255,255,0.08);"
        "    letter-spacing: 0.5px;"
        "}"
        ".lp-kpi-label {"
        "    color: #475569; font-size: 9.5px; font-weight: 700;"
        "    text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 6px;"
        "}"
        ".lp-kpi-value {"
        "    font-size: 26px; font-weight: 900; color: #f1f5f9;"
        "    letter-spacing: -1px; line-height: 1; margin-bottom: 6px;"
        "}"
        ".lp-kpi-sub { font-size: 11px; font-weight: 600; }"

        ".lp-filters {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(91,140,255,0.14);"
        "    border-radius: 16px; padding: 14px 22px 6px 22px;"
        "    margin-bottom: 20px;"
        "}"
        ".lp-filters-label {"
        "    font-size: 10px; font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 1.5px; color: #5b8cff; margin-bottom: 12px;"
        "}"

        ".lp-sec-head  { margin: 0 0 16px 0; }"
        ".lp-sec-title {"
        "    font-size: 20px; font-weight: 800; color: #f1f5f9;"
        "    letter-spacing: -0.5px; line-height: 1.2; margin: 0 0 4px 0;"
        "}"
        ".lp-sec-sub {"
        "    font-size: 12px; color: #475569; font-weight: 500; margin: 0;"
        "}"

        ".lp-seg-banner {"
        "    background: linear-gradient(135deg,"
        "        rgba(91,140,255,0.08), rgba(124,92,255,0.04));"
        "    border: 1px solid rgba(91,140,255,0.18); border-radius: 14px;"
        "    padding: 14px 20px; margin-bottom: 16px;"
        "    display: flex; align-items: center; gap: 20px; flex-wrap: wrap;"
        "}"
        ".lp-seg-stat       { display: flex; align-items: baseline; gap: 6px; }"
        ".lp-seg-stat-value { font-size: 22px; font-weight: 900; color: #f1f5f9; }"
        ".lp-seg-stat-label { font-size: 12px; color: #475569; font-weight: 500; }"
        ".lp-seg-divider    { width: 1px; height: 24px; background: rgba(255,255,255,0.05); }"

        ".lp-card {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(255,255,255,0.05); border-radius: 20px;"
        "    padding: 22px; margin-bottom: 16px; position: relative; overflow: hidden;"
        "    transition: transform 0.22s ease, border-color 0.25s ease, box-shadow 0.25s ease;"
        "}"
        ".lp-card:hover {"
        "    transform: translateY(-2px);"
        "    border-color: rgba(91,140,255,0.22);"
        "    box-shadow: 0 12px 36px rgba(91,140,255,0.10);"
        "}"
        ".lp-card-stripe {"
        "    position: absolute; top: 0; left: 0;"
        "    width: 4px; height: 100%; border-radius: 20px 0 0 20px;"
        "}"
        ".lp-card-header {"
        "    display: flex; justify-content: space-between;"
        "    align-items: flex-start; margin-bottom: 16px; padding-left: 10px;"
        "}"
        ".lp-card-name {"
        "    font-size: 17px; font-weight: 800; color: #f1f5f9;"
        "    letter-spacing: -0.3px; margin-bottom: 8px;"
        "}"
        ".lp-card-badges { display: flex; gap: 6px; flex-wrap: wrap; }"
        ".lp-badge {"
        "    display: inline-flex; align-items: center; gap: 4px;"
        "    padding: 3px 9px; border-radius: 999px; font-size: 10px;"
        "    font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 0.5px; white-space: nowrap;"
        "}"
        ".lp-score-block { text-align: right; flex-shrink: 0; }"
        ".lp-score-num {"
        "    font-size: 38px; font-weight: 900; line-height: 1; letter-spacing: -2px;"
        "}"
        ".lp-score-label {"
        "    font-size: 9.5px; font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 1.5px; color: #475569; margin-top: 2px;"
        "}"
        ".lp-contact-grid {"
        "    display: grid; grid-template-columns: 1fr 1fr;"
        "    gap: 10px; margin-bottom: 14px; padding-left: 10px;"
        "}"
        ".lp-contact-cell {"
        "    background: rgba(255,255,255,0.025);"
        "    border: 1px solid rgba(255,255,255,0.05);"
        "    border-radius: 12px; padding: 11px 13px;"
        "}"
        ".lp-contact-cell-label {"
        "    color: #475569; font-size: 9.5px; font-weight: 700;"
        "    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px;"
        "}"
        ".lp-contact-cell-value {"
        "    color: #f1f5f9; font-size: 13px; font-weight: 600; word-break: break-all;"
        "}"
        ".lp-social-row {"
        "    display: flex; gap: 8px; flex-wrap: wrap;"
        "    padding-left: 10px; margin-bottom: 14px;"
        "}"
        ".lp-social-btn {"
        "    display: inline-flex; align-items: center; gap: 6px;"
        "    padding: 6px 14px; border-radius: 10px; font-size: 12px;"
        "    font-weight: 700; text-decoration: none;"
        "}"
        ".lp-progress-wrap { padding-left: 10px; }"
        ".lp-progress-row {"
        "    display: flex; justify-content: space-between;"
        "    align-items: center; margin-bottom: 6px;"
        "}"
        ".lp-progress-label { font-size: 11px; font-weight: 600; color: #94a3b8; }"
        ".lp-progress-pct   { font-size: 12px; font-weight: 800; color: #f1f5f9; }"
        ".lp-progress-track {"
        "    height: 6px; background: rgba(255,255,255,0.05);"
        "    border-radius: 999px; overflow: hidden;"
        "}"
        ".lp-progress-fill {"
        "    height: 100%;"
        "    background: linear-gradient(90deg, #5b8cff, #7c5cff);"
        "    border-radius: 999px;"
        "}"

        ".lp-page-info   { font-size: 13px; color: #475569; margin-bottom: 18px; }"
        ".lp-page-info b { color: #f1f5f9; }"

        ".lp-empty {"
        "    text-align: center; padding: 48px 24px;"
        "    background: rgba(255,255,255,0.02);"
        "    border: 1px dashed rgba(255,255,255,0.08);"
        "    border-radius: 18px; margin: 20px 0;"
        "}"
        ".lp-empty-icon  { font-size: 36px; margin-bottom: 14px; }"
        ".lp-empty-title {"
        "    font-size: 16px; font-weight: 700; color: #94a3b8; margin-bottom: 6px;"
        "}"
        ".lp-empty-sub   { font-size: 13px; color: #475569; }"

        ".ng-lead-card {"
        "    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%);"
        "    border: 1px solid rgba(37,211,102,0.15);"
        "    border-radius: 16px; padding: 18px 20px; margin-bottom: 12px;"
        "    transition: transform 0.2s ease, border-color 0.25s ease;"
        "}"
        ".ng-lead-card:hover {"
        "    transform: translateY(-2px);"
        "    border-color: rgba(37,211,102,0.30);"
        "}"
        ".ng-lead-name {"
        "    font-size: 15px; font-weight: 800; color: #f1f5f9; margin-bottom: 6px;"
        "}"
        ".ng-lead-dm {"
        "    font-size: 12px; color: #25D366; font-weight: 700; margin-bottom: 10px;"
        "}"
        ".ng-lead-grid {"
        "    display: grid; grid-template-columns: 1fr 1fr;"
        "    gap: 8px; margin-bottom: 10px;"
        "}"
        ".ng-lead-field-label {"
        "    font-size: 9px; font-weight: 700; text-transform: uppercase;"
        "    letter-spacing: 1px; color: #475569; margin-bottom: 3px;"
        "}"
        ".ng-lead-field-value {"
        "    font-size: 12px; color: #f1f5f9; font-weight: 600; word-break: break-all;"
        "}"

        # BUG-NG-01: new CSS class for saved Nigeria DB lead cards
        # Distinguishes DB leads (green border) from session search results
        ".ng-db-lead-card {"
        "    background: linear-gradient(145deg, #0b1628 0%, #0d1f10 100%);"
        "    border: 1px solid rgba(37,211,102,0.20);"
        "    border-left: 4px solid #25D366;"
        "    border-radius: 14px; padding: 16px 18px; margin-bottom: 10px;"
        "    transition: transform 0.2s ease, border-color 0.25s ease;"
        "}"
        ".ng-db-lead-card:hover {"
        "    transform: translateY(-2px);"
        "    border-color: rgba(37,211,102,0.40);"
        "    box-shadow: 0 8px 28px rgba(37,211,102,0.10);"
        "}"

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
def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def safe_key(lead_id: Optional[int], suffix: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]", "_", suffix)[:20]
    return "lead_" + str(lead_id or 0) + "_" + clean


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _is_scraping_active() -> bool:
    if not _JOB_MANAGER_AVAILABLE:
        return False
    try:
        return bool(any_job_running())
    except Exception:
        return False


def _score_color(score: int) -> str:
    if score >= 70: return _RED
    if score >= 50: return _GOLD
    return _BRAND_BLUE


def _source_color(source: str) -> str:
    return {
        "google_maps":     _BRAND_BLUE,
        "b2bhint":         _BRAND_PUR,
        "yelp":            _RED,
        "yellowpages":     _GREEN,
        "companies_house": "#3b82f6",
    }.get(source, _TEXT_MUT)


def _source_label(source: str) -> str:
    return {
        "google_maps":     "Maps",
        "b2bhint":         "B2B",
        "yelp":            "Yelp",
        "yellowpages":     "YP",
        "companies_house": "GB",
    }.get(source, "?")


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        '<div class="lp-sec-sub">' + _esc(subtitle) + '</div>'
        if subtitle else ""
    )
    st.markdown(
        '<div class="lp-sec-head">'
        '<div class="lp-sec-title">' + _esc(title) + '</div>'
        + sub_html +
        '</div>',
        unsafe_allow_html=True,
    )


def _is_uk_postcode(value: str) -> bool:
    return bool(_UK_POSTCODE_RE.match(value.strip()))


def _postcode_to_area_label(postcode: str) -> str:
    area = postcode.strip().split()[0].upper() if postcode.strip() else ""
    return area + " Area" if area else "United Kingdom"


# ─────────────────────────────────────────────────────────
# Hero
# ─────────────────────────────────────────────────────────
def render_hero() -> None:
    now       = datetime.now()
    timestamp = _esc(now.strftime("%A, %d %B %Y, %H:%M"))

    st.markdown(
        '<div class="lp-hero">'
        '<div class="lp-hero-orb"></div>'
        '<div class="lp-hero-eyebrow">Lead Intelligence Workspace</div>'
        '<div class="lp-hero-title">Your Business Lead Database</div>'
        '<div class="lp-hero-sub">'
        'UK, US and Nigeria B2B leads &mdash; managed separately'
        ' in one place. Select your market below.'
        '<br>'
        '<span style="color:#94a3b8;">' + timestamp + '</span>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# UK/US KPIs — reads fit_score (FIX-L-01)
# ─────────────────────────────────────────────────────────
def render_kpis(leads: list) -> None:
    total    = len(leads)
    hot      = sum(1 for l in leads if (l.get("fit_score") or 0) >= 70)
    emails   = sum(1 for l in leads if l.get("email"))
    phones   = sum(1 for l in leads if l.get("phone"))
    enriched = sum(
        1 for l in leads
        if str(l.get("enrichment_status", "")).lower()
        in ("done", "complete", "completed", "enriched")
    )
    with_dm = sum(1 for l in leads if l.get("decision_maker_name"))

    cards = [
        ("TL",  "Total Leads", str(f"{total:,}"),    "All leads",             _BRAND_BLUE),
        ("HOT", "Hot Leads",   str(f"{hot:,}"),      "High-intent",           _RED),
        ("EM",  "Emails",      str(f"{emails:,}"),   "Reachable contacts",    _GREEN),
        ("PH",  "Phones",      str(f"{phones:,}"),   "Direct outreach",       "#38bdf8"),
        ("DM",  "With DM",     str(f"{with_dm:,}"),  "Named decision makers", _BRAND_PUR),
        ("OK",  "Enriched",    str(f"{enriched:,}"), "Processed leads",       _GOLD),
    ]

    cols = st.columns(6)
    for col, (icon, label, value, sub, accent) in zip(cols, cards):
        with col:
            st.markdown(
                '<div class="lp-kpi">'
                '<div class="lp-kpi-accent" style="background:' + accent + ';"></div>'
                '<div class="lp-kpi-icon" style="color:' + accent + ';">'
                + icon +
                '</div>'
                '<div class="lp-kpi-label">' + _esc(label) + '</div>'
                '<div class="lp-kpi-value">' + value + '</div>'
                '<div class="lp-kpi-sub" style="color:' + accent + ';">'
                + _esc(sub) +
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────
# UK/US Filters — reads fit_score (FIX-L-01)
# ─────────────────────────────────────────────────────────
def render_filters(all_leads: list) -> list:
    st.markdown(
        '<div class="lp-filters">'
        '<div class="lp-filters-label">Lead Filters</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        search = st.text_input(
            "Search Business",
            placeholder="Type name...",
            key="leads_search",
        )
    with c2:
        min_score = st.slider(
            "Min Score", 0, 100, 0, 5,
            key="leads_min_score",
        )
    with c3:
        source = st.selectbox(
            "Source",
            ["All", "companies_house", "google_maps",
             "b2bhint", "yelp", "yellowpages"],
            key="leads_source",
        )
    with c4:
        status = st.selectbox(
            "Status",
            ["All", "new", "called", "interested",
             "callback", "deal_closed"],
            key="leads_status",
        )
    with c5:
        email_f = st.selectbox(
            "Email",
            ["All", "Has Email", "Missing Email"],
            key="leads_email_filter",
        )

    leads = all_leads
    if search:
        sl    = search.lower()
        leads = [l for l in leads if sl in (l.get("business_name", "")).lower()]
    if min_score > 0:
        leads = [l for l in leads if (l.get("fit_score", 0) or 0) >= min_score]
    if source != "All":
        leads = [l for l in leads if l.get("source") == source]
    if status != "All":
        leads = [l for l in leads if l.get("contact_status") == status]
    if email_f == "Has Email":
        leads = [l for l in leads if l.get("email")]
    elif email_f == "Missing Email":
        leads = [l for l in leads if not l.get("email")]

    return leads


# ─────────────────────────────────────────────────────────
# Segment builder — FIX-L-03 postcode detection
# ─────────────────────────────────────────────────────────
def build_segments(leads: list) -> dict:
    segments: dict = {}
    for lead in leads:
        industry = (lead.get("industry") or "Unknown").strip()
        if industry.lower() in ("", "unknown", "none"):
            industry = "Unknown Niche"

        raw_city = (
            lead.get("city") or
            lead.get("location") or
            ""
        ).strip()

        if raw_city and _is_uk_postcode(raw_city):
            city = _postcode_to_area_label(raw_city)
        elif not raw_city:
            city = "United Kingdom"
        else:
            city = raw_city
            if "," in city:
                city = city.split(",")[0].strip()

        key = industry + "||" + city
        if key not in segments:
            segments[key] = {"industry": industry, "city": city, "leads": []}
        segments[key]["leads"].append(lead)

    return dict(
        sorted(segments.items(), key=lambda x: len(x[1]["leads"]), reverse=True)
    )


# ─────────────────────────────────────────────────────────
# Lead card (UK/US)
# ─────────────────────────────────────────────────────────
def render_lead_card(lead: dict) -> None:
    lead_id = lead.get("id", 0)
    score   = _safe_int(lead.get("fit_score") or lead.get("opportunity_score") or 0)
    sc_col  = _score_color(score)
    src     = lead.get("source") or "unknown"
    s_col   = _source_color(src)
    s_lbl   = _source_label(src)
    name    = lead.get("business_name", "Unknown")

    website_discovered = lead.get("website_discovered", 0)
    website_disc_src   = lead.get("website_discovery_source", "")
    fb_url  = lead.get("social_facebook_url",  "")
    ig_url  = lead.get("social_instagram_url", "")
    li_url  = lead.get("social_linkedin_url",  "")
    dm      = lead.get("decision_maker_name",  "")
    dm_source    = lead.get("dm_source", "")
    dm_src_label = _DM_SOURCE_LABELS.get(dm_source, "")
    wa_stat      = lead.get("wa_status", "")
    cqc_rating   = lead.get("cqc_rating", "")
    cqc_url      = lead.get("cqc_url", "")

    completeness = get_contact_completeness(lead)
    if isinstance(completeness, dict):
        pct = completeness.get("percentage", completeness.get("score", 0))
    else:
        pct = completeness
    pct = max(0.0, min(100.0, _safe_float(pct, 0.0)))

    phone   = _esc(lead.get("phone")   or "Not available")
    email   = _esc(lead.get("email")   or "Not available")
    website = _esc((lead.get("website") or "Not available")[:42])

    raw_city = (lead.get("city") or "").strip()
    if raw_city and _is_uk_postcode(raw_city):
        display_city = _postcode_to_area_label(raw_city)
    else:
        display_city = raw_city

    city    = _esc(display_city)
    country = _esc(lead.get("country", ""))
    ind     = _esc(lead.get("industry", "Unknown"))

    def _badge(bg: str, border: str, text: str, content: str) -> str:
        return (
            '<span class="lp-badge" style="'
            'background:' + bg + '1a;'
            'border:1px solid ' + border + '33;'
            'color:' + text + ';">'
            + content +
            '</span>'
        )

    src_name = src.replace("_", " ").title()
    badges = _badge(s_col, s_col, s_col, s_lbl + " " + src_name)
    badges += _badge(_TEXT_MUT, _TEXT_MUT, _TEXT_SEC, ind)

    if website_discovered:
        badges += _badge(
            _GREEN, _GREEN, _GREEN,
            "WEB via " + _esc(website_disc_src),
        )
    if dm:
        badges += _badge(_BRAND_PUR, _BRAND_PUR, _BRAND_PUR, "DM: " + _esc(dm))
    if cqc_rating:
        badges += _badge(_GOLD, _GOLD, _GOLD, "CQC: " + _esc(cqc_rating))
    if wa_stat in ("verified", "opted_in", "business_confirmed"):
        badges += _badge(
            _GREEN, _GREEN, _GREEN,
            "WA " + _esc(wa_stat.replace("_", " ").title()),
        )

    dm_source_html = ""
    if dm and dm_src_label:
        dm_source_html = (
            '<div style="font-size:10px;color:' + _TEXT_MUT + ';'
            'padding-left:2px;margin-top:4px;font-weight:600;">'
            'DM Source: ' + _esc(dm_src_label) +
            '</div>'
        )

    cqc_link_html = ""
    if cqc_url:
        cqc_link_html = (
            '<div style="font-size:10px;padding-left:2px;margin-top:4px;">'
            '<a href="' + _esc(cqc_url) + '" target="_blank" '
            'style="color:' + _GOLD + ';font-weight:600;text-decoration:none;">'
            'View CQC Profile &rarr;</a>'
            '</div>'
        )

    social_html = ""
    if fb_url or ig_url or li_url:
        social_html = '<div class="lp-social-row">'
        if fb_url:
            social_html += (
                '<a href="' + _esc(fb_url) + '" target="_blank"'
                ' class="lp-social-btn"'
                ' style="background:#3b82f61a;'
                'border:1px solid #3b82f633;color:#3b82f6;">'
                'Facebook</a>'
            )
        if ig_url:
            social_html += (
                '<a href="' + _esc(ig_url) + '" target="_blank"'
                ' class="lp-social-btn"'
                ' style="background:#ec48991a;'
                'border:1px solid #ec489933;color:#ec4899;">'
                'Instagram</a>'
            )
        if li_url:
            social_html += (
                '<a href="' + _esc(li_url) + '" target="_blank"'
                ' class="lp-social-btn"'
                ' style="background:#0ea5e91a;'
                'border:1px solid #0ea5e933;color:#0ea5e9;">'
                'LinkedIn</a>'
            )
        social_html += '</div>'

    pct_str = str(int(pct))

    st.markdown(
        '<div class="lp-card">'
        '<div class="lp-card-stripe" style="background:' + sc_col + ';"></div>'
        '<div class="lp-card-header">'
        '<div>'
        '<div class="lp-card-name">' + _esc(name) + '</div>'
        '<div class="lp-card-badges">' + badges + '</div>'
        + dm_source_html
        + cqc_link_html +
        '</div>'
        '<div class="lp-score-block">'
        '<div class="lp-score-num" style="color:' + sc_col + ';">' + str(score) + '</div>'
        '<div class="lp-score-label">Score</div>'
        '</div>'
        '</div>'
        '<div class="lp-contact-grid">'
        '<div class="lp-contact-cell">'
        '<div class="lp-contact-cell-label">Phone</div>'
        '<div class="lp-contact-cell-value">' + phone + '</div>'
        '</div>'
        '<div class="lp-contact-cell">'
        '<div class="lp-contact-cell-label">Email</div>'
        '<div class="lp-contact-cell-value">' + email + '</div>'
        '</div>'
        '<div class="lp-contact-cell">'
        '<div class="lp-contact-cell-label">Website</div>'
        '<div class="lp-contact-cell-value">' + website + '</div>'
        '</div>'
        '<div class="lp-contact-cell">'
        '<div class="lp-contact-cell-label">Location</div>'
        '<div class="lp-contact-cell-value">' + city + ' ' + country + '</div>'
        '</div>'
        '</div>'
        + social_html +
        '<div class="lp-progress-wrap">'
        '<div class="lp-progress-row">'
        '<span class="lp-progress-label">Contact Completeness</span>'
        '<span class="lp-progress-pct">' + pct_str + '%</span>'
        '</div>'
        '<div class="lp-progress-track">'
        '<div class="lp-progress-fill" style="width:' + pct_str + '%;"></div>'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)

    with c1:
        if not lead.get("email"):
            if st.button("Find Email", key=safe_key(lead_id, "find_email")):
                if not _ENRICHER_AVAILABLE:
                    st.error("Enricher not available.")
                else:
                    with st.spinner("Searching..."):
                        try:
                            found = find_email_multi_source(
                                business_name=name,
                                website=lead.get("website"),
                                location=lead.get("city", ""),
                            )
                            if found:
                                update_lead_email(lead_id, found)
                                st.success("Found: " + str(found))
                                st.rerun()
                            else:
                                st.warning("No email found.")
                        except Exception as e:
                            st.error("Failed: " + str(e))

    with c2:
        if st.button("Re-Enrich", key=safe_key(lead_id, "re_enrich")):
            if not _ENRICHER_AVAILABLE:
                st.error("Enricher not available.")
            else:
                with st.spinner("Re-enriching..."):
                    try:
                        enriched = enrich_lead(
                            lead, find_email=True, find_phone=True,
                        )
                        if _DB_AVAILABLE:
                            saved = update_lead_enrichment(lead_id, enriched)
                            if saved:
                                _dm_val  = enriched.get("decision_maker_name") or "—"
                                _src_raw = enriched.get("dm_source", "")
                                _src_lbl = _DM_SOURCE_LABELS.get(_src_raw, _src_raw) or "—"
                                st.success(
                                    "Re-enriched. DM: "
                                    + str(_dm_val)
                                    + " (" + str(_src_lbl) + ")"
                                )
                            else:
                                st.warning("Enriched but DB save failed.")
                        st.rerun()
                    except Exception as e:
                        st.error("Failed: " + str(e))

    with c3:
        if st.button("Mark Called", key=safe_key(lead_id, "mark_called")):
            try:
                update_lead_status(lead_id, "called")
                st.success("Updated.")
                st.rerun()
            except Exception as e:
                st.error("Failed: " + str(e))


# ─────────────────────────────────────────────────────────
# Segment row
# ─────────────────────────────────────────────────────────
def render_segment_row(
    seg_key: str,
    seg_data: dict,
    show_cards: bool,
    row_idx: int,
) -> None:
    industry = seg_data["industry"]
    city     = seg_data["city"]
    leads    = seg_data["leads"]
    total    = len(leads)
    emails   = sum(1 for l in leads if l.get("email"))
    phones   = sum(1 for l in leads if l.get("phone"))
    hot      = sum(1 for l in leads if (l.get("fit_score") or 0) >= 70)
    with_dm  = sum(1 for l in leads if l.get("decision_maker_name"))
    safe_seg = re.sub(r"[^a-zA-Z0-9]", "_", seg_key)[:30]

    label = (
        industry + "  |  " + city + "  |  "
        + str(total) + " leads  |  "
        + str(emails) + " emails  |  "
        + str(phones) + " phones  |  "
        + str(with_dm) + " DM  |  "
        + str(hot) + " hot"
    )

    with st.expander(label, expanded=False):
        seg_df = pd.DataFrame([
            {
                "Business":   l.get("business_name"),
                "Phone":      l.get("phone"),
                "Email":      l.get("email"),
                "Website":    l.get("website"),
                "Score":      l.get("fit_score") or l.get("opportunity_score"),
                "Status":     l.get("contact_status"),
                "Source":     l.get("source"),
                "City":       l.get("city"),
                "Country":    l.get("country"),
                "DM Name":    l.get("decision_maker_name", ""),
                "DM Source":  _DM_SOURCE_LABELS.get(
                    l.get("dm_source", ""), l.get("dm_source", ""),
                ),
                "CQC Rating": l.get("cqc_rating", ""),
                "CQC URL":    l.get("cqc_url", ""),
                "CQC ID":     l.get("cqc_location_id", ""),
            }
            for l in leads
        ])

        _, col_btn = st.columns([3, 1])
        with col_btn:
            fname = (
                industry + "_" + city + ".csv"
            ).replace(" ", "_").replace("/", "_")
            st.download_button(
                "Export CSV",
                data=seg_df.to_csv(index=False),
                file_name=fname,
                mime="text/csv",
                key="exp_" + safe_seg + "_" + str(row_idx),
            )

        if show_cards:
            for idx, lead in enumerate(leads[:10]):
                try:
                    render_lead_card(lead)
                except Exception as e:
                    st.warning("Card #" + str(idx + 1) + " failed: " + str(e))
            if total > 10:
                st.info(
                    "Showing first 10 of " + str(total)
                    + ". Export CSV to see all."
                )
        else:
            try:
                st.dataframe(
                    seg_df,
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, 40 + len(leads) * 35),
                )
            except Exception as e:
                st.warning("Table error: " + str(e))


# ─────────────────────────────────────────────────────────
# Segment view
# ─────────────────────────────────────────────────────────
def render_segment_view(leads: list) -> None:
    if not leads:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#128229;</div>'
            '<div class="lp-empty-title">No leads match current filters</div>'
            '<div class="lp-empty-sub">Adjust the filters above or run a new search.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    segments   = build_segments(leads)
    seg_count  = len(segments)
    total      = len(leads)
    all_emails = sum(1 for l in leads if l.get("email"))
    all_phones = sum(1 for l in leads if l.get("phone"))
    all_hot    = sum(1 for l in leads if (l.get("fit_score") or 0) >= 70)
    all_dm     = sum(1 for l in leads if l.get("decision_maker_name"))

    st.markdown(
        '<div class="lp-seg-banner">'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value">' + str(seg_count) + '</span>'
        '<span class="lp-seg-stat-label">segments</span>'
        '</div>'

        '<div class="lp-seg-divider"></div>'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value">' + str(f"{total:,}") + '</span>'
        '<span class="lp-seg-stat-label">leads</span>'
        '</div>'

        '<div class="lp-seg-divider"></div>'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value" style="color:' + _GREEN + ';">'
        + str(f"{all_emails:,}") +
        '</span>'
        '<span class="lp-seg-stat-label">emails</span>'
        '</div>'

        '<div class="lp-seg-divider"></div>'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value" style="color:' + _BRAND_BLUE + ';">'
        + str(f"{all_phones:,}") +
        '</span>'
        '<span class="lp-seg-stat-label">phones</span>'
        '</div>'

        '<div class="lp-seg-divider"></div>'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value" style="color:' + _BRAND_PUR + ';">'
        + str(f"{all_dm:,}") +
        '</span>'
        '<span class="lp-seg-stat-label">with DM</span>'
        '</div>'

        '<div class="lp-seg-divider"></div>'

        '<div class="lp-seg-stat">'
        '<span class="lp-seg-stat-value" style="color:' + _RED + ';">'
        + str(f"{all_hot:,}") +
        '</span>'
        '<span class="lp-seg-stat-label">hot</span>'
        '</div>'

        '</div>',
        unsafe_allow_html=True,
    )

    scraping_active = _is_scraping_active()
    ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])

    with ctrl1:
        if scraping_active:
            show_cards = True
            st.caption("Card view forced while scraping.")
        else:
            show_cards = st.checkbox(
                "Show card view inside segments",
                value=False,
                key="seg_show_cards",
            )

    all_df = pd.DataFrame([
        {
            "Business":   l.get("business_name"),
            "Industry":   l.get("industry"),
            "City":       l.get("city"),
            "Country":    l.get("country"),
            "Phone":      l.get("phone"),
            "Email":      l.get("email"),
            "Website":    l.get("website"),
            "Score":      l.get("fit_score") or l.get("opportunity_score"),
            "DM Name":    l.get("decision_maker_name", ""),
            "DM Source":  _DM_SOURCE_LABELS.get(
                l.get("dm_source", ""), l.get("dm_source", "")
            ),
            "CQC Rating": l.get("cqc_rating", ""),
            "CQC URL":    l.get("cqc_url", ""),
            "CQC ID":     l.get("cqc_location_id", ""),
        }
        for l in leads
    ])

    with ctrl2:
        st.download_button(
            "Export All",
            data=all_df.to_csv(index=False),
            file_name="all_leads.csv",
            mime="text/csv",
            key="export_all_seg",
        )
    with ctrl3:
        hot_df = all_df[all_df["Score"].fillna(0) >= 70]
        st.download_button(
            "Export Hot (" + str(len(hot_df)) + ")",
            data=hot_df.to_csv(index=False),
            file_name="hot_leads.csv",
            mime="text/csv",
            key="export_hot_seg",
        )

    for row_idx, (seg_key, seg_data) in enumerate(segments.items()):
        render_segment_row(seg_key, seg_data, show_cards, row_idx)


# ─────────────────────────────────────────────────────────
# Table view
# ─────────────────────────────────────────────────────────
def render_table_view(leads: list) -> None:
    _section_header("CRM Table View", "Interactive lead management.")

    if not leads:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#128202;</div>'
            '<div class="lp-empty-title">No leads match current filters</div>'
            '<div class="lp-empty-sub">Adjust the filters above.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    if _is_scraping_active():
        st.info("Table paused during scraping.")
        return

    rows = [
        {
            "Business":   l.get("business_name"),
            "Industry":   l.get("industry"),
            "City":       l.get("city"),
            "Country":    l.get("country"),
            "Phone":      l.get("phone"),
            "Email":      l.get("email"),
            "Website":    l.get("website"),
            "Score":      l.get("fit_score") or l.get("opportunity_score"),
            "Status":     l.get("contact_status"),
            "Source":     l.get("source"),
            "Enriched":   l.get("enrichment_status"),
            "DM Name":    l.get("decision_maker_name", ""),
            "DM Source":  _DM_SOURCE_LABELS.get(
                l.get("dm_source", ""), l.get("dm_source", "")
            ),
            "CQC Rating": l.get("cqc_rating", ""),
            "CQC URL":    l.get("cqc_url", ""),
            "CQC ID":     l.get("cqc_location_id", ""),
        }
        for l in leads
    ]
    df = pd.DataFrame(rows)

    try:
        st.dataframe(df, use_container_width=True, hide_index=True, height=500)
    except Exception as e:
        st.warning("Table error: " + str(e))

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Export All (CSV)",
            data=df.to_csv(index=False),
            file_name="leads.csv",
            mime="text/csv",
            key="export_all_csv",
        )
    with c2:
        if "Score" in df.columns:
            hot_df = df[df["Score"].fillna(0) >= 70]
            if not hot_df.empty:
                st.download_button(
                    "Export Hot (" + str(len(hot_df)) + ")",
                    data=hot_df.to_csv(index=False),
                    file_name="hot_leads.csv",
                    mime="text/csv",
                    key="export_hot_csv",
                )


# ─────────────────────────────────────────────────────────
# Card view
# ─────────────────────────────────────────────────────────
def render_card_view(leads: list) -> None:
    _section_header("Lead Cards", "Premium CRM card view.")

    if not leads:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#127183;</div>'
            '<div class="lp-empty-title">No leads match current filters</div>'
            '<div class="lp-empty-sub">Adjust the filters above.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    per_page    = 8
    total_pages = max(1, (len(leads) + per_page - 1) // per_page)
    page = st.number_input(
        "Page", min_value=1, max_value=total_pages,
        value=1, key="leads_card_page",
    )
    start = (page - 1) * per_page
    end   = start + per_page

    st.markdown(
        '<div class="lp-page-info">'
        'Showing <b>' + str(f"{len(leads):,}") + '</b> leads &mdash; '
        'Page <b>' + str(page) + '</b> of <b>' + str(total_pages) + '</b>'
        '</div>',
        unsafe_allow_html=True,
    )

    for idx, lead in enumerate(leads[start:end]):
        try:
            render_lead_card(lead)
        except Exception as e:
            st.warning("Card #" + str(start + idx + 1) + " failed: " + str(e))


# ─────────────────────────────────────────────────────────
# Nigeria helpers
# ─────────────────────────────────────────────────────────
def _ng_init_state() -> None:
    defaults = {
        "ng_leads_results":  [],
        "ng_leads_enriched": [],
        "ng_leads_error":    None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _ng_build_wa_link(phone: str, message: str) -> str:
    from urllib.parse import quote as _quote
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("0"):
        digits = "+234" + digits[1:]
    elif digits.startswith("234") and not digits.startswith("+"):
        digits = "+" + digits
    return "https://wa.me/" + digits + "?text=" + _quote(message)


def _ng_message(
    dm_name: str, business_name: str, niche: str, city: str,
) -> str:
    greeting   = "Hi " + dm_name if dm_name else "Hello"
    city_short = city.split()[0] if city else "Nigeria"
    msgs = {
        "private_school": (
            greeting + ", I came across " + business_name
            + " and was impressed by your work in education in "
            + city_short + ". I'd love to discuss how we can help you"
            " attract more quality students. Do you have 5 minutes this week?"
        ),
        "private_hospital": (
            greeting + ", I noticed " + business_name + " serves "
            + city_short + ". I help clinics grow their patient base"
            " through digital marketing. Could we connect briefly?"
        ),
        "real_estate": (
            greeting + ", I came across " + business_name
            + " and your portfolio caught my attention. I help real"
            " estate companies in " + city_short + " close more deals."
            " Would you be open to a quick call?"
        ),
        "hotel": (
            greeting + ", " + business_name + " looks like a great property."
            " I help hotels in " + city_short + " increase bookings."
            " Can we speak briefly?"
        ),
        "law_firm": (
            greeting + ", I found " + business_name
            + " while researching top law firms in " + city_short
            + ". I help law firms attract high-value clients."
            " Would you be open to a brief chat?"
        ),
    }
    return msgs.get(
        niche,
        greeting + ", I'd love to explore how we can help "
        + business_name + " grow in " + city_short + ". Can we connect?",
    )


def _ng_enrich_one(raw: dict, city_display: str) -> dict:
    lead  = dict(raw)
    niche = lead.get("niche", "")
    name  = lead.get("business_name", "")
    if not lead.get("city"):
        lead["city"] = city_display

    if _NG_ENRICHER_OK:
        try:
            lead = NigeriaEnricher().enrich_lead(lead)
        except Exception:
            pass

    dm_name = dm_title = ""
    if _NG_DM_OK:
        try:
            dm = DecisionMakerFinder().find(
                business_name=name,
                website=lead.get("website", ""),
                city=city_display,
                niche=niche,
            )
            dm_name  = dm.get("decision_maker_name", "")
            dm_title = dm.get("decision_maker_title", "")
        except Exception:
            pass

    if not dm_name:
        dm_name = lead.get("decision_maker", "") or lead.get("dm_name", "")
    if not dm_title:
        dm_title = _NG_NICHE_DM_TITLES.get(niche, "Director")

    lead["dm_name"]  = dm_name
    lead["dm_title"] = dm_title

    wa = lead.get("whatsapp_number", "") or lead.get("phone", "")
    if wa:
        msg = _ng_message(dm_name, name, niche, city_display)
        lead["outreach_message"] = msg
        lead["wa_outreach_url"]  = _ng_build_wa_link(wa, msg)
        if not lead.get("whatsapp_number"):
            lead["whatsapp_number"] = wa

    filled = sum(1 for f in [
        lead.get("business_name"), lead.get("phone"),
        lead.get("whatsapp_number"), lead.get("email"),
        lead.get("dm_name"), lead.get("website"),
        lead.get("address"),
    ] if f and str(f).strip())
    lead["completeness_pct"] = int(filled / 7 * 100)

    has_wa = bool(lead.get("whatsapp_number"))
    has_dm = bool(lead.get("dm_name"))
    pct    = lead["completeness_pct"]
    lead["confidence"] = (
        "high"   if has_wa and has_dm and pct >= 60 else
        "medium" if (has_wa or has_dm) and pct >= 35 else
        "low"
    )
    return lead


# ─────────────────────────────────────────────────────────
# FIX-NG-SAVE-01: Save-to-database helpers
#
# These two functions close the actual root cause of the Nigeria
# WhatsApp queue bug: neither this page nor nigeria_dashboard.py ever
# called insert_lead() anywhere. Search + enrich only ever wrote to
# st.session_state / CSV. This meant every Nigeria batch, no matter
# how it was routed, was invisible to Call Center/Dashboard/Reports.
# ─────────────────────────────────────────────────────────

def _map_ng_lead_for_db(
    lead: dict, niche_label: str = "", city_label: str = "",
) -> dict:
    """
    Maps this page's Nigeria enrichment field names to the exact
    column names database.py's insert_lead() expects. Without this,
    insert_lead() silently drops "niche", "dm_name", "dm_title",
    "whatsapp_number" because they don't match any column in its
    internal allow-list (_LEAD_INSERT_FIELDS) — it only writes keys
    that are an exact string match.
    """
    mapped = dict(lead)

    # niche -> industry
    if not str(mapped.get("industry") or "").strip():
        niche_val = mapped.get("niche") or niche_label
        if niche_val:
            mapped["industry"] = niche_val

    # dm_name -> decision_maker_name
    if not str(mapped.get("decision_maker_name") or "").strip():
        dm_val = mapped.get("dm_name") or mapped.get("decision_maker") or ""
        if dm_val:
            mapped["decision_maker_name"] = dm_val

    # dm_title -> decision_maker_title
    if not str(mapped.get("decision_maker_title") or "").strip():
        dm_title_val = mapped.get("dm_title") or ""
        if dm_title_val:
            mapped["decision_maker_title"] = dm_title_val

    # whatsapp_number -> phone (only fills if phone is blank —
    # never overwrites a real landline/mobile already captured)
    if not str(mapped.get("phone") or "").strip():
        wa_val = mapped.get("whatsapp_number") or ""
        if wa_val:
            mapped["phone"] = wa_val

    # This section is Nigeria-only — force country/market accordingly
    mapped["country"] = "Nigeria"
    if not str(mapped.get("market") or "").strip():
        mapped["market"] = "Nigeria"

    # City fallback if enrichment somehow left it blank
    if not str(mapped.get("city") or "").strip() and city_label:
        mapped["city"] = city_label

    # Source tagging for traceability in Reports/Dashboard
    if not str(mapped.get("source") or "").strip():
        mapped["source"] = "nigeria_google_maps"

    # Mark enrichment complete so scoring/routing pipelines that
    # gate on enrichment_status (e.g. get_unscored_leads()) pick
    # this lead up correctly instead of treating it as "pending"
    if not str(mapped.get("enrichment_status") or "").strip() \
            or mapped.get("enrichment_status") == "pending":
        mapped["enrichment_status"] = "complete"

    return mapped


def _save_ng_leads_to_db(
    leads: list, niche_label: str = "", city_label: str = "",
) -> dict:
    """
    Persists a list of enriched Nigeria leads to the leads table via
    insert_lead(), then immediately applies channel routing via
    engine.router.assign_channel()/apply_routing_updates() on the
    same row — so best_channel/wa_status/whatsapp_ready are set at
    save time, with no separate backfill script required for future
    batches.

    Returns {"saved", "updated", "skipped", "errors"} counts.
    """
    summary = {"saved": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not _DB_AVAILABLE:
        summary["errors"] = len(leads)
        return summary

    for lead in leads:
        try:
            mapped = _map_ng_lead_for_db(lead, niche_label, city_label)

            if not str(mapped.get("business_name") or "").strip():
                summary["skipped"] += 1
                continue

            ok, status, lead_id = insert_lead(mapped)

            if not ok:
                summary["skipped"] += 1
                continue

            if status == "inserted":
                summary["saved"] += 1
            elif status == "updated":
                summary["updated"] += 1
            else:
                summary["skipped"] += 1

            # Apply routing on the row we just wrote — this is what
            # makes best_channel/whatsapp_ready visible in Call
            # Center immediately, with zero manual backfill needed.
            if lead_id and _ROUTER_AVAILABLE:
                try:
                    routing_input       = dict(mapped)
                    routing_input["id"] = lead_id
                    routing = assign_channel(routing_input)
                    if routing:
                        apply_routing_updates(lead_id, routing)
                except Exception:
                    pass  # routing failure must never block the save

        except Exception:
            summary["errors"] += 1

    return summary


# ─────────────────────────────────────────────────────────
# BUG-NG-01: Nigeria DB lead card
# Renders a single saved Nigeria lead from the database.
# Uses ng-db-lead-card CSS class (green left border).
# Does NOT use render_lead_card() — that is for UK/US leads.
# ─────────────────────────────────────────────────────────
def _render_ng_db_lead_card(lead: dict, card_index: int) -> None:
    """
    Render a single saved Nigerian lead from the database.
    Called only by _render_ng_db_leads_section().
    card_index used to generate unique Streamlit widget keys.
    """
    bname   = _esc(lead.get("business_name") or "Unknown Business")
    phone   = _esc(lead.get("phone")   or "—")
    email   = _esc(lead.get("email")   or "—")
    website = _esc((lead.get("website") or "—")[:42])
    address = _esc(lead.get("address") or lead.get("city") or "—")
    niche   = _esc(lead.get("industry") or lead.get("niche") or "—")
    dm      = _esc(
        lead.get("decision_maker_name") or
        lead.get("dm_name") or
        ""
    )
    wa_num  = lead.get("whatsapp_number") or lead.get("phone") or ""
    score   = _safe_int(lead.get("fit_score") or 0)

    # WhatsApp link — Nigeria rule: WhatsApp primary
    wa_btn_html = ""
    if wa_num:
        wa_digits = re.sub(r"[^\d+]", "", wa_num)
        if wa_digits.startswith("0"):
            wa_digits = "+234" + wa_digits[1:]
        elif wa_digits.startswith("234") and not wa_digits.startswith("+"):
            wa_digits = "+" + wa_digits
        wa_href = "https://wa.me/" + wa_digits
        wa_btn_html = (
            '<a href="' + wa_href + '" target="_blank"'
            ' style="display:inline-flex;align-items:center;gap:5px;'
            'padding:5px 12px;border-radius:8px;'
            'background:rgba(37,211,102,0.15);'
            'border:1px solid rgba(37,211,102,0.3);'
            'color:#25D366;font-size:11px;font-weight:700;'
            'text-decoration:none;margin-top:8px;">'
            'WhatsApp'
            '</a>'
        )

    dm_html = ""
    if dm:
        dm_html = (
            '<div style="font-size:11px;color:#25D366;'
            'font-weight:700;margin-bottom:8px;">'
            + dm +
            '</div>'
        )

    score_col = _NG_GREEN if score >= 5 else _TEXT_MUT
    score_html = (
        '<div style="text-align:right;flex-shrink:0;">'
        '<div style="font-size:20px;font-weight:900;'
        'color:' + score_col + ';line-height:1;">'
        + str(score) +
        '</div>'
        '<div style="font-size:9px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:1px;'
        'color:' + _TEXT_MUT + ';margin-top:2px;">'
        'Score'
        '</div>'
        '</div>'
    )

    st.markdown(
        '<div class="ng-db-lead-card">'
        '<div style="display:flex;justify-content:space-between;'
        'align-items:flex-start;margin-bottom:4px;">'
        '<div style="flex:1;">'
        '<div class="ng-lead-name">' + bname + '</div>'
        + dm_html +
        '</div>'
        + score_html +
        '</div>'
        '<div class="ng-lead-grid">'
        '<div>'
        '<div class="ng-lead-field-label">Phone</div>'
        '<div class="ng-lead-field-value">' + phone + '</div>'
        '</div>'
        '<div>'
        '<div class="ng-lead-field-label">WhatsApp</div>'
        '<div class="ng-lead-field-value">'
        + _esc(wa_num or "—") +
        '</div>'
        '</div>'
        '<div>'
        '<div class="ng-lead-field-label">Email</div>'
        '<div class="ng-lead-field-value">' + email + '</div>'
        '</div>'
        '<div>'
        '<div class="ng-lead-field-label">Niche</div>'
        '<div class="ng-lead-field-value">' + niche + '</div>'
        '</div>'
        '</div>'
        '<div style="font-size:11px;color:' + _TEXT_MUT + ';'
        'margin-bottom:4px;">'
        + address +
        '</div>'
        '<div style="font-size:11px;color:' + _TEXT_MUT + ';">'
        'Website: ' + website +
        '</div>'
        + wa_btn_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# BUG-NG-01: Saved Nigeria DB leads section
# NG-FILTER: Industry filter added — v10.3
# ─────────────────────────────────────────────────────────
def _render_ng_db_leads_section() -> None:
    """
    BUG-NG-01 FIX: Load and display Nigerian leads from the database.

    This runs at the TOP of render_nigeria_section() before the
    search form. It is completely independent of session state.

    Data source: get_all_leads() filtered to country = 'Nigeria'.
    Matching values: 'nigeria', 'ng' (case-insensitive).

    NG-FILTER: Industry filter added after summary banner.
    Filter uses session state to persist across tab switches.
    Summary banner always shows db_ng_leads totals (unfiltered).
    Tabs use filtered_ng_leads.
    """
    if not _DB_AVAILABLE:
        return

    # ── Load from DB ─────────────────────────────────────────
    try:
        all_leads = get_all_leads(limit=5000)
    except Exception as exc:
        st.warning("Could not load saved Nigeria leads: " + str(exc))
        return

    # Filter to Nigeria only — match _NIGERIA_COUNTRY_VALUES
    db_ng_leads = [
        l for l in all_leads
        if str(l.get("country", "")).strip().lower()
        in _NIGERIA_COUNTRY_VALUES
    ]

    if not db_ng_leads:
        # Silent — no banner if no Nigeria leads saved yet
        return

    # ── STEP 1 — Build industry options (no extra DB query) ──
    # Built from db_ng_leads directly.
    # Falls back to niche field if industry is blank.
    # Blank/None values excluded. Sorted alphabetically.
    available_industries = sorted(set(
        l.get("industry") or l.get("niche") or ""
        for l in db_ng_leads
        if (l.get("industry") or l.get("niche") or "").strip()
    ))
    industry_options = ["All"] + available_industries

    # ── STEP 2 — Session state ───────────────────────────────
    # Initialise only if not already set.
    # Preserves selected filter when user switches tabs.
    if "ng_industry_filter" not in st.session_state:
        st.session_state["ng_industry_filter"] = "All"

    # ── Summary banner ───────────────────────────────────────
    # ALWAYS uses db_ng_leads (unfiltered).
    # Shows overall Nigeria totals regardless of filter selection.
    total_ng   = len(db_ng_leads)
    with_phone = sum(1 for l in db_ng_leads if l.get("phone"))
    with_email = sum(1 for l in db_ng_leads if l.get("email"))
    with_dm    = sum(
        1 for l in db_ng_leads
        if l.get("decision_maker_name") or l.get("dm_name")
    )
    with_wa    = sum(
        1 for l in db_ng_leads
        if l.get("whatsapp_number") or l.get("phone")
    )

    st.markdown(
        '<div style="background:linear-gradient(135deg,'
        'rgba(37,211,102,0.08),rgba(37,211,102,0.03));'
        'border:1px solid rgba(37,211,102,0.25);'
        'border-radius:14px;padding:16px 20px;margin-bottom:16px;">'

        '<div style="font-size:10px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:1.5px;'
        'color:#25D366;margin-bottom:10px;">'
        'Saved Nigerian Leads — Database'
        '</div>'

        '<div style="display:flex;gap:24px;flex-wrap:wrap;">'

        '<div>'
        '<div style="font-size:22px;font-weight:900;color:#f1f5f9;">'
        + str(total_ng) +
        '</div>'
        '<div style="font-size:11px;color:#475569;">Total</div>'
        '</div>'

        '<div>'
        '<div style="font-size:22px;font-weight:900;color:#25D366;">'
        + str(with_wa) +
        '</div>'
        '<div style="font-size:11px;color:#475569;">WhatsApp</div>'
        '</div>'

        '<div>'
        '<div style="font-size:22px;font-weight:900;color:#7c5cff;">'
        + str(with_dm) +
        '</div>'
        '<div style="font-size:11px;color:#475569;">Decision Makers</div>'
        '</div>'

        '<div>'
        '<div style="font-size:22px;font-weight:900;color:#10b981;">'
        + str(with_email) +
        '</div>'
        '<div style="font-size:11px;color:#475569;">Emails</div>'
        '</div>'

        '<div>'
        '<div style="font-size:22px;font-weight:900;color:#38bdf8;">'
        + str(with_phone) +
        '</div>'
        '<div style="font-size:11px;color:#475569;">Phones</div>'
        '</div>'

        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── STEP 3 — Filter UI ───────────────────────────────────
    # Placed AFTER summary banner, BEFORE tabs.
    # fi_col1 holds selectbox (wide). fi_col2 shows live count.
    fi_col1, fi_col2 = st.columns([3, 1])
    with fi_col1:
        selected_industry = st.selectbox(
            "Filter by Industry",
            options=industry_options,
            key="ng_industry_filter",
            label_visibility="collapsed",
        )
    with fi_col2:
        filter_count = (
            len(db_ng_leads) if selected_industry == "All"
            else sum(
                1 for l in db_ng_leads
                if (l.get("industry") or l.get("niche") or "")
                == selected_industry
            )
        )
        st.markdown(
            '<div style="padding-top:6px;font-size:13px;'
            'font-weight:700;color:#25D366;">'
            + str(filter_count) + " leads"
            + '</div>',
            unsafe_allow_html=True,
        )

    # ── STEP 4 — Apply filter ────────────────────────────────
    # filtered_ng_leads is what all tabs below render from.
    # db_ng_leads remains untouched for summary banner above.
    if selected_industry == "All":
        filtered_ng_leads = db_ng_leads
    else:
        filtered_ng_leads = [
            l for l in db_ng_leads
            if (l.get("industry") or l.get("niche") or "")
            == selected_industry
        ]

    # ── STEP 5 — Zero results guard ──────────────────────────
    # Early return before tabs render.
    # Shows styled empty state with selected industry name.
    if not filtered_ng_leads:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#128269;</div>'
            '<div class="lp-empty-title">No leads for '
            + _esc(selected_industry) + '</div>'
            '<div class="lp-empty-sub">'
            'Try a different industry or select All.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── STEP 6 — Tabs (all use filtered_ng_leads) ────────────
    # Tab headers show filtered counts.
    # Export tab label and filename include industry slug.
    db_tab1, db_tab2, db_tab3 = st.tabs([
        "Table (" + str(len(filtered_ng_leads)) + ")",
        "Cards (first 20)",
        "Export — " + selected_industry,
    ])

    with db_tab1:
        rows = [
            {
                "Business":       l.get("business_name", ""),
                "Niche":          l.get("industry") or l.get("niche", ""),
                "City":           l.get("city", ""),
                "Phone":          l.get("phone", ""),
                "WhatsApp":       l.get("whatsapp_number") or l.get("phone", ""),
                "Email":          l.get("email", ""),
                "Website":        l.get("website", ""),
                "Decision Maker": (
                    l.get("decision_maker_name") or
                    l.get("dm_name", "")
                ),
                "Score":          l.get("fit_score", 0),
                "Source":         l.get("source", ""),
            }
            for l in filtered_ng_leads
        ]
        df_db = pd.DataFrame(rows)
        try:
            st.dataframe(
                df_db,
                use_container_width=True,
                hide_index=True,
                height=min(500, 40 + len(rows) * 35),
            )
        except Exception as exc:
            st.warning("Table error: " + str(exc))

    with db_tab2:
        shown = filtered_ng_leads[:20]
        for idx, lead in enumerate(shown):
            try:
                _render_ng_db_lead_card(lead, card_index=idx)
            except Exception as exc:
                st.warning(
                    "Card " + str(idx + 1) + " failed: " + str(exc)
                )
        if len(filtered_ng_leads) > 20:
            st.info(
                "Showing first 20 of "
                + str(len(filtered_ng_leads))
                + " saved leads. Export CSV to see all."
            )

    with db_tab3:
        ts       = datetime.now().strftime("%Y%m%d_%H%M")
        safe_ind = selected_industry.replace(" ", "_").lower()

        if not filtered_ng_leads:
            st.info("No Nigeria leads to export.")
        else:
            rows_exp = [
                {
                    "Business":       l.get("business_name", ""),
                    "Niche":          l.get("industry") or l.get("niche", ""),
                    "City":           l.get("city", ""),
                    "Country":        l.get("country", ""),
                    "Phone":          l.get("phone", ""),
                    "WhatsApp":       (
                        l.get("whatsapp_number") or l.get("phone", "")
                    ),
                    "Email":          l.get("email", ""),
                    "Website":        l.get("website", ""),
                    "Address":        l.get("address", ""),
                    "Decision Maker": (
                        l.get("decision_maker_name") or
                        l.get("dm_name", "")
                    ),
                    "Score":          l.get("fit_score", 0),
                    "Source":         l.get("source", ""),
                    "Enriched":       l.get("enrichment_status", ""),
                }
                for l in filtered_ng_leads
            ]
            df_exp = pd.DataFrame(rows_exp)

            exp_c1, exp_c2 = st.columns(2)
            with exp_c1:
                st.download_button(
                    "Download All Nigeria Leads (CSV)",
                    data=df_exp.to_csv(index=False),
                    file_name="nigeria_" + safe_ind + "_" + ts + ".csv",
                    mime="text/csv",
                    key="ng_db_export_all",
                )
            with exp_c2:
                wa_rows = [r for r in rows_exp if r.get("WhatsApp")]
                if wa_rows:
                    df_wa = pd.DataFrame(wa_rows)
                    st.download_button(
                        "WhatsApp Ready (" + str(len(wa_rows)) + ")",
                        data=df_wa.to_csv(index=False),
                        file_name="nigeria_wa_ready_" + ts + ".csv",
                        mime="text/csv",
                        key="ng_db_export_wa",
                    )

    st.markdown(
        '<div style="height:4px;background:rgba(37,211,102,0.12);'
        'border-radius:4px;margin:20px 0 24px 0;"></div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Nigeria section UI
# BUG-NG-01 FIX: _render_ng_db_leads_section() called first
# so saved DB leads are always visible above the search form.
# FIX-NG-SAVE-01: Save to Database button added to the search form
# button row so newly enriched leads can be persisted.
# ─────────────────────────────────────────────────────────
def render_nigeria_section() -> None:
    _ng_init_state()

    # ── Hero banner ──────────────────────────────────────────
    st.markdown(
        '<div style="background:linear-gradient(135deg,'
        'rgba(37,211,102,0.06),rgba(37,211,102,0.02));'
        'border:1px solid rgba(37,211,102,0.2);'
        'border-radius:16px;padding:20px 24px;margin-bottom:20px;">'
        '<div style="font-size:11px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:2px;'
        'color:#25D366;margin-bottom:6px;">'
        'Nigeria B2B Lead Engine'
        '</div>'
        '<div style="font-size:18px;font-weight:900;'
        'color:#f1f5f9;margin-bottom:6px;">'
        'Nigerian Business Discovery'
        '</div>'
        '<div style="font-size:12px;color:#475569;">'
        'Find decision makers across 7 niches and 21 cities. '
        'Data is fully separate from UK leads.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── BUG-NG-01 FIX ───────────────────────────────────────
    _render_ng_db_leads_section()

    # ── Engine status ────────────────────────────────────────
    cs1, cs2, cs3 = st.columns(3)
    with cs1:
        if _NG_CLIENT_OK:
            st.success("Search Engine Ready")
        else:
            st.error("Search Engine Not Loaded")
    with cs2:
        if _NG_ENRICHER_OK:
            st.success("Enricher Ready")
        else:
            st.warning("Enricher Not Available")
    with cs3:
        if _NG_DM_OK:
            st.success("DM Finder Ready")
        else:
            st.warning("DM Finder Not Available")

    st.markdown(
        '<div style="font-size:11px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:1.5px;'
        'color:' + _TEXT_MUT + ';margin:16px 0 8px 0;">'
        'Find New Businesses'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Search form ──────────────────────────────────────────
    col_a, col_b, col_c, col_d = st.columns([2, 2, 1, 1])
    with col_a:
        niche_label = st.selectbox(
            "Niche",
            options=list(_NG_NICHES.keys()),
            key="ng_leads_niche_sel",
        )
    with col_b:
        city_label = st.selectbox(
            "City",
            options=list(_NG_CITIES.keys()),
            key="ng_leads_city_sel",
        )
    with col_c:
        max_results = st.number_input(
            "Max", min_value=5, max_value=60, value=10, key="ng_leads_max",
        )
    with col_d:
        gmaps_key = st.text_input(
            "API Key", placeholder="Optional", type="password", key="ng_leads_api",
        )

    # FIX-NG-SAVE-01: 4th column added for Save to Database button
    btn1, btn2, btn3, btn4 = st.columns(4)
    with btn1:
        search_btn = st.button(
            "Find Businesses", type="primary",
            use_container_width=True, key="ng_leads_search_btn",
            disabled=not _NG_CLIENT_OK,
        )
    with btn2:
        enrich_btn = st.button(
            "Enrich + Decision Makers",
            use_container_width=True, key="ng_leads_enrich_btn",
            disabled=len(st.session_state.get("ng_leads_results", [])) == 0,
        )
    with btn3:
        # FIX-NG-SAVE-01: this is the button that closes the root
        # cause bug — without it, enriched leads never reach the DB.
        save_btn = st.button(
            "Save to Database", type="primary",
            use_container_width=True, key="ng_leads_save_btn",
            disabled=len(st.session_state.get("ng_leads_enriched", [])) == 0,
        )
    with btn4:
        clear_btn = st.button(
            "Clear", use_container_width=True, key="ng_leads_clear_btn",
        )

    if clear_btn:
        st.session_state["ng_leads_results"]  = []
        st.session_state["ng_leads_enriched"] = []
        st.session_state["ng_leads_error"]    = None
        st.rerun()

    niche_key = _NG_NICHES[niche_label]
    city_key  = _NG_CITIES[city_label]
    city_disp = _NG_CITY_DISPLAY.get(city_key, city_label)

    if search_btn and _NG_CLIENT_OK:
        with st.spinner("Searching " + niche_label + " in " + city_label + "..."):
            try:
                client  = NigeriaGoogleMapsClient(
                    api_key=gmaps_key.strip() if gmaps_key else ""
                )
                results = client.search_businesses(
                    niche=niche_key, city_key=city_key,
                    max_results=int(max_results),
                )
                if results:
                    for r in results:
                        r["search_label_niche"] = niche_label
                        r["search_label_city"]  = city_label

                    existing = st.session_state.get("ng_leads_results", [])
                    existing_names = {
                        l.get("business_name", "").lower() for l in existing
                    }
                    new = [
                        r for r in results
                        if r.get("business_name", "").lower() not in existing_names
                    ]
                    st.session_state["ng_leads_results"] = existing + new
                    st.session_state["ng_leads_error"] = None
                    st.success(
                        "Found " + str(len(results))
                        + " businesses (" + str(len(new)) + " new). "
                        "Click Enrich next."
                    )
                else:
                    st.warning("No businesses found. Try different settings.")
            except Exception as exc:
                st.session_state["ng_leads_error"] = str(exc)
                st.error("Search error: " + str(exc))

    if enrich_btn:
        raw = st.session_state.get("ng_leads_results", [])
        if raw:
            enriched_list = []
            prog   = st.progress(0)
            status = st.empty()
            total  = len(raw)

            for i, lead in enumerate(raw):
                bname = lead.get("business_name", "Lead " + str(i + 1))
                status.markdown(
                    "Enriching **" + str(i + 1) + "/" + str(total)
                    + ":** " + bname + "..."
                )
                prog.progress((i + 1) / total)
                try:
                    enriched_list.append(_ng_enrich_one(lead, city_disp))
                except Exception:
                    lead_copy = dict(lead)
                    lead_copy.update({
                        "dm_name": "", "dm_title": "",
                        "confidence": "low", "completeness_pct": 0,
                    })
                    enriched_list.append(lead_copy)
                time.sleep(0.05)

            st.session_state["ng_leads_enriched"] = enriched_list
            prog.empty()
            status.empty()

            with_wa = sum(1 for l in enriched_list if l.get("whatsapp_number"))
            with_dm = sum(1 for l in enriched_list if l.get("dm_name"))
            st.success(
                str(len(enriched_list)) + " enriched | "
                + str(with_dm) + " DMs | "
                + str(with_wa) + " WhatsApp. "
                "Click Save to Database to persist these leads."
            )

    # FIX-NG-SAVE-01: handles the new Save to Database button
    if save_btn:
        enriched = st.session_state.get("ng_leads_enriched", [])
        if not enriched:
            st.warning("Enrich leads first, then save.")
        else:
            with st.spinner(
                "Saving " + str(len(enriched)) + " leads to database..."
            ):
                result = _save_ng_leads_to_db(
                    enriched, niche_label=niche_label, city_label=city_disp,
                )
            st.success(
                "Saved " + str(result["saved"]) + " new, updated "
                + str(result["updated"]) + ", skipped "
                + str(result["skipped"])
                + (
                    ", " + str(result["errors"]) + " errors"
                    if result["errors"] else ""
                )
                + ". Leads now visible in Call Center and Dashboard."
            )
            if result["saved"] or result["updated"]:
                st.session_state["ng_leads_results"]  = []
                st.session_state["ng_leads_enriched"] = []
                st.rerun()

    if st.session_state.get("ng_leads_error"):
        st.error(str(st.session_state["ng_leads_error"]))

    # ── Session state results (new searches this session) ────
    raw      = st.session_state.get("ng_leads_results",  [])
    enriched = st.session_state.get("ng_leads_enriched", [])
    display  = enriched if enriched else raw

    if not display:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#127475;&#127468;</div>'
            '<div class="lp-empty-title">No new search results</div>'
            '<div class="lp-empty-sub">'
            'Select a niche and city above, then click Find Businesses.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    ng_m1, ng_m2, ng_m3, ng_m4, ng_m5 = st.columns(5)
    ng_m1.metric("Total", len(display))
    ng_m2.metric(
        "Decision Makers",
        sum(1 for l in display if l.get("dm_name") or l.get("decision_maker")),
    )
    ng_m3.metric("WhatsApp", sum(1 for l in display if l.get("whatsapp_number")))
    ng_m4.metric("Email",    sum(1 for l in display if l.get("email")))
    ng_m5.metric(
        "High Confidence",
        sum(1 for l in display if l.get("confidence") == "high"),
    )

    st.markdown("---")

    ng_t1, ng_t2, ng_t3 = st.tabs([
        "All " + str(len(display)) + " Leads",
        "Lead Cards",
        "WhatsApp Ready",
    ])

    with ng_t1:
        rows = [{
            "Business Name":  l.get("business_name", ""),
            "Decision Maker": l.get("dm_name", "") or l.get("decision_maker", ""),
            "DM Title":       l.get("dm_title", ""),
            "Phone":          l.get("phone", ""),
            "WhatsApp":       l.get("whatsapp_number", ""),
            "Email":          l.get("email", ""),
            "Website":        l.get("website", ""),
            "Address":        l.get("address", ""),
            "City":           l.get("city", ""),
            "Niche":          l.get("niche", ""),
            "Rating":         l.get("rating", ""),
            "Confidence":     l.get("confidence", ""),
            "Complete %":     l.get("completeness_pct", ""),
            "Source":         l.get("source", ""),
        } for l in display]

        if rows:
            df_ng = pd.DataFrame(rows)
            st.dataframe(df_ng, use_container_width=True, hide_index=True, height=400)
            ts  = datetime.now().strftime("%Y%m%d_%H%M")
            ex1, ex2 = st.columns(2)
            with ex1:
                buf = StringIO()
                df_ng.to_csv(buf, index=False)
                st.download_button(
                    "Download All CSV",
                    data=buf.getvalue(),
                    file_name="nigeria_leads_" + ts + ".csv",
                    mime="text/csv",
                    key="ng_dl_all",
                )
            with ex2:
                wa_rows = [r for r in rows if r.get("WhatsApp")]
                if wa_rows:
                    wa_buf = StringIO()
                    pd.DataFrame(wa_rows).to_csv(wa_buf, index=False)
                    st.download_button(
                        "WhatsApp List (" + str(len(wa_rows)) + ")",
                        data=wa_buf.getvalue(),
                        file_name="nigeria_wa_" + ts + ".csv",
                        mime="text/csv",
                        key="ng_dl_wa",
                    )

    with ng_t2:
        conf_order = {"high": 0, "medium": 1, "low": 2}
        conf_dots  = {
            "high":   "&#128994;",
            "medium": "&#128993;",
            "low":    "&#128308;",
        }

        for lead in sorted(
            display,
            key=lambda l: conf_order.get(l.get("confidence", "low"), 2),
        ):
            conf     = lead.get("confidence", "low")
            dot      = conf_dots.get(conf, "&#128308;")
            bname    = lead.get("business_name", "Unknown")
            dm       = lead.get("dm_name", "") or lead.get("decision_maker", "")
            dm_title = lead.get("dm_title", "")
            wa       = lead.get("whatsapp_number", "")
            phone    = lead.get("phone", "")
            email    = lead.get("email", "")
            website  = lead.get("website", "")
            address  = lead.get("address", "")
            pct      = lead.get("completeness_pct", 0)
            wa_url   = lead.get("wa_outreach_url", "")
            message  = lead.get("outreach_message", "")

            dm_line = ""
            if dm:
                t = " &mdash; " + _esc(dm_title) if dm_title else ""
                dm_line = (
                    '<div class="ng-lead-dm">'
                    + _esc(dm) + t +
                    '</div>'
                )

            wa_btn = ""
            if wa_url:
                wa_btn = (
                    '<a href="' + _esc(wa_url) + '" target="_blank"'
                    ' style="display:inline-flex;align-items:center;gap:6px;'
                    'padding:7px 16px;border-radius:10px;'
                    'background:rgba(37,211,102,0.15);'
                    'border:1px solid rgba(37,211,102,0.3);'
                    'color:#25D366;font-size:12px;font-weight:700;'
                    'text-decoration:none;margin-top:10px;">'
                    'Open WhatsApp Chat'
                    '</a>'
                )

            st.markdown(
                '<div class="ng-lead-card">'
                '<div style="display:flex;justify-content:space-between;'
                'align-items:flex-start;">'
                '<div style="flex:1;">'
                '<div class="ng-lead-name">'
                + dot + ' ' + _esc(bname) +
                '</div>'
                + dm_line +
                '</div>'
                '<div style="text-align:right;flex-shrink:0;">'
                '<div style="font-size:11px;color:' + _TEXT_MUT + ';font-weight:700;">'
                + str(pct) + '% complete'
                '</div>'
                '<div style="font-size:10px;color:' + _TEXT_MUT + ';">'
                + conf.upper() +
                '</div>'
                '</div>'
                '</div>'
                '<div class="ng-lead-grid">'
                '<div>'
                '<div class="ng-lead-field-label">Phone</div>'
                '<div class="ng-lead-field-value">' + _esc(phone or "—") + '</div>'
                '</div>'
                '<div>'
                '<div class="ng-lead-field-label">WhatsApp</div>'
                '<div class="ng-lead-field-value">' + _esc(wa or "—") + '</div>'
                '</div>'
                '<div>'
                '<div class="ng-lead-field-label">Email</div>'
                '<div class="ng-lead-field-value">' + _esc(email or "—") + '</div>'
                '</div>'
                '<div>'
                '<div class="ng-lead-field-label">Website</div>'
                '<div class="ng-lead-field-value">'
                + _esc((website or "—")[:35]) +
                '</div>'
                '</div>'
                '</div>'
                '<div style="font-size:11px;color:' + _TEXT_MUT + ';margin-bottom:6px;">'
                + _esc(address or lead.get("city", "")) +
                '</div>'
                + wa_btn +
                '</div>',
                unsafe_allow_html=True,
            )

            if message:
                with st.expander("Outreach message"):
                    st.write(message)

    with ng_t3:
        wa_leads = [l for l in display if l.get("whatsapp_number")]
        if wa_leads:
            st.markdown("### " + str(len(wa_leads)) + " Leads with WhatsApp")
            conf_dots = {
                "high":   "&#128994;",
                "medium": "&#128993;",
                "low":    "&#128308;",
            }
            for lead in wa_leads:
                conf   = lead.get("confidence", "low")
                dot    = conf_dots.get(conf, "&#128308;")
                bname  = lead.get("business_name", "Unknown")
                dm     = lead.get("dm_name", "") or lead.get("decision_maker", "")
                wa     = lead.get("whatsapp_number", "")
                wa_url = lead.get("wa_outreach_url", "")
                msg    = lead.get("outreach_message", "")

                c1, c2, c3 = st.columns([3, 2, 2])
                with c1:
                    st.markdown(dot + " **" + bname + "**")
                    if dm:
                        st.caption(dm)
                with c2:
                    st.code(wa, language=None)
                with c3:
                    if wa_url:
                        st.markdown("[Send Message](" + wa_url + ")")
                if msg:
                    with st.expander("Message"):
                        st.write(msg)
                st.divider()
        else:
            st.info("No WhatsApp numbers found. Run Enrich first.")


# ─────────────────────────────────────────────────────────
# US-MARKET-01: Shared UK/US rendering — KPIs, filters, tabs.
# Extracted from the old inline UK-only block so US Businesses
# can reuse the exact same rendering path with zero duplication.
# Market-specific filtering happens BEFORE calling this function.
# ─────────────────────────────────────────────────────────
def _render_market_leads_section(
    leads: list,
    market_label: str,
) -> None:
    if not leads:
        st.markdown(
            '<div class="lp-empty">'
            '<div class="lp-empty-icon">&#128229;</div>'
            '<div class="lp-empty-title">No ' + _esc(market_label)
            + ' leads yet</div>'
            '<div class="lp-empty-sub">'
            'Run a search from Lead Search to populate your database.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    render_kpis(leads)
    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
    filtered = render_filters(leads)
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    tabs = st.tabs(["Lead Segments", "CRM Table", "Lead Cards"])
    with tabs[0]:
        render_segment_view(filtered)
    with tabs[1]:
        render_table_view(filtered)
    with tabs[2]:
        render_card_view(filtered)


# ─────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# FIX-L-02: UK market filter
# US-MARKET-01: US Businesses added as a third market option
# ─────────────────────────────────────────────────────────
def render_leads_page() -> None:
    _render_page_css()
    render_hero()

    st.markdown(
        '<div style="font-size:11px;font-weight:700;'
        'text-transform:uppercase;letter-spacing:1.5px;'
        'color:' + _TEXT_MUT + ';margin-bottom:8px;">'
        'Select Market'
        '</div>',
        unsafe_allow_html=True,
    )
    market = st.radio(
        "market_radio",
        options=["UK Businesses", "US Businesses", "Nigeria B2B"],
        horizontal=True,
        label_visibility="collapsed",
        key="leads_market_radio",
    )
    st.divider()

    if market == "Nigeria B2B":
        render_nigeria_section()
        return

    if not _DB_AVAILABLE:
        st.error("Database module not available.")
        return

    try:
        all_leads_raw = get_all_leads(limit=5000)
    except Exception as e:
        st.error("Cannot load leads: " + str(e))
        return

    if market == "US Businesses":
        # US-MARKET-01 — explicit match only, no catch-all fallback
        us_leads = [
            l for l in all_leads_raw
            if str(l.get("country", "")).strip().lower()
            in _US_COUNTRY_VALUES
        ]
        _render_market_leads_section(us_leads, market_label="US")

    else:
        # FIX-L-02 / US-MARKET-01 — UK is the catch-all: everything
        # that is not explicitly Nigeria or US. Preserves legacy
        # behaviour where blank/unrecognised country defaults to UK.
        uk_leads = [
            l for l in all_leads_raw
            if str(l.get("country", "")).strip().lower()
            not in _NIGERIA_COUNTRY_VALUES
            and str(l.get("country", "")).strip().lower()
            not in _US_COUNTRY_VALUES
        ]
        _render_market_leads_section(uk_leads, market_label="UK")


def render() -> None:
    render_leads_page()