# pages/call_center.py
# KNCS LeadGen Pro - v10.1
#
# Audit fixes applied:
#   BUG-027: log_call_outcome() dedup via session_state["_logged_outcomes"]
#            set; (bool, str) return from log_outreach_action() handled
#   BUG-028: _get_sender_name() checks session_state first; business name
#            never used as greeting recipient
#   BUG-029: get_call_queue() sort: market -> wa_status -> -fit_score ->
#            -urgency_score -> priority_rank
#   ISSUE-A: log_call_outcome() revenue write uses save_revenue_record()
#            (enforces UNIQUE(lead_id, service_type) via database.py)
#   ISSUE-B: get_live_stats() high_fit threshold corrected to >= 70 (0-100)
#   ISSUE-C: _score_with_progress() thresholds corrected to 70/50 (0-100)
#   ISSUE-D: render_current_lead() thresholds + label corrected (0-100)
#   ISSUE-F: build_whatsapp_message() fallback uses decision_maker_name
#   ISSUE-G: _get_unscored_lead_ids() enforces enrichment gate
#   ISSUE-J: deal_value cast uses _safe_float()

from __future__ import annotations

from datetime import datetime
import html
import json
import re
from typing import Optional

import pandas as pd
import streamlit as st

# ---------------------------------------------------------
# Sender constants
# ---------------------------------------------------------
SENDER_NAME  = "Amechi"
COMPANY_NAME = "King Networks Consulting Services (KNCS)"
CTA_LINK     = ""

# ---------------------------------------------------------
# Design tokens
# ---------------------------------------------------------
_BG_BASE    = "#060d19"
_BG_SURFACE = "#0b1628"
_BG_RAISED  = "#101f38"
_BG_INPUT   = "#0d1a2e"
_BRAND_BLUE = "#5b8cff"
_BRAND_PUR  = "#7c5cff"
_WA_GREEN   = "#25D366"
_WA_DARK    = "#128c7e"
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
_GLOW_WA    = "rgba(37,211,102,0.15)"

# ---------------------------------------------------------
# Safe DB imports
# ---------------------------------------------------------
try:
    from database import (
        get_connection,
        update_lead_status,
        get_dashboard_stats as _get_stats_raw,
        log_call,
        log_outreach_action,
        get_top_prospects,
        get_ai_call_ready_leads,
        get_whatsapp_ready_leads,
        get_whatsapp_verified_leads,
        get_whatsapp_estimated_leads,
        get_email_ready_leads,
        get_leads_by_priority,
        get_call_ready_leads,
        get_manual_review_leads,
        verify_whatsapp_manually,
        batch_verify_whatsapp,
        save_revenue_record,          # ISSUE-A: BUG-DB-07 write path
    )
    def get_stats() -> dict:
        try:   return _get_stats_raw() or {}
        except Exception: return {}
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE  = False
    get_connection = None
    def get_stats() -> dict:                                   return {}
    def update_lead_status(*a, **k) -> bool:                  return False
    def log_call(*a, **k) -> bool:                            return False
    def log_outreach_action(*a, **k):                         return (False, "DB unavailable")
    def get_top_prospects(limit=100) -> list:                 return []
    def get_ai_call_ready_leads(limit=100) -> list:           return []
    def get_whatsapp_ready_leads(limit=100) -> list:          return []
    def get_whatsapp_verified_leads(limit=100) -> list:       return []
    def get_whatsapp_estimated_leads(limit=100) -> list:      return []
    def get_email_ready_leads(limit=100) -> list:             return []
    def get_leads_by_priority(p, limit=100) -> list:          return []
    def get_call_ready_leads(limit=100) -> list:              return []
    def get_manual_review_leads(limit=50) -> list:            return []
    def verify_whatsapp_manually(lid, verified=True) -> bool: return False
    def batch_verify_whatsapp(lids, verified=True) -> int: return 0
    def save_revenue_record(*a, **k) -> bool:                 return False

# ---------------------------------------------------------
# Safe scorer imports
# ---------------------------------------------------------
try:
    from engine.scorer import (
        get_fit_label,
        get_channel_label,
        get_contact_completeness,
        score_unscored_leads_batch,
        score_and_save_lead,
    )
    _SCORER_AVAILABLE = True
except Exception:
    _SCORER_AVAILABLE = False

    def get_fit_label(fit_score) -> dict:
        # ISSUE-D: thresholds on 0-100 scale
        if fit_score >= 70:
            return {"label": "HIGH FIT",   "color": _GREEN,    "bg": "rgba(16,185,129,0.10)"}
        if fit_score >= 50:
            return {"label": "MEDIUM FIT", "color": _GOLD,     "bg": "rgba(245,158,11,0.10)"}
        if fit_score >= 10:
            return {"label": "LOW FIT",    "color": _TEXT_MUT, "bg": "rgba(71,85,105,0.10)"}
        return         {"label": "UNSCORED",   "color": _TEXT_SEC, "bg": "rgba(148,163,184,0.10)"}

    def get_channel_label(channel) -> dict:
        return {
            "whatsapp": {"label": "WhatsApp", "color": _WA_GREEN,  "bg": "rgba(37,211,102,0.10)"},
            "ai_call":  {"label": "AI Call",  "color": _BRAND_PUR, "bg": "rgba(124,92,255,0.10)"},
            "email":    {"label": "Email",     "color": _BRAND_BLUE,"bg": "rgba(91,140,255,0.10)"},
            "manual":   {"label": "Manual",    "color": _TEXT_MUT,  "bg": "rgba(71,85,105,0.10)"},
        }.get(
            channel or "manual",
            {"label": "Unknown", "color": _TEXT_MUT, "bg": "rgba(71,85,105,0.10)"},
        )

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
            "fields": fields, "filled": filled, "total": len(fields),
        }

    def score_unscored_leads_batch(limit=50, use_ai=True) -> dict:
        return {"total": 0, "scored": 0, "high": 0, "medium": 0, "low": 0}

    def score_and_save_lead(lead_id) -> Optional[dict]:
        return None

# ---------------------------------------------------------
# Safe WhatsApp imports
# ---------------------------------------------------------
try:
    from engine.whatsapp_messages import (
        build_whatsapp_message,
        build_wa_prefill_link,
    )
except Exception:
    def build_whatsapp_message(
        lead, tone="friendly",
        sender_name=SENDER_NAME,
        company_name=COMPANY_NAME,
        cta_link="",
    ) -> str:
        # ISSUE-F: BUG-028 - use decision_maker_name not business_name
        # as the greeting recipient in the fallback too
        dm_name  = (
            lead.get("decision_maker_name")
            or lead.get("decision_maker_first")
            or ""
        )
        greeting = dm_name.strip() if dm_name.strip() else "there"
        return (
            f"Hi {greeting}, I noticed your business could benefit "
            f"from a professional website.\n\n- {sender_name}"
        )

    def build_wa_prefill_link(digits, message) -> str:
        from urllib.parse import quote
        d = re.sub(r"\D", "", str(digits or ""))
        return f"https://wa.me/{d}?text={quote(message)}" if d else ""

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
_VALID_WA = {"verified", "opted_in", "estimated", "business_confirmed", "unknown"}

MARKET_ORDER = {
    "nigeria": 0, "africa": 1, "uk": 2, "europe": 3,
    "north_america": 4, "asia": 5, "middle_east": 6,
    "latam": 7, "oceania": 8, "other": 99,
}

COUNTRY_DIAL_CODES = {
    "nigeria": "234",      "ghana": "233",       "kenya": "254",
    "south africa": "27",  "egypt": "20",        "ethiopia": "251",
    "tanzania": "255",     "uganda": "256",       "rwanda": "250",
    "united kingdom": "44","uk": "44",            "ireland": "353",
    "germany": "49",       "france": "33",        "italy": "39",
    "spain": "34",         "netherlands": "31",
    "uae": "971",          "united arab emirates": "971",
    "saudi arabia": "966", "qatar": "974",        "kuwait": "965",
    "india": "91",         "pakistan": "92",      "bangladesh": "880",
    "indonesia": "62",     "philippines": "63",
    "malaysia": "60",      "singapore": "65",
    "united states": "1",  "usa": "1",            "canada": "1",
    "brazil": "55",        "mexico": "52",        "colombia": "57",
    "argentina": "54",     "australia": "61",
}

_PRIORITY_COLOR = {
    "high":     _GREEN,
    "medium":   _GOLD,
    "low":      _TEXT_MUT,
    "unscored": _TEXT_SEC,
}
_PRIORITY_LABEL = {
    "high":     "HIGH PRIORITY",
    "medium":   "MEDIUM PRIORITY",
    "low":      "LOW PRIORITY",
    "unscored": "UNSCORED",
}
_WA_BADGE = {
    "verified":           (" WA Verified",           _GREEN),
    "opted_in":           (" WA Opted-In",            _GREEN),
    "business_confirmed": (" WA Business Confirmed",  _GOLD),
    "estimated":          (" WA Estimated",           _GOLD),
    "unknown":            (" WA Unknown",              _TEXT_MUT),
}
_STATUS_MAP = {
    " Called - No Answer": "called",
    " Callback Requested":  "callback",
    " Interested":          "interested",
    " Not Interested":      "not_interested",
    " Deal Closed":         "deal_closed",
    " Wrong Number":        "wrong_number",
}

# ---------------------------------------------------------
# BUG-028: Session-aware sender name resolver
# ---------------------------------------------------------

def _get_sender_name() -> str:
    """
    BUG-028: Returns sender name from session state if set,
    falls back to SENDER_NAME constant.
    Sender name is NEVER derived from the lead's business_name.
    """
    try:
        name = st.session_state.get("sender_name", "")
        if name and str(name).strip():
            return str(name).strip()
    except Exception:
        pass
    return SENDER_NAME


# ---------------------------------------------------------
# Page CSS
# ---------------------------------------------------------
def _render_page_css() -> None:
    # CSS uses string concatenation for colour values to avoid
    # f-string maintenance trap (BUG-APP-02 pattern).
    css = (
        "<style>\n"
        ".block-container {"
        "  padding-top: 1.4rem !important;"
        "  padding-bottom: 3rem !important;"
        "  max-width: 1400px !important;"
        "}\n"
        ".cc-hero {"
        "  background: linear-gradient(145deg, " + _BG_SURFACE + " 0%, " + _BG_RAISED + " 50%, " + _BG_SURFACE + " 100%);"
        "  border: 1px solid " + _BORDER + ";"
        "  border-left: 4px solid " + _WA_GREEN + ";"
        "  border-radius: 20px; padding: 24px 28px;"
        "  margin-bottom: 24px; position: relative; overflow: hidden;"
        "}\n"
        ".cc-hero::before {"
        "  content: ''; position: absolute; top: -80px; right: -80px;"
        "  width: 280px; height: 280px;"
        "  background: radial-gradient(circle, " + _GLOW_WA + " 0%, transparent 70%);"
        "  pointer-events: none;"
        "}\n"
        ".cc-hero-eyebrow {"
        "  font-size: 10px; font-weight: 700; text-transform: uppercase;"
        "  letter-spacing: 2px; color: " + _WA_GREEN + "; margin-bottom: 8px;"
        "}\n"
        ".cc-hero-title {"
        "  font-size: 30px; font-weight: 900; color: " + _TEXT_PRI + ";"
        "  letter-spacing: -1px; line-height: 1.15; margin: 0 0 8px 0;"
        "}\n"
        ".cc-hero-sub {"
        "  font-size: 13px; color: " + _TEXT_MUT + "; font-weight: 500; line-height: 1.7;"
        "}\n"
        ".cc-kpi {"
        "  background: linear-gradient(145deg, " + _BG_SURFACE + " 0%, " + _BG_RAISED + " 100%);"
        "  border: 1px solid " + _BORDER + "; border-radius: 18px;"
        "  padding: 18px 16px 14px 16px; position: relative; overflow: hidden;"
        "  transition: transform 0.22s ease, border-color 0.25s ease, box-shadow 0.25s ease;"
        "  cursor: default;"
        "}\n"
        ".cc-kpi:hover {"
        "  transform: translateY(-3px); border-color: " + _BORDER_BRD + ";"
        "  box-shadow: 0 14px 40px rgba(91,140,255,0.14), 0 4px 12px rgba(0,0,0,0.3);"
        "}\n"
        ".cc-kpi-accent {"
        "  position: absolute; top: 0; left: 0; right: 0;"
        "  height: 3px; border-radius: 18px 18px 0 0;"
        "}\n"
        ".cc-kpi-icon  { font-size: 20px; margin-bottom: 10px; line-height: 1; }\n"
        ".cc-kpi-label {"
        "  color: " + _TEXT_MUT + "; font-size: 9.5px; font-weight: 700;"
        "  text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 6px;"
        "}\n"
        ".cc-kpi-value {"
        "  font-size: 24px; font-weight: 900; color: " + _TEXT_PRI + ";"
        "  letter-spacing: -0.8px; line-height: 1; margin-bottom: 5px;"
        "}\n"
        ".cc-kpi-sub { font-size: 11px; font-weight: 600; }\n"
        ".cc-sec-head  { margin: 0 0 14px 0; }\n"
        ".cc-sec-title {"
        "  font-size: 18px; font-weight: 800; color: " + _TEXT_PRI + ";"
        "  letter-spacing: -0.5px; line-height: 1.2; margin: 0 0 3px 0;"
        "}\n"
        ".cc-sec-sub { font-size: 12px; color: " + _TEXT_MUT + "; font-weight: 500; margin: 0; }\n"
        ".cc-lead-header {"
        "  background: linear-gradient(145deg, " + _BG_SURFACE + " 0%, " + _BG_RAISED + " 100%);"
        "  border: 1px solid " + _BORDER + "; border-radius: 18px;"
        "  padding: 20px; margin-bottom: 14px;"
        "  transition: border-color 0.25s ease;"
        "}\n"
        ".cc-lead-header:hover { border-color: " + _BORDER_BRD + "; }\n"
        ".cc-lead-name {"
        "  font-size: 20px; font-weight: 900; color: " + _TEXT_PRI + ";"
        "  letter-spacing: -0.5px; margin-bottom: 6px;"
        "}\n"
        ".cc-lead-meta { font-size: 12px; color: " + _TEXT_MUT + "; margin-bottom: 12px; }\n"
        ".cc-lead-badges { display: flex; flex-wrap: wrap; gap: 5px; }\n"
        ".cc-fit-block { text-align: right; flex-shrink: 0; }\n"
        ".cc-fit-num {"
        "  font-size: 38px; font-weight: 900; line-height: 1; letter-spacing: -2px;"
        "}\n"
        ".cc-fit-label {"
        "  font-size: 9.5px; font-weight: 700; text-transform: uppercase;"
        "  letter-spacing: 1.5px; color: " + _TEXT_MUT + "; margin-top: 2px;"
        "}\n"
        ".cc-opp-num { font-size: 18px; font-weight: 800; color: " + _TEXT_PRI + "; margin-top: 2px; }\n"
        ".cc-opp-label { font-size: 9.5px; color: " + _TEXT_MUT + "; }\n"
        ".cc-score-grid {"
        "  display: grid; grid-template-columns: repeat(5, 1fr);"
        "  gap: 8px; margin-bottom: 14px;"
        "}\n"
        ".cc-score-cell {"
        "  background: rgba(255,255,255,0.025); border: 1px solid " + _BORDER + ";"
        "  border-radius: 12px; padding: 10px; text-align: center;"
        "  transition: border-color 0.2s ease;"
        "}\n"
        ".cc-score-cell:hover { border-color: " + _BORDER_MID + "; }\n"
        ".cc-score-num { font-size: 18px; font-weight: 900; color: " + _TEXT_PRI + "; line-height: 1; margin-bottom: 4px; }\n"
        ".cc-score-lbl {"
        "  font-size: 9px; font-weight: 700; text-transform: uppercase;"
        "  letter-spacing: 0.8px; color: " + _TEXT_MUT + ";"
        "}\n"
        ".cc-wa-verified {"
        "  background: rgba(37,211,102,0.07); border: 1px solid rgba(37,211,102,0.20);"
        "  border-left: 3px solid " + _WA_GREEN + "; border-radius: 12px;"
        "  padding: 12px 14px; margin-bottom: 12px;"
        "  font-size: 13px; font-weight: 700; color: " + _WA_GREEN + ";"
        "}\n"
        ".cc-wa-estimated {"
        "  background: rgba(245,158,11,0.07); border: 1px solid rgba(245,158,11,0.20);"
        "  border-left: 3px solid " + _GOLD + "; border-radius: 12px;"
        "  padding: 12px 14px; margin-bottom: 12px;"
        "  font-size: 13px; font-weight: 700; color: " + _GOLD + ";"
        "}\n"
        ".cc-wa-unknown {"
        "  background: rgba(71,85,105,0.07); border: 1px solid rgba(71,85,105,0.20);"
        "  border-left: 3px solid " + _TEXT_MUT + "; border-radius: 12px;"
        "  padding: 12px 14px; margin-bottom: 12px;"
        "  font-size: 13px; font-weight: 700; color: " + _TEXT_SEC + ";"
        "}\n"
        ".cc-detail {"
        "  background: rgba(255,255,255,0.025); border: 1px solid " + _BORDER + ";"
        "  border-radius: 12px; padding: 12px 14px; margin-bottom: 10px;"
        "  transition: border-color 0.2s ease;"
        "}\n"
        ".cc-detail:hover { border-color: " + _BORDER_MID + "; }\n"
        ".cc-detail-label {"
        "  color: " + _TEXT_MUT + "; font-size: 9.5px; font-weight: 700;"
        "  text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px;"
        "}\n"
        ".cc-detail-value {"
        "  color: " + _TEXT_PRI + "; font-size: 14px; font-weight: 600;"
        "  line-height: 1.5; word-break: break-word;"
        "}\n"
        ".cc-badge {"
        "  display: inline-flex; align-items: center; gap: 4px;"
        "  padding: 3px 9px; border-radius: 999px; font-size: 10px;"
        "  font-weight: 700; text-transform: uppercase;"
        "  letter-spacing: 0.5px; white-space: nowrap;"
        "}\n"
        ".cc-queue-banner {"
        "  background: linear-gradient(135deg, rgba(91,140,255,0.08), rgba(124,92,255,0.04));"
        "  border: 1px solid rgba(91,140,255,0.16); border-radius: 14px;"
        "  padding: 12px 16px; margin-bottom: 14px;"
        "  display: flex; align-items: center; justify-content: space-between;"
        "}\n"
        ".cc-queue-pos  { font-size: 13px; font-weight: 700; color: " + _TEXT_SEC + "; }\n"
        ".cc-queue-total { font-size: 11px; color: " + _TEXT_MUT + "; }\n"
        ".cc-scoring-banner {"
        "  background: linear-gradient(135deg, rgba(91,140,255,0.08), rgba(124,92,255,0.04));"
        "  border: 1px solid rgba(91,140,255,0.20);"
        "  border-radius: 16px; padding: 18px 22px; margin-bottom: 16px;"
        "}\n"
        ".cc-outcome-panel {"
        "  background: linear-gradient(145deg, " + _BG_SURFACE + " 0%, " + _BG_RAISED + " 100%);"
        "  border: 1px solid " + _BORDER + "; border-radius: 16px;"
        "  padding: 18px 20px; margin-top: 8px;"
        "}\n"
        ".cc-wa-btn {"
        "  display: block; text-align: center;"
        "  background: linear-gradient(135deg, " + _WA_GREEN + ", " + _WA_DARK + ");"
        "  color: white; padding: 12px 16px; border-radius: 12px;"
        "  font-weight: 700; font-size: 14px; text-decoration: none;"
        "  margin-bottom: 10px;"
        "  transition: opacity 0.2s ease, transform 0.15s ease, box-shadow 0.25s ease;"
        "}\n"
        ".cc-wa-btn:hover {"
        "  opacity: 0.92; transform: translateY(-2px);"
        "  box-shadow: 0 8px 28px rgba(37,211,102,0.30);"
        "}\n"
        ".cc-call-btn {"
        "  display: inline-block; background: #2563EB; color: white;"
        "  padding: 10px 18px; border-radius: 10px; font-weight: 700;"
        "  font-size: 13px; text-decoration: none; margin-bottom: 10px;"
        "  transition: opacity 0.2s ease, transform 0.15s ease;"
        "}\n"
        ".cc-call-btn:hover { opacity: 0.88; transform: translateY(-2px); }\n"
        ".cc-revenue {"
        "  background: linear-gradient(135deg, rgba(245,158,11,0.07), rgba(245,158,11,0.03));"
        "  border: 1px solid rgba(245,158,11,0.18);"
        "  border-left: 3px solid " + _GOLD + "; border-radius: 0 12px 12px 0;"
        "  padding: 10px 14px; margin-top: 10px; font-size: 13px; color: " + _TEXT_PRI + ";"
        "}\n"
        ".cc-revenue-label {"
        "  font-size: 9.5px; font-weight: 700; text-transform: uppercase;"
        "  letter-spacing: 1px; color: " + _GOLD + "; margin-bottom: 4px; opacity: 0.85;"
        "}\n"
        ".cc-revenue-value {"
        "  font-size: 20px; font-weight: 900; color: " + _TEXT_PRI + "; letter-spacing: -0.5px;"
        "}\n"
        ".stTabs [data-baseweb='tab-list'] { gap: 6px; border-bottom: 1px solid " + _BORDER + "; }\n"
        ".stTabs [data-baseweb='tab'] {"
        "  background: transparent !important;"
        "  border-radius: 10px 10px 0 0 !important;"
        "  padding: 10px 18px !important; color: " + _TEXT_MUT + " !important;"
        "  border: none !important; font-weight: 600 !important; font-size: 13px !important;"
        "}\n"
        ".stTabs [aria-selected='true'] {"
        "  background: rgba(91,140,255,0.10) !important;"
        "  color: " + _BRAND_BLUE + " !important; font-weight: 700 !important;"
        "  border-bottom: 2px solid " + _BRAND_BLUE + " !important;"
        "}\n"
        ".stButton > button {"
        "  border-radius: 12px !important; border: none !important;"
        "  background: linear-gradient(135deg, " + _BRAND_BLUE + ", " + _BRAND_PUR + ") !important;"
        "  color: white !important; font-weight: 700 !important;"
        "  font-size: 13px !important; padding: 11px 18px !important; width: 100%;"
        "  transition: opacity 0.2s ease, transform 0.15s ease, box-shadow 0.25s ease !important;"
        "}\n"
        ".stButton > button:hover {"
        "  transform: translateY(-2px) !important;"
        "  box-shadow: 0 8px 28px rgba(91,140,255,0.32) !important;"
        "  opacity: 0.94 !important;"
        "}\n"
        ".stButton > button[kind='primary'] {"
        "  background: linear-gradient(135deg, " + _WA_GREEN + ", " + _WA_DARK + ") !important;"
        "}\n"
        ".stButton > button[kind='primary']:hover {"
        "  box-shadow: 0 8px 28px rgba(37,211,102,0.30) !important;"
        "}\n"
        ".stTextInput input, .stTextArea textarea,"
        ".stNumberInput input, .stSelectbox > div > div {"
        "  background: " + _BG_INPUT + " !important; border: 1px solid " + _BORDER_MID + " !important;"
        "  border-radius: 10px !important; color: " + _TEXT_PRI + " !important; font-size: 13px !important;"
        "}\n"
        ".stTextInput label, .stTextArea label, .stNumberInput label,"
        ".stSelectbox label, .stRadio label, .stSlider label {"
        "  color: " + _TEXT_MUT + " !important; font-size: 10px !important;"
        "  font-weight: 700 !important; text-transform: uppercase !important;"
        "  letter-spacing: 0.8px !important;"
        "}\n"
        ".stProgress > div > div > div {"
        "  background: linear-gradient(90deg, " + _WA_GREEN + ", " + _WA_DARK + ") !important;"
        "  border-radius: 999px !important;"
        "}\n"
        ".stProgress > div > div {"
        "  background: rgba(255,255,255,0.05) !important;"
        "  border-radius: 999px !important; height: 6px !important;"
        "}\n"
        "[data-testid='stMetric'] {"
        "  background: linear-gradient(145deg, " + _BG_SURFACE + ", " + _BG_RAISED + ");"
        "  border: 1px solid " + _BORDER + "; border-radius: 12px; padding: 12px 14px;"
        "}\n"
        "[data-testid='stMetricValue'] {"
        "  color: " + _TEXT_PRI + " !important; font-weight: 900 !important; font-size: 18px !important;"
        "}\n"
        "[data-testid='stMetricLabel'] {"
        "  color: " + _TEXT_MUT + " !important; font-size: 9.5px !important;"
        "  font-weight: 700 !important; text-transform: uppercase !important;"
        "}\n"
        ".stDataFrame { border-radius: 14px !important; overflow: hidden !important; border: 1px solid " + _BORDER + " !important; }\n"
        ".stDataFrame th {"
        "  background: " + _BG_RAISED + " !important; color: " + _TEXT_MUT + " !important;"
        "  font-size: 10px !important; font-weight: 700 !important;"
        "  text-transform: uppercase !important; letter-spacing: 0.8px !important;"
        "  border-bottom: 1px solid " + _BORDER + " !important; padding: 10px 14px !important;"
        "}\n"
        ".stDataFrame td {"
        "  background: " + _BG_SURFACE + " !important; color: " + _TEXT_PRI + " !important;"
        "  font-size: 12px !important; padding: 9px 14px !important;"
        "  border-bottom: 1px solid " + _BORDER + " !important;"
        "}\n"
        ".stDataFrame tr:hover td { background: " + _BG_RAISED + " !important; }\n"
        ".stSuccess { background: rgba(16,185,129,0.08) !important; border-left: 3px solid " + _GREEN + " !important; border-radius: 10px !important; }\n"
        ".stError   { background: rgba(239,68,68,0.08)  !important; border-left: 3px solid " + _RED   + " !important; border-radius: 10px !important; }\n"
        ".stWarning { background: rgba(245,158,11,0.08) !important; border-left: 3px solid " + _GOLD  + " !important; border-radius: 10px !important; }\n"
        ".stInfo    { background: rgba(91,140,255,0.08) !important; border-left: 3px solid " + _BRAND_BLUE + " !important; border-radius: 10px !important; }\n"
        "::-webkit-scrollbar { width: 4px; height: 4px; }\n"
        "::-webkit-scrollbar-track { background: transparent; }\n"
        "::-webkit-scrollbar-thumb { background: " + _BG_RAISED + "; border-radius: 20px; }\n"
        "::-webkit-scrollbar-thumb:hover { background: " + _BRAND_BLUE + "; }\n"
        "</style>"
    )
    st.markdown(css, unsafe_allow_html=True)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))

def _safe_int(v, default: int = 0) -> int:
    try:   return int(v) if v is not None else default
    except Exception: return default

def _safe_float(v, default: float = 0.0) -> float:
    try:   return float(v) if v is not None else default
    except Exception: return default

def _norm(v) -> str:
    return str(v or "").strip().lower()

def _parse_json_list(value) -> list:
    if isinstance(value, list): return value
    if not value: return []
    if isinstance(value, str):
        text = value.strip()
        if not text: return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list): return parsed
        except Exception: pass
        return [p.strip() for p in re.split(r"[|,;]\s*", text) if p.strip()]
    return [str(value)]

def _fmt_money(value) -> str:
    try:
        if isinstance(value, str) and _norm(value) in ("", "none", "null"):
            return "N0"
        return f"N{float(value or 0):,.0f}"
    except Exception:
        return "N0"

def _priority_rank(p: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "unscored": 3}.get(_norm(p), 3)

def _status_allowed(status: str) -> bool:
    return _norm(status or "new") not in {
        "deal_closed", "not_interested", "wrong_number",
    }

def _wa_status(lead: dict) -> str:
    raw = _norm(lead.get("wa_status") or "")
    if raw in _VALID_WA: return raw
    if (
        _safe_int(lead.get("wa_verified"))
        or _safe_int(lead.get("whatsapp_verified"))
    ):
        return "verified"
    if _safe_int(lead.get("whatsapp_ready")):
        return "estimated"
    return "unknown"

def _normalize_phone(phone: str, country: str = "") -> str:
    if not phone: return ""
    digits = re.sub(r"[^\d]", "", str(phone).strip())
    if not digits or len(digits) < 6: return ""
    if len(digits) >= 10 and not digits.startswith("0"): return digits
    if digits.startswith("00"): return digits[2:]
    if digits.startswith("0"):
        country_lower = _norm(country)
        dial_code     = COUNTRY_DIAL_CODES.get(country_lower, "")
        if not dial_code:
            for name, code in COUNTRY_DIAL_CODES.items():
                if name in country_lower or country_lower in name:
                    dial_code = code; break
        if dial_code: return dial_code + digits[1:]
    return digits

def _wa_gate(lead: dict) -> dict:
    phone       = str(lead.get("phone") or "").strip()
    country     = str(lead.get("country") or "").strip()
    wa          = _wa_status(lead)
    phone_clean = _normalize_phone(phone, country)
    if not phone_clean:
        return {
            "can_check": False, "show_composer": False,
            "verified": False,  "estimated": False,
            "wa_link": "",      "phone_clean": "",
            "display_phone": "", "reason": "No valid phone number present.",
        }
    verified  = wa in {"verified", "opted_in"}
    estimated = wa in {"estimated", "business_confirmed"}
    return {
        "can_check":     True,
        "show_composer": verified or estimated,
        "verified":      verified,
        "estimated":     estimated,
        "wa_link":       build_wa_prefill_link(
            phone_clean, "Hi, is this the business WhatsApp?"
        ),
        "phone_clean":   phone_clean,
        "display_phone": phone,
        "reason": (
            "WhatsApp verified by agent."          if verified  else
            "WhatsApp estimated from market data." if estimated else
            "Not confirmed as WhatsApp-active."
        ),
    }


# ---------------------------------------------------------
# Section header
# ---------------------------------------------------------

def _section(title: str, subtitle: str = "") -> None:
    sub_html = (
        "<div class='cc-sec-sub'>" + _esc(subtitle) + "</div>"
        if subtitle else ""
    )
    st.markdown(
        "<div class='cc-sec-head'>"
        "<div class='cc-sec-title'>" + _esc(title) + "</div>"
        + sub_html +
        "</div>",
        unsafe_allow_html=True,
    )

def section_header(title: str, subtitle: str = "") -> None:
    _section(title, subtitle)


# ---------------------------------------------------------
# Badge helpers
# ---------------------------------------------------------

def _badge(label: str, color: str, bg: str = "") -> str:
    if not bg: bg = color + "18"
    return (
        "<span class='cc-badge' style='"
        "background:" + bg + ";color:" + color + ";"
        "border:1px solid " + color + "33;'>"
        + _esc(label) + "</span>"
    )

def _priority_badge(priority: str) -> str:
    p = _norm(priority)
    return _badge(
        _PRIORITY_LABEL.get(p, "UNSCORED"),
        _PRIORITY_COLOR.get(p, _TEXT_SEC),
    )

def _wa_badge_html(lead: dict) -> str:
    ws = _wa_status(lead)
    label, color = _WA_BADGE.get(ws, (" WA Unknown", _TEXT_MUT))
    return _badge(label, color)


# ---------------------------------------------------------
# KPI card
# ---------------------------------------------------------

def _kpi_card(icon: str, title: str, value, sub: str, color: str) -> None:
    st.markdown(
        "<div class='cc-kpi'>"
        "<div class='cc-kpi-accent' style='background:" + color + ";'></div>"
        "<div class='cc-kpi-icon'>" + icon + "</div>"
        "<div class='cc-kpi-label'>" + _esc(title) + "</div>"
        "<div class='cc-kpi-value'>" + _esc(str(value)) + "</div>"
        "<div class='cc-kpi-sub' style='color:" + color + ";'>" + _esc(sub) + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------
# Detail block
# ---------------------------------------------------------

def _detail_block(label: str, value: str) -> None:
    st.markdown(
        "<div class='cc-detail'>"
        "<div class='cc-detail-label'>" + _esc(label) + "</div>"
        "<div class='cc-detail-value'>" + _esc(value) + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------
# Data helpers
# ---------------------------------------------------------

def get_pipeline_summary() -> dict:
    if get_connection is None: return {}
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT contact_status, COUNT(*) FROM leads GROUP BY contact_status"
        )
        return {row[0]: row[1] for row in cur.fetchall()}
    except Exception: return {}
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


# ---------------------------------------------------------
# BUG-CC-01: Live stats reader - reads directly from leads table
# ISSUE-B: high_fit threshold corrected to >= 70 (0-100 scale)
# ---------------------------------------------------------

def get_live_stats() -> dict:
    """
    BUG-CC-01: Read KPI values directly from the leads table.
    ISSUE-B: fit_score >= 70 is "high fit" on the 0-100 scale
    that scorer.py outputs. The original >= 8 was wrong.
    """
    if get_connection is None:
        return {}
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM leads")
        total = cur.fetchone()[0] or 0

        # ISSUE-B: corrected threshold - scorer outputs 0-100
        cur.execute("SELECT COUNT(*) FROM leads WHERE fit_score >= 70")
        high_fit = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE wa_verified = 1
               OR whatsapp_verified = 1
               OR wa_status IN ('verified', 'opted_in')
        """)
        wa_verified = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE (
                wa_status IN ('estimated', 'business_confirmed')
                OR whatsapp_ready = 1
            )
            AND COALESCE(wa_verified, 0) = 0
            AND COALESCE(whatsapp_verified, 0) = 0
        """)
        wa_estimated = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE wa_verified = 1
               OR whatsapp_verified = 1
               OR whatsapp_ready = 1
               OR wa_status IN (
                   'verified', 'opted_in',
                   'estimated', 'business_confirmed'
               )
        """)
        wa_ready = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE best_channel = 'ai_call'
              AND enrichment_status IN ('complete', 'enriched')
        """)
        ai_call_ready = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE email IS NOT NULL
              AND TRIM(email) != ''
              AND enrichment_status IN ('complete', 'enriched')
        """)
        email_ready = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE decision_maker_name IS NOT NULL
              AND TRIM(decision_maker_name) != ''
        """)
        with_dm = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM leads
            WHERE fit_score IS NULL
               OR lead_priority IS NULL
               OR lead_priority IN ('', 'unscored')
        """)
        unscored = cur.fetchone()[0] or 0

        try:
            cur.execute("SELECT COALESCE(SUM(amount), 0) FROM revenue")
            revenue = cur.fetchone()[0] or 0
        except Exception:
            revenue = 0

        cur.execute("""
            SELECT COALESCE(country, 'Unknown'), COUNT(*)
            FROM leads GROUP BY country
        """)
        by_country = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("""
            SELECT COALESCE(market, 'Unknown'), COUNT(*)
            FROM leads GROUP BY market
        """)
        by_market = {row[0]: row[1] for row in cur.fetchall()}

        return {
            "total_leads":               total,
            "high_fit_leads":            high_fit,
            "whatsapp_verified":         wa_verified,
            "whatsapp_estimated":        wa_estimated,
            "whatsapp_ready":            wa_ready,
            "ai_call_ready":             ai_call_ready,
            "email_ready":               email_ready,
            "leads_with_decision_maker": with_dm,
            "unscored_leads":            unscored,
            "total_revenue":             revenue,
            "by_country":                by_country,
            "by_market":                 by_market,
        }

    except Exception:
        return {}
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def get_outreach_history(limit: int = 100) -> list:
    if get_connection is None: return []
    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.id AS log_id, o.sent_at AS logged_at, o.channel,
                   o.status, o.message_text,
                   l.id AS lead_id, l.business_name, l.industry,
                   l.city, l.country, l.market, l.phone, l.email,
                   l.fit_score, l.lead_priority, l.best_channel
            FROM outreach_log o
            LEFT JOIN leads l ON o.lead_id = l.id
            ORDER BY o.sent_at DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]
    except Exception: return []
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def get_deals_data() -> pd.DataFrame:
    if get_connection is None: return pd.DataFrame()
    conn = None
    try:
        conn = get_connection()
        return pd.read_sql_query("""
            SELECT r.business_name, r.amount, r.service_type,
                   r.payment_status, r.closed_at,
                   l.city, l.country, l.market, l.fit_score,
                   l.lead_priority, l.best_channel
            FROM revenue r
            LEFT JOIN leads l ON r.lead_id = l.id
            ORDER BY r.closed_at DESC LIMIT 100
        """, conn)
    except Exception: return pd.DataFrame()
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def get_call_queue(limit: int = 200) -> list:
    sources = []
    for fn in [
        lambda: get_whatsapp_verified_leads(limit=100),
        lambda: get_whatsapp_estimated_leads(limit=100),
        lambda: get_ai_call_ready_leads(limit=100),
        lambda: get_call_ready_leads(limit=100),
        lambda: get_top_prospects(limit=100),
        lambda: get_leads_by_priority("high",   limit=100),
        lambda: get_leads_by_priority("medium", limit=100),
        lambda: get_email_ready_leads(limit=100),
        lambda: get_manual_review_leads(limit=50),
    ]:
        try: sources.extend(fn() or [])
        except Exception: pass

    out:  list = []
    seen: set  = set()
    for lead in sources:
        lid = lead.get("id")
        if not lid or lid in seen: continue
        seen.add(lid)
        if not str(lead.get("phone") or "").strip(): continue
        if not _status_allowed(lead.get("contact_status")): continue
        # WA_SENT_FIX_R1: a lead already messaged leaves the queue for good
        if _wa_status(lead) == "sent": continue
        out.append(lead)

    # BUG-029: market -> wa_status -> -fit_score -> -urgency_score -> priority_rank
    out.sort(key=lambda l: (
        MARKET_ORDER.get(_norm(l.get("market")), 99),
        (0 if _wa_status(l) in {"verified", "opted_in"} else
         1 if _wa_status(l) == "estimated" else 2),
        -_safe_float(l.get("fit_score", 0)),
        -_safe_int(l.get("urgency_score", 0)),
        _priority_rank(l.get("lead_priority")),
    ))
    return out[:limit]


def get_ai_prospects(limit: int = 100) -> list:
    sources = []
    for fn in [
        lambda: get_top_prospects(limit=limit),
        lambda: get_whatsapp_verified_leads(limit=limit),
        lambda: get_whatsapp_estimated_leads(limit=limit),
        lambda: get_ai_call_ready_leads(limit=limit),
        lambda: get_email_ready_leads(limit=limit),
        lambda: get_leads_by_priority("high",   limit=limit),
        lambda: get_leads_by_priority("medium", limit=limit),
    ]:
        try: sources.extend(fn() or [])
        except Exception: pass

    out:  list = []
    seen: set  = set()
    for lead in sources:
        lid = lead.get("id")
        if lid and lid not in seen:
            seen.add(lid)
            out.append(lead)
    return out[:limit]


def _get_unscored_lead_ids(
    limit:       int,
    score_scope: str = "all",
    country:     str = "All",
    market:      str = "All",
) -> list:
    """
    ISSUE-G: Enrichment gate added - only 'complete'/'enriched' leads
    enter the scoring queue. Matches BUG-SC-05 requirement.
    """
    if get_connection is None: return []
    conn   = None
    scope  = _norm(score_scope)
    params = []
    # ISSUE-G: enrichment gate - only enriched leads can be scored
    where  = """
        (fit_score IS NULL
         OR lead_priority IS NULL
         OR lead_priority IN ('', 'unscored'))
        AND enrichment_status IN ('complete', 'enriched')
    """
    if scope == "country" and country and country != "All":
        where += " AND LOWER(COALESCE(country,'')) = LOWER(?)"
        params.append(country)
    if scope == "market" and market and market != "All":
        where += " AND LOWER(COALESCE(market,'')) = LOWER(?)"
        params.append(market)
    params.append(limit)
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, business_name FROM leads "
            "WHERE " + where + " ORDER BY id ASC LIMIT ?",
            tuple(params),
        )
        out = []
        for r in cur.fetchall():
            try:    out.append({"id": r["id"],  "name": r["business_name"] or "Unknown"})
            except: out.append({"id": r[0],     "name": r[1] or "Unknown"})
        return out
    except Exception: return []
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


# ---------------------------------------------------------
# Batch scoring
# ISSUE-C: thresholds corrected to 70/50 (0-100 scale)
# ---------------------------------------------------------

def _score_with_progress(
    batch_size:  int,
    score_scope: str = "all",
    country:     str = "All",
    market:      str = "All",
) -> dict:
    if batch_size <= 0: return {"total": 0, "scored": 0}

    lead_ids = _get_unscored_lead_ids(
        batch_size, score_scope=score_scope,
        country=country, market=market,
    )
    total = len(lead_ids)
    if total == 0: return {"total": 0, "scored": 0}

    st.markdown(
        "<div style='font-size:11px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:1px;"
        "color:" + _BRAND_BLUE + ";margin-bottom:8px;'>"
        " Scoring Progress</div>",
        unsafe_allow_html=True,
    )

    progress_bar  = st.progress(0.0)
    status_slot   = st.empty()
    feedback_slot = st.empty()
    scored = high = medium = low = 0

    for i, item in enumerate(lead_ids, start=1):
        lead_id   = item["id"]
        lead_name = item["name"]

        progress_bar.progress(
            max(0.0, min(1.0, float(i - 1) / float(max(total, 1))))
        )
        status_slot.markdown(
            "<div style='color:" + _TEXT_SEC + ";font-size:12px;padding:4px 0;'>"
            " Scoring <b>" + str(i) + "</b> / <b>" + str(total) + "</b> - "
            + _esc(lead_name) + "</div>",
            unsafe_allow_html=True,
        )

        try:
            res = score_and_save_lead(lead_id)
            if res:
                scored += 1
                fit = _safe_float(res.get("fit_score", 0))
                p   = _norm(res.get("lead_priority", ""))
                # ISSUE-C: thresholds on 0-100 scale
                if p == "high"   or fit >= 70:
                    high += 1;   dot = ""; fc = _GREEN
                elif p == "medium" or fit >= 50:
                    medium += 1; dot = ""; fc = _GOLD
                else:
                    low += 1;    dot = ""; fc = _RED

                feedback_slot.markdown(
                    "<div style='background:rgba(255,255,255,0.025);"
                    "border:1px solid " + _BORDER + ";"
                    "border-radius:10px;padding:8px 12px;"
                    "font-size:12px;color:" + _TEXT_SEC + ";'>"
                    + dot + " <b style='color:" + fc + ";'>" + _esc(lead_name) + "</b>"
                    " &ensp; fit: <b>" + f"{fit:.0f}" + "/100</b>"
                    " &middot; priority: <b>" + _esc(p or "unscored") + "</b>"
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                feedback_slot.markdown(
                    "<div style='color:" + _GOLD + ";font-size:12px;padding:4px 0;'>"
                    "! Skipped: " + _esc(lead_name) + "</div>",
                    unsafe_allow_html=True,
                )
        except Exception as exc:
            feedback_slot.markdown(
                "<div style='color:" + _RED + ";font-size:12px;padding:4px 0;'>"
                "! Error: " + _esc(lead_name) + ": " + _esc(str(exc)) + "</div>",
                unsafe_allow_html=True,
            )

    progress_bar.progress(1.0)
    status_slot.markdown(
        "<div style='color:" + _GREEN + ";font-size:13px;font-weight:700;"
        "padding:6px 0;'> Done - " + str(scored) + "/" + str(total) + " scored</div>",
        unsafe_allow_html=True,
    )
    feedback_slot.empty()
    return {
        "total": total, "scored": scored,
        "high": high, "medium": medium, "low": low,
    }


# ---------------------------------------------------------
# Call outcome logger
# BUG-027: dedup via session_state["_logged_outcomes"]
# ISSUE-A: revenue write via save_revenue_record() (BUG-DB-07)
# ---------------------------------------------------------

def _outreach_dedup_key(lead_id, channel: str, status: str) -> tuple:
    return (int(lead_id), str(channel).lower(), str(status).lower())


def log_call_outcome(
    lead: dict,
    status: str,
    notes: str = "",
    deal_value: float = 0,
) -> None:
    """
    BUG-027: Dedup set prevents Streamlit rerun double-writes.
    ISSUE-A: Revenue insert uses save_revenue_record() from database.py
             which enforces UNIQUE(lead_id, service_type) via INSERT OR REPLACE.
    """
    lead_id = lead.get("id")
    if not lead_id:
        return

    channel = _norm(lead.get("best_channel") or "manual")
    if channel not in {"whatsapp", "ai_call", "email", "manual"}:
        channel = "manual"

    # BUG-027: dedup check before any DB writes
    if "_logged_outcomes" not in st.session_state:
        st.session_state["_logged_outcomes"] = set()

    dedup_key = _outreach_dedup_key(lead_id, channel, status)
    if dedup_key in st.session_state["_logged_outcomes"]:
        return

    try:
        update_lead_status(lead_id, status, notes)
    except Exception:
        pass

    is_call_channel = channel in {"ai_call", "manual"}
    if is_call_channel:
        try:
            log_call(lead_id=lead_id, status=status, notes=notes, duration=0)
        except Exception:
            pass

    try:
        result = log_outreach_action(
            lead_id=lead_id, channel=channel,
            message_text=notes, status=status,
        )
        if isinstance(result, tuple) and len(result) == 2:
            ok, err_msg = result
            if not ok and err_msg:
                st.warning("! Outreach log warning: " + _esc(err_msg))
    except Exception as exc:
        st.warning("! Could not write outreach log: " + _esc(str(exc)))

    # Mark deduped before revenue write - revenue failure should not
    # allow the outcome to be re-logged
    st.session_state["_logged_outcomes"].add(dedup_key)

    # ISSUE-A: use save_revenue_record() - enforces UNIQUE(lead_id, service_type)
    if status == "deal_closed" and deal_value and deal_value > 0:
        try:
            save_revenue_record(
                lead_id=lead_id,
                amount=deal_value,
                service_type="Website Design",
                business_name=lead.get("business_name", "Unknown"),
                payment_status="confirmed",
            )
        except Exception as exc:
            st.error("Error logging deal: " + _esc(str(exc)))


# ---------------------------------------------------------
# Call script fallback
# BUG-028: sender_name from _get_sender_name() - not business name
# ---------------------------------------------------------

def _script_fallback(lead: dict) -> str:
    name        = lead.get("business_name", "your business")
    industry    = lead.get("industry", "business")
    city        = lead.get("city", "your area")
    opener      = lead.get("recommended_opener") or ""
    country     = lead.get("country", "")
    # BUG-028: sender_name from session state, never from lead
    sender_name = _get_sender_name()
    return (
        f"{opener or f'Hi, is that the owner of {name}?'}\n\n"
        f"My name is {sender_name} - I help {industry} businesses "
        f"in {city}{', ' + country if country else ''} get more "
        f"customers through a professional website.\n\n"
        f"I noticed {name} might benefit from:\n"
        f"  -> Professional website design\n"
        f"  -> Google presence and local SEO\n"
        f"  -> WhatsApp enquiry button\n"
        f"  -> Mobile-friendly layout\n\n"
        f"Could I take 2 minutes to share what I found?\n\n"
        f"[INTERESTED] Great - I can show you a quick concept and pricing.\n"
        f"[BUSY] No problem. When is a better time to call?\n"
        f"[NO WEBSITE] I noticed you don't have a website yet - "
        f"that's where we can help fastest."
    )


# ---------------------------------------------------------
# Hero
# ---------------------------------------------------------

def render_hero() -> None:
    now = datetime.now()
    st.markdown(
        "<div class='cc-hero'>"
        "<div class='cc-hero-eyebrow'>"
        "Sales Call Center &middot; KNCS LeadGen Pro v10.1"
        "</div>"
        "<div class='cc-hero-title'>Work your highest-fit prospects first</div>"
        "<div class='cc-hero-sub'>"
        "Ranked by AI fit, urgency, reachability, country/market "
        "and best outreach channel &middot; "
        + _esc(now.strftime("%d %b %Y, %H:%M")) +
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------
# KPI rows
# ---------------------------------------------------------

def render_pipeline_kpis() -> None:
    pipeline = get_pipeline_summary()
    stats    = get_live_stats()

    _section("Pipeline Overview", "Live outreach and deal status.")
    cards = [
        ("", "Total Leads",  _safe_int(stats.get("total_leads", 0)),        "In database",     _BRAND_BLUE),
        ("", "WA Verified",  _safe_int(stats.get("whatsapp_verified", 0)),  "Agent confirmed", _WA_GREEN),
        ("", "WA Estimated", _safe_int(stats.get("whatsapp_estimated", 0)), "Heuristic",       _GOLD),
        ("", "Interested",   _safe_int(pipeline.get("interested", 0)),       "Warm prospects",  _GREEN),
        ("", "Called",       _safe_int(pipeline.get("called", 0)),           "Outreach done",   _BRAND_PUR),
        ("", "Closed",       _safe_int(pipeline.get("deal_closed", 0)),      "Deals won",       _RED),
    ]
    cols = st.columns(6)
    for i, (icon, title, value, sub, color) in enumerate(cards):
        with cols[i]:
            _kpi_card(icon, title, value, sub, color)

    revenue = _fmt_money(stats.get("total_revenue", 0))
    st.markdown(
        "<div class='cc-revenue' style='margin-top:10px;'>"
        "<div class='cc-revenue-label'> Revenue</div>"
        "<div class='cc-revenue-value'>" + revenue + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_intelligence_kpis() -> None:
    stats = get_live_stats()
    _section("Channel Intelligence", "AI scoring and outreach readiness.")
    cards = [
        ("", "High Fit",        _safe_int(stats.get("high_fit_leads", 0)),             "Best prospects",  _GREEN),
        ("", "AI Call Ready",   _safe_int(stats.get("ai_call_ready", 0)),              "Voice queue",     _BRAND_PUR),
        ("", "WhatsApp Ready",  _safe_int(stats.get("whatsapp_ready", 0)),             "WA queue",        _WA_GREEN),
        ("", "Email Ready",     _safe_int(stats.get("email_ready", 0)),                "Email queue",     _BRAND_BLUE),
        ("", "Decision Makers", _safe_int(stats.get("leads_with_decision_maker", 0)),  "Named contacts",  _RED),
        ("", "Unscored",        _safe_int(stats.get("unscored_leads", 0)),             "Need scoring",    _TEXT_MUT),
    ]
    cols = st.columns(6)
    for i, (icon, title, value, sub, color) in enumerate(cards):
        with cols[i]:
            _kpi_card(icon, title, value, sub, color)


# ---------------------------------------------------------
# Scoring banner
# ---------------------------------------------------------

def render_scoring_banner(
    queue_country: str = "All",
    queue_market:  str = "All",
) -> None:
    stats    = get_live_stats()
    unscored = _safe_int(stats.get("unscored_leads", 0))
    if unscored <= 0: return

    st.markdown(
        "<div class='cc-scoring-banner'>"
        "<div style='font-size:16px;font-weight:800;"
        "color:" + _TEXT_PRI + ";margin-bottom:6px;'> Scoring Backlog</div>"
        "<div style='font-size:13px;color:" + _TEXT_SEC + ";'>"
        "<b style='color:" + _TEXT_PRI + ";'>" + f"{unscored:,}" + "</b>"
        " leads waiting to be scored."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([1, 1, 1.5])
    with c1:
        batch_size = st.selectbox(
            "Batch size", [5, 10, 20, 50, 100], index=1,
            key="cc_score_batch",
        )
    with c2:
        scope_label = st.selectbox(
            "Scope",
            ["All unscored", "Selected country", "Selected market"],
            index=0, key="cc_score_scope",
        )
    with c3:
        if scope_label == "Selected country":
            st.caption(f"Scoring country: **{queue_country}**")
        elif scope_label == "Selected market":
            st.caption(f"Scoring market: **{queue_market}**")
        else:
            st.caption("Scope: **All unscored leads**")

    score_scope = (
        "country" if scope_label == "Selected country"
        else "market" if scope_label == "Selected market"
        else "all"
    )
    actual = min(batch_size, unscored)
    btn    = f" Score Next {actual} Leads"
    if score_scope == "country" and queue_country != "All":
        btn += f" - {queue_country}"
    elif score_scope == "market" and queue_market != "All":
        btn += f" - {queue_market}"

    if st.button(
        btn, type="primary",
        use_container_width=True, key="score_cc_btn",
    ):
        with st.spinner("Scoring..."):
            res = _score_with_progress(
                actual, score_scope=score_scope,
                country=queue_country, market=queue_market,
            )
        if res.get("total", 0) == 0:
            st.warning("No unscored leads matched this scope.")
        else:
            st.success(
                f" {res.get('scored',0)}/{res.get('total',0)} scored - "
                f"High: {res.get('high',0)} - "
                f"Medium: {res.get('medium',0)} - "
                f"Low: {res.get('low',0)}"
            )
        st.rerun()


# ---------------------------------------------------------
# Current lead detail
# ISSUE-D: fit_score thresholds and label corrected (0-100 scale)
# ---------------------------------------------------------


def _generate_queue_vcf(queue: list) -> str:
    cards = []
    nl = chr(10)
    for l in queue:
        raw_name = l.get('business_name') or 'Lead'
        clean_name = re.split(r'\s*(?:\|\||\||\s-\s|::)\s*', str(raw_name).strip())[0].strip()
        phone = str(l.get('phone') or '').strip()
        if not phone:
            continue
        ind = str(l.get('industry') or '')
        city = str(l.get('city') or '')
        lines = [
            'BEGIN:VCARD',
            'VERSION:3.0',
            f'FN:{clean_name}',
            f'ORG:{clean_name}',
            f'TEL;TYPE=CELL:{phone}',
            f'NOTE:KNCS {ind} in {city}',
            'END:VCARD'
        ]
        cards.append(nl.join(lines))
    return (nl + nl).join(cards)


def render_current_lead(lead: dict) -> None:
    if not lead:
        st.markdown(
            "<div style='text-align:center;padding:40px 24px;"
            "background:rgba(255,255,255,0.02);"
            "border:1px dashed " + _BORDER_MID + ";"
            "border-radius:16px;'>"
            "<div style='font-size:32px;margin-bottom:12px;'></div>"
            "<div style='font-size:15px;font-weight:700;"
            "color:" + _TEXT_SEC + ";margin-bottom:6px;'>Queue Empty</div>"
            "<div style='font-size:13px;color:" + _TEXT_MUT + ";'>"
            "Score your leads first to populate the queue."
            "</div></div>",
            unsafe_allow_html=True,
        )
        return

    name     = lead.get("business_name") or "Unknown"
    industry = lead.get("industry")      or "-"
    city     = lead.get("city")          or ""
    country  = lead.get("country")       or ""
    phone    = lead.get("phone")         or "-"
    email    = lead.get("email")         or "-"
    website  = lead.get("website")       or "-"
    source   = lead.get("source")        or "unknown"
    market   = lead.get("market")        or ""
    dm       = lead.get("decision_maker_name") or "Not found"
    opener   = lead.get("recommended_opener") or _script_fallback(lead)
    reason   = lead.get("fit_reason")    or "No analysis yet."
    pains    = _parse_json_list(lead.get("pain_points"))
    routing  = lead.get("routing_reason") or "-"
    priority = _norm(lead.get("lead_priority") or "unscored")
    channel  = lead.get("best_channel")  or "manual"

    fit_score    = _safe_float(lead.get("fit_score",         0))
    urgency      = _safe_int(lead.get("urgency_score",       0))
    reachability = _safe_int(lead.get("reachability_score",  0))
    opp_score    = _safe_int(lead.get("opportunity_score",   0))
    audit_score  = _safe_int(lead.get("website_audit_score", 0))

    wa  = _wa_status(lead)
    fit = get_fit_label(fit_score)
    ch  = get_channel_label(channel)
    comp             = get_contact_completeness(lead)
    completeness_pct = _safe_float(comp.get("percentage", 0))

    # ISSUE-D: thresholds on 0-100 scale
    fit_color = (
        _GREEN if fit_score >= 70 else
        _GOLD  if fit_score >= 50 else _RED
    )
    wa_label, wa_color = _WA_BADGE.get(wa, (" WA Unknown", _TEXT_MUT))
    loc = " - ".join(p for p in [city, country] if p)

    # ISSUE-D: display fit_score on 0-100 scale with correct label
    fit_display = f"{fit_score:.0f}"
    fit_label   = "FIT / 100"

    st.markdown(
        "<div class='cc-lead-header'>"
        "<div style='display:flex;justify-content:space-between;"
        "align-items:flex-start;margin-bottom:14px;'>"
        "<div style='flex:1;'>"
        "<div class='cc-lead-name'>" + _esc(name) + "</div>"
        "<div class='cc-lead-meta'>"
        + _esc(industry)
        + ("&ensp;&middot;&ensp;" + _esc(loc) if loc else "")
        + ("&ensp;&middot;&ensp;" + _esc(market) if market else "")
        + "</div></div>"
        "<div class='cc-fit-block'>"
        "<div class='cc-fit-num' style='color:" + fit_color + ";'>"
        + fit_display + "</div>"
        "<div class='cc-fit-label'>" + fit_label + "</div>"
        "<div class='cc-opp-num'>" + str(opp_score) + "</div>"
        "<div class='cc-opp-label'>OPP</div>"
        "</div></div>"
        "<div class='cc-lead-badges'>"
        + _badge(_esc(source.replace("_", " ").title()), _TEXT_MUT)
        + _badge(fit["label"], fit["color"], fit["bg"])
        + _badge(ch["label"],  ch["color"],  ch["bg"])
        + _priority_badge(priority)
        + _badge(wa_label, wa_color)
        + "</div></div>",
        unsafe_allow_html=True,
    )

    audit_color = (
        _RED  if audit_score <= 2 else
        _GOLD if audit_score <= 5 else _GREEN
    )
    st.markdown(
        "<div class='cc-score-grid'>"
        "<div class='cc-score-cell'>"
        "<div class='cc-score-num' style='color:" + fit_color + ";'>"
        + f"{fit_score:.0f}"
        + "<span style='font-size:12px;color:" + _TEXT_MUT + ";'>/100</span>"
        + "</div><div class='cc-score-lbl'>Fit</div></div>"
        "<div class='cc-score-cell'>"
        "<div class='cc-score-num'>" + str(urgency) + "</div>"
        "<div class='cc-score-lbl'>Urgency</div></div>"
        "<div class='cc-score-cell'>"
        "<div class='cc-score-num'>" + str(reachability) + "</div>"
        "<div class='cc-score-lbl'>Reach</div></div>"
        "<div class='cc-score-cell'>"
        "<div class='cc-score-num'>" + f"{completeness_pct:.0f}%" + "</div>"
        "<div class='cc-score-lbl'>Contact</div></div>"
        "<div class='cc-score-cell' style='border-top:3px solid " + audit_color + ";'>"
        "<div class='cc-score-num' style='color:" + audit_color + ";'>"
        + str(audit_score)
        + "<span style='font-size:12px;color:" + _TEXT_MUT + ";'>/10</span>"
        + "</div><div class='cc-score-lbl'>Web Audit</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if wa in {"verified", "opted_in"}:
        st.markdown(
            "<div class='cc-wa-verified'> WhatsApp verified for this lead</div>",
            unsafe_allow_html=True,
        )
    elif wa in {"estimated", "business_confirmed"}:
        st.markdown(
            "<div class='cc-wa-estimated'> WhatsApp estimated - confirm before sending</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='cc-wa-unknown'> WhatsApp not confirmed - verify on the right panel</div>",
            unsafe_allow_html=True,
        )

    d1, d2, d3 = st.columns(3)
    with d1: _detail_block(" Phone",          phone)
    with d2: _detail_block(" Email",          email)
    with d3: _detail_block(" Decision Maker", dm)

    _detail_block(" Website",  website)
    _detail_block(" Location", f"{city} {country}".strip() or "-")
    _detail_block(" Routing",  routing)

    with st.expander(" Why This Lead", expanded=True):
        st.markdown(
            "<div style='color:" + _TEXT_SEC + ";font-size:13px;"
            "line-height:1.7;'>" + _esc(reason) + "</div>",
            unsafe_allow_html=True,
        )

    with st.expander(" Recommended Opener", expanded=True):
        st.markdown(
            "<div style='color:" + _TEXT_SEC + ";font-size:13px;"
            "line-height:1.7;font-style:italic;'>"
            "&ldquo;" + _esc(opener) + "&rdquo;</div>",
            unsafe_allow_html=True,
        )

    if pains:
        with st.expander(" Pain Points", expanded=False):
            for p in pains[:6]:
                st.markdown(
                    "<div style='color:#fca5a5;font-size:12px;"
                    "padding:3px 0;'>- " + _esc(str(p)) + "</div>",
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------
# Call script panel
# ---------------------------------------------------------

def render_call_script(lead: dict) -> None:
    _section("Call Script", "Use opener first, then follow the flow.")
    script = lead.get("recommended_opener") or _script_fallback(lead)
    st.text_area(
        "Script (editable)",
        value=script,
        height=220,
        key=f"script_area_{lead.get('id', 'x')}",
        label_visibility="collapsed",
    )


# ---------------------------------------------------------
# WhatsApp panels
# BUG-028: sender name always from _get_sender_name()
# ---------------------------------------------------------

def render_whatsapp_verify_button(lead: dict, key_suffix: str = "") -> None:
    lead_id = lead.get("id")
    if not str(lead.get("phone") or "").strip() or not lead_id:
        return

    ws = _wa_status(lead)
    if ws in {"verified", "opted_in"} or _safe_int(lead.get("whatsapp_verified")):
        st.success(" WhatsApp verified")
        if st.button(
            " Remove Verification",
            key=f"wa_unverify_{lead_id}{key_suffix}",
            use_container_width=True,
        ):
            try:
                verify_whatsapp_manually(lead_id, verified=False)
                st.rerun()
            except Exception as exc:
                st.error("Could not remove: " + _esc(str(exc)))
    else:
        st.caption("Confirm this number is on WhatsApp first.")
        if st.button(
            " Mark as WhatsApp Verified",
            key=f"wa_verify_{lead_id}{key_suffix}",
            use_container_width=True,
        ):
            try:
                verify_whatsapp_manually(lead_id, verified=True)
                st.rerun()
            except Exception as exc:
                st.error("Could not save: " + _esc(str(exc)))


def render_whatsapp_manual_tools(
    lead: dict, wa_gate: dict, key_suffix: str = ""
) -> None:
    if not wa_gate.get("phone_clean"): return
    _section("WA Verification", "Confirm before sending.")
    c1, c2 = st.columns(2)
    with c1:
        wa_link = wa_gate.get("wa_link", "")
        if wa_link:
            st.markdown(
                '<a href="' + wa_link + '" target="_blank" class="cc-wa-btn">'
                ' Open WhatsApp Check</a>',
                unsafe_allow_html=True,
            )
    with c2:
        render_whatsapp_verify_button(lead, key_suffix=key_suffix)


def render_whatsapp_composer(lead: dict, wa_gate: dict) -> None:
    """
    BUG-028: sender_name from _get_sender_name() - never from lead fields.
    The greeting recipient in the message is the decision maker or business,
    not the sender.
    """
    lead_id     = lead.get("id", "x")
    phone_clean = wa_gate.get("phone_clean", "")
    verified    = wa_gate.get("verified", False)
    badge       = " Verified" if verified else "! Estimated"

    _section("WhatsApp Composer", f"Tone-matched message - {badge}")

    tone = st.selectbox(
        "Tone", ["friendly", "formal", "direct"], index=0,
        key=f"wa_tone_{lead_id}",
    )

    # BUG-028: sender_name from session state - never lead's business name
    sender_name = _get_sender_name()
    generated = build_whatsapp_message(
        lead, tone=tone,
        sender_name=sender_name,
        company_name=COMPANY_NAME,
        cta_link=CTA_LINK,
    )
    final_msg = st.text_area(
        "Message (edit if needed)",
        value=generated, height=200,
        key=f"wa_msg_{lead_id}",
    )
    wa_link = (
        build_wa_prefill_link(phone_clean, final_msg.strip())
        if phone_clean and final_msg.strip()
        else wa_gate.get("wa_link", "")
    )
    if wa_link:
        st.markdown(
            '<a href="' + wa_link + '" target="_blank" class="cc-wa-btn">'
            ' Send via WhatsApp ' + _esc(badge) + '</a>',
            unsafe_allow_html=True,
        )
        # WA_SENT_FIX_R1: a link cannot record anything - this button can
        st.caption("1) Open WhatsApp above and press send.  2) Then record it here so the "
                   "queue advances and WA SENT moves off zero.")
        render_wa_sent_button(lead, final_msg)
        st.markdown(
            "<div style='font-size:11px;color:" + _TEXT_MUT + ";margin-top:4px;'>"
            "From: " + _esc(sender_name) + " &middot; "
            "Tone: " + _esc(tone) + " &middot; "
            "Chars: " + str(len(final_msg)) + "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.warning("No valid phone to open WhatsApp.")


# ---------------------------------------------------------
# Queue panel
# ---------------------------------------------------------


# ---------------------------------------------------------
# WA_SENT_FIX_R1: record an actual WhatsApp send
#
# The dashboard counts leads.wa_status = 'sent'. Before this patch nothing wrote
# that value, so WA SENT was 0 no matter how many messages went out. The button
# below writes it, then moves the queue.
# ---------------------------------------------------------

def record_wa_sent(lead: dict, message: str = "") -> bool:
    """Write wa_status='sent' + contact_status='contacted'. True if it stuck."""
    lead_id = lead.get("id")
    if not lead_id:
        return False
    try:
        update_lead_status(int(lead_id), "contacted", "WhatsApp message sent")
    except Exception:
        return False
    ok = False
    try:
        if get_connection is not None:
            conn = get_connection()
            try:
                cur = conn.execute(
                    "UPDATE leads SET wa_status = 'sent' WHERE id = ?", (int(lead_id),)
                )
                conn.commit()
                ok = bool(cur.rowcount)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return False
    try:
        log_outreach_action(
            lead_id=int(lead_id), channel="whatsapp",
            message_text=(message or "")[:2000], status="sent",
        )
    except Exception:
        pass
    return ok


def render_wa_sent_button(lead: dict, message: str, key_suffix: str = "") -> None:
    """Mark this lead as sent and advance to the next one in the queue."""
    if st.button(
        "\u2705 I sent this message \u2014 mark sent & next",
        type="primary", use_container_width=True,
        key="wasent_" + str(lead.get("id", key_suffix)),
    ):
        if record_wa_sent(lead, message):
            st.success("Marked sent. Loading the next lead\u2026")
        else:
            st.warning("Could not record the send \u2014 check the database connection.")
        st.session_state["call_idx"] = st.session_state.get("call_idx", 0) + 1
        st.rerun()


def render_queue_panel(queue: list, idx: int) -> None:
    total = max(len(queue), 1)

    st.markdown(
        "<div class='cc-queue-banner'><div>"
        "<div class='cc-queue-pos'>Lead " + str(idx + 1) + " of " + str(len(queue)) + "</div>"
        "<div class='cc-queue-total'>" + str(len(queue) - idx - 1) + " remaining in queue</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    st.progress(max(0.0, min(1.0, float(idx + 1) / float(total))))

    c1, c2 = st.columns(2)
    with c1:
        new_idx = st.number_input(
            "Jump to #", min_value=1, max_value=total,
            value=idx + 1, step=1, key="queue_jump",
        )
        if new_idx - 1 != idx:
            st.session_state["call_idx"] = new_idx - 1
            st.rerun()
    with c2:
        st.metric("Position", f"{idx+1} / {len(queue)}")


# ---------------------------------------------------------
# Outcome logger
# ISSUE-J: deal_value uses _safe_float()
# ---------------------------------------------------------

def render_outcome_logger(lead: dict, idx: int, queue: list) -> None:
    _section("Log Outcome", "Record what happened on this call.")

    st.markdown("<div class='cc-outcome-panel'>", unsafe_allow_html=True)

    outcome = st.radio(
        "Outcome",
        options=list(_STATUS_MAP.keys()),
        label_visibility="collapsed",
        key=f"outcome_{lead.get('id', idx)}",
    )
    notes = st.text_area(
        "Notes",
        placeholder="Objections? Next steps? What did they say?",
        height=90,
        key=f"notes_{lead.get('id', idx)}",
    )

    # ISSUE-J: _safe_float() prevents TypeError on None return from number_input
    deal_value = 0.0
    if "Deal Closed" in outcome:
        raw_val    = st.number_input(
            "Deal Value (N)", min_value=0,
            value=300000, step=10000,
            key=f"deal_{lead.get('id', idx)}",
        )
        deal_value = _safe_float(raw_val)

    st.markdown("</div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(
            " Save & Next", type="primary",
            use_container_width=True,
            key=f"save_{lead.get('id', idx)}",
        ):
            log_call_outcome(
                lead=lead,
                status=_STATUS_MAP.get(outcome, "called"),
                notes=notes, deal_value=deal_value,
            )
            st.session_state["call_idx"] = idx + 1
            st.success(" Saved.")
            st.rerun()
    with c2:
        if st.button(
            " Skip", use_container_width=True,
            key=f"skip_{lead.get('id', idx)}",
        ):
            st.session_state["call_idx"] = idx + 1
            st.rerun()
    with c3:
        if st.button(
            " Back", use_container_width=True,
            key=f"prev_{lead.get('id', idx)}",
        ):
            st.session_state["call_idx"] = max(0, idx - 1)
            st.rerun()


# ---------------------------------------------------------
# Tab content
# ---------------------------------------------------------

def render_prospect_table() -> None:
    _section("AI Prospect Queue", "Leads ranked by AI fit for calling first.")
    prospects = get_ai_prospects(limit=100)
    if not prospects:
        st.info("No scored prospects yet.")
        return
    st.dataframe(
        pd.DataFrame(prospects),
        use_container_width=True, hide_index=True, height=480,
    )


def render_call_history() -> None:
    _section("Call History", "All outreach activity logged.")
    rows = get_outreach_history(limit=100)
    if not rows:
        st.info("No calls logged yet.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True, hide_index=True, height=400,
    )


def render_deals_panel() -> None:
    _section("Closed Deals", "Revenue from converted opportunities.")
    df = get_deals_data()
    if df.empty:
        st.info("No deals closed yet.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True, height=400)


# ---------------------------------------------------------
# Main render
# ---------------------------------------------------------

def render_call_center() -> None:
    _render_page_css()
    render_hero()

    stats      = get_live_stats()
    by_country = stats.get("by_country") or {}
    by_market  = stats.get("by_market")  or {}
    if not isinstance(by_country, dict): by_country = {}
    if not isinstance(by_market,  dict): by_market  = {}

    country_opts = ["All"] + sorted(
        {str(k).strip() for k in by_country if str(k).strip()}
    )
    market_opts = ["All"] + sorted(
        {str(k).strip() for k in by_market if str(k).strip()}
    )

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        queue_country = st.selectbox(
            " Country", country_opts, index=0, key="queue_country",
        )
    with f2:
        queue_market = st.selectbox(
            " Market", market_opts, index=0, key="queue_market",
        )
    with f3:
        queue_channel = st.selectbox(
            " Channel",
            ["All", "whatsapp", "ai_call", "email", "manual"],
            index=0, key="queue_channel",
        )
    with f4:
        queue_wa = st.selectbox(
            " WA Status",
            ["All", "verified", "estimated",
             "business_confirmed", "opted_in", "unknown"],
            index=0, key="queue_wa",
        )

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    render_pipeline_kpis()
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    render_intelligence_kpis()
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    render_scoring_banner(
        queue_country=queue_country,
        queue_market=queue_market,
    )

    tabs = st.tabs([
        "  Call Queue",
        "  AI Prospects",
        "  Call History",
        "  Closed Deals",
    ])

    with tabs[0]:
        queue = [
            lead for lead in get_call_queue()
            if _norm(lead.get("contact_status", "")) not in
               {"deal_closed", "not_interested", "wrong_number"}
            and (queue_country == "All"
                 or _norm(lead.get("country")) == _norm(queue_country))
            and (queue_market  == "All"
                 or _norm(lead.get("market"))  == _norm(queue_market))
            and (queue_channel == "All"
                 or _norm(lead.get("best_channel")) == _norm(queue_channel))
            and (queue_wa == "All"
                 or _wa_status(lead) == _norm(queue_wa))
        ]

        if not queue:
            st.markdown(
                "<div style='text-align:center;padding:48px 24px;"
                "background:rgba(255,255,255,0.02);"
                "border:1px dashed " + _BORDER_MID + ";"
                "border-radius:18px;margin-top:20px;'>"
                "<div style='font-size:36px;margin-bottom:14px;'></div>"
                "<div style='font-size:16px;font-weight:700;"
                "color:" + _TEXT_SEC + ";margin-bottom:6px;'>Queue empty</div>"
                "<div style='font-size:13px;color:" + _TEXT_MUT + ";'>"
                "No call-ready prospects match your filters. "
                "Score your leads or adjust the filters above."
                "</div></div>",
                unsafe_allow_html=True,
            )
        else:
            if "call_idx" not in st.session_state:
                st.session_state["call_idx"] = 0

            # Batch Actions Bar
            unverified_in_queue = [
                lead.get("id") for lead in queue
                if lead.get("id")
                and not _safe_int(lead.get("wa_verified"))
                and not _safe_int(lead.get("whatsapp_verified"))
                and _wa_status(lead) != "verified"
            ]

            b1, b2, b3 = st.columns([2.5, 1.3, 1.2])
            with b1:
                if unverified_in_queue:
                    st.info(f" **{len(unverified_in_queue)} leads** in this queue are unverified. Bulk-verify them in 1 click instead of 1-by-1.")
                else:
                    st.success(f" All {len(queue)} leads in this queue are WhatsApp Verified!")
            with b2:
                if unverified_in_queue and st.button(f" Bulk Verify {len(unverified_in_queue)} Leads", key="bulk_wa_verify_btn", use_container_width=True):
                    try:
                        n = batch_verify_whatsapp(unverified_in_queue, verified=True)
                        st.success(f"Successfully verified {n} leads!")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error: {err}")
            with b3:
                vcf_data = _generate_queue_vcf(queue)
                st.download_button(
                    " Export VCF",
                    data=vcf_data,
                    file_name="kncs_whatsapp_leads.vcf",
                    mime="text/vcard",
                    use_container_width=True,
                    help="Import to Google Contacts to sync all numbers on your phone in 1 minute."
                )

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            idx  = min(st.session_state["call_idx"], len(queue) - 1)
            lead = queue[idx]
            gate = _wa_gate(lead)

            left, right = st.columns([1.5, 1], gap="large")

            with left:
                _section(
                    f"Lead {idx+1} of {len(queue)}",
                    "Current outreach target",
                )
                try:
                    render_current_lead(lead)
                except Exception as exc:
                    st.error("Lead render error: " + _esc(str(exc)))

                st.markdown(
                    "<div style='height:12px'></div>",
                    unsafe_allow_html=True,
                )
                render_call_script(lead)

            with right:
                if gate["can_check"]:
                    render_whatsapp_manual_tools(
                        lead, gate, key_suffix="_right",
                    )
                if gate["show_composer"]:
                    render_whatsapp_composer(lead, gate)

                raw_phone  = str(lead.get("phone") or "").strip()
                call_ready = _safe_int(lead.get("call_ready", 0))
                if raw_phone and call_ready:
                    clean = _normalize_phone(
                        raw_phone, str(lead.get("country") or "")
                    )
                    if clean:
                        st.markdown(
                            '<a href="tel:+' + clean + '" class="cc-call-btn">'
                            ' Call: ' + _esc(raw_phone) + '</a>',
                            unsafe_allow_html=True,
                        )

                st.markdown(
                    "<div style='height:1px;background:" + _BORDER + ";"
                    "margin:14px 0;'></div>",
                    unsafe_allow_html=True,
                )
                render_queue_panel(queue, idx)
                st.markdown(
                    "<div style='height:1px;background:" + _BORDER + ";"
                    "margin:14px 0;'></div>",
                    unsafe_allow_html=True,
                )
                render_outcome_logger(lead, idx, queue)

    with tabs[1]:
        render_prospect_table()

    with tabs[2]:
        render_call_history()

    with tabs[3]:
        render_deals_panel()


def render() -> None:
    """Entry point called by app.py page router."""
    render_call_center()
