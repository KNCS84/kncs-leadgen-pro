# pages/dashboard.py
# KNCS LeadGen Pro — v10.1
# Fixes:
#   BUG-DB-01 — fit_score colour thresholds updated to 0-100 scale
#   BUG-DB-02 — score progress colours + "/10" label fixed to "/100"
#   BUG-DB-03 — revenue + live_clock cards suppress click strip

from __future__ import annotations

from datetime import datetime
from textwrap import dedent
import html
import json
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────
# Safe imports (DB)
# ─────────────────────────────────────────────────────────

try:
    from database import get_dashboard_stats
except Exception:
    def get_dashboard_stats() -> dict:
        return {}

try:
    from database import get_connection
except Exception:
    get_connection = None

try:
    from database import apply_routing_update
except Exception:
    def apply_routing_update(*a, **kw) -> bool:
        return False

try:
    from database import get_top_prospects
except Exception:
    def get_top_prospects(limit=8) -> list:
        return []

try:
    from database import get_whatsapp_ready_leads
except Exception:
    def get_whatsapp_ready_leads(limit=8) -> list:
        return []

try:
    from database import get_whatsapp_verified_leads
except Exception:
    def get_whatsapp_verified_leads(limit=8) -> list:
        return []

try:
    from database import get_whatsapp_estimated_leads
except Exception:
    def get_whatsapp_estimated_leads(limit=8) -> list:
        return []

try:
    from database import get_ai_call_ready_leads
except Exception:
    def get_ai_call_ready_leads(limit=8) -> list:
        return []

try:
    from database import get_email_ready_leads
except Exception:
    def get_email_ready_leads(limit=8) -> list:
        return []

try:
    from database import get_leads_by_priority
except Exception:
    def get_leads_by_priority(priority, limit=8) -> list:
        return []

try:
    from database import get_unrouted_leads
except Exception:
    def get_unrouted_leads(limit=200) -> list:
        return []

try:
    from database import get_leads_by_country
except Exception:
    def get_leads_by_country(country, limit=100) -> list:
        return []

try:
    from database import get_leads_by_market
except Exception:
    def get_leads_by_market(market, limit=100) -> list:
        return []

try:
    from database import get_manual_review_leads
except Exception:
    def get_manual_review_leads(limit=50) -> list:
        return []

try:
    _test = get_dashboard_stats()
    _DB_AVAILABLE = isinstance(_test, dict)
except Exception:
    _DB_AVAILABLE = False

# ─────────────────────────────────────────────────────────
# Safe imports (Scorer)
# ─────────────────────────────────────────────────────────
try:
    from engine.scorer import score_and_save_lead, score_unscored_leads_batch
    _SCORER_AVAILABLE = True
except Exception:
    _SCORER_AVAILABLE = False
    def score_and_save_lead(lead_id: int) -> Optional[dict]: return None
    def score_unscored_leads_batch(limit=50, use_ai=True) -> dict:
        return {"total": 0, "scored": 0, "high": 0, "medium": 0, "low": 0}

# ─────────────────────────────────────────────────────────
# Safe imports (Router)
# ─────────────────────────────────────────────────────────
try:
    from engine.router import assign_channel as _router_assign_channel
    _ROUTER_AVAILABLE = True
except Exception:
    _ROUTER_AVAILABLE = False
    def _router_assign_channel(lead: dict) -> dict: return {}

# ─────────────────────────────────────────────────────────
# Safe imports (Jobs)
# ─────────────────────────────────────────────────────────
try:
    from engine.job_manager import (
        get_recent_jobs,
        get_running_job,
        get_job_status_summary,
    )
except Exception:
    def get_recent_jobs(limit=5) -> list: return []
    def get_running_job() -> dict:        return {}
    def get_job_status_summary(job_id) -> dict:
        return {
            "found": False, "status": "unknown",
            "progress": 0,  "leads": 0,
            "message": "Job unavailable",
            "is_done": False, "is_running": False,
        }

# ─────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────
_BG_SURFACE  = "#0b1628"
_BG_RAISED   = "#101f38"
_BRAND_BLUE  = "#5b8cff"
_BRAND_PUR   = "#7c5cff"
_GREEN       = "#10b981"
_GOLD        = "#f59e0b"
_RED         = "#ef4444"
_TEXT_PRI    = "#f1f5f9"
_TEXT_SEC    = "#94a3b8"
_TEXT_MUT    = "#475569"
_BORDER      = "rgba(255,255,255,0.05)"
_BORDER_MID  = "rgba(255,255,255,0.08)"
_BORDER_BRD  = "rgba(91,140,255,0.28)"

# ─────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────
def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _safe_call(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _truncate_text(value, max_len: int = 120) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _parse_json_list(value) -> list:
    if isinstance(value, list):  return value
    if value is None:            return []
    if isinstance(value, str):
        text = value.strip()
        if not text: return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list): return parsed
        except Exception:
            pass
        return [p.strip() for p in text.split(",") if p.strip()]
    return [str(value)]


def _fmt_money(value) -> str:
    try:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("", "none", "null", "n/a"): return "&#8358;0"
        v = float(value or 0)
        if v == int(v):
            return "&#8358;" + "{:,}".format(int(v))
        return "&#8358;" + "{:,.2f}".format(v)
    except (ValueError, TypeError):
        return "&#8358;0"


def _fmt_money_plain(value) -> str:
    try:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("", "none", "null", "n/a"): return "₦0"
        v = float(value or 0)
        return "₦{:,}".format(int(v)) if v == int(v) else "₦{:,.2f}".format(v)
    except (ValueError, TypeError):
        return "₦0"


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _lead_wa_status(lead: dict) -> str:
    status = _norm(
        lead.get("wa_status") or lead.get("verification_status") or ""
    )
    if status in {
        "verified", "opted_in", "estimated",
        "business_confirmed", "unknown",
    }:
        return status
    if (
        _safe_int(lead.get("wa_verified", 0))
        or _safe_int(lead.get("whatsapp_verified", 0))
    ):
        return "verified"
    if _safe_int(lead.get("whatsapp_ready", 0)):
        return "estimated"
    return "unknown"


# ─────────────────────────────────────────────────────────
# FILTERED STATS
# ─────────────────────────────────────────────────────────
def _get_filtered_stats(
    country: str  = "All",
    market: str   = "All",
    channel: str  = "All",
    wa_status: str = "All",
    min_fit: int  = 0,
) -> dict:
    filters_active = (
        country   != "All"
        or market   != "All"
        or channel  != "All"
        or wa_status != "All"
        or min_fit  > 0
    )

    if not filters_active:
        try:
            return get_dashboard_stats() or {}
        except Exception:
            return {}

    if get_connection is None:
        return {}

    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()

        where_parts = []
        params      = []

        if country != "All":
            where_parts.append("LOWER(TRIM(country)) = LOWER(?)")
            params.append(country.strip())
        if market != "All":
            where_parts.append("LOWER(TRIM(market)) = LOWER(?)")
            params.append(market.strip())
        if channel != "All":
            where_parts.append("LOWER(TRIM(best_channel)) = LOWER(?)")
            params.append(channel.strip())
        if wa_status != "All":
            where_parts.append("LOWER(TRIM(wa_status)) = LOWER(?)")
            params.append(wa_status.strip())
        if min_fit > 0:
            where_parts.append(
                "CAST(COALESCE(fit_score, 0) AS INTEGER) >= ?"
            )
            params.append(min_fit)

        where_sql = (
            "WHERE " + " AND ".join(where_parts)
            if where_parts else ""
        )

        def _q(sql: str) -> any:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else 0

        def _and(where_sql: str) -> str:
            return " AND " if where_sql else "WHERE "

        total_leads   = _q("SELECT COUNT(*) FROM leads " + where_sql)
        high_fit      = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(lead_priority)) IN ('high','hot')")
        medium_fit    = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(lead_priority)) IN ('medium','warm')")
        low_fit       = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(lead_priority)) = 'low'")
        unscored      = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "(fit_score IS NULL OR lead_priority IS NULL OR TRIM(lead_priority) = '')")
        with_email    = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "email IS NOT NULL AND TRIM(email) != ''")
        with_phone    = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "phone IS NOT NULL AND TRIM(phone) != ''")
        wa_verified   = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(wa_status)) IN ('verified','opted_in')")
        wa_estimated  = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(wa_status)) = 'estimated'")
        wa_ready      = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "CAST(COALESCE(whatsapp_ready,0) AS INTEGER) = 1")
        call_ready    = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "CAST(COALESCE(call_ready,0) AS INTEGER) = 1")
        email_ready   = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "email IS NOT NULL AND TRIM(email) != ''")
        wa_sent       = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(wa_status)) = 'sent'")
        with_dm       = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "decision_maker_name IS NOT NULL AND TRIM(decision_maker_name) != ''")
        deals_closed  = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(contact_status)) = 'deal_closed'")
        total_revenue = _q("SELECT COALESCE(SUM(CAST(revenue AS REAL)),0) FROM leads " + where_sql)
        manual_review = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(best_channel)) = 'manual'")
        unrouted      = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "(best_channel IS NULL OR TRIM(best_channel) = '')")
        ai_call_ready = _q("SELECT COUNT(*) FROM leads " + where_sql + _and(where_sql) + "LOWER(TRIM(best_channel)) = 'ai_call'")

        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(country),''),'Unknown') AS cn, "
            "COUNT(*) AS total FROM leads " + where_sql
            + " GROUP BY cn ORDER BY total DESC LIMIT 5",
            params,
        )
        by_country = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(market),''),'Unknown') AS mn, "
            "COUNT(*) AS total FROM leads " + where_sql
            + " GROUP BY mn ORDER BY total DESC LIMIT 5",
            params,
        )
        by_market = {r[0]: r[1] for r in cur.fetchall()}

        return {
            "total_leads":               total_leads,
            "high_fit_leads":            high_fit,
            "medium_fit_leads":          medium_fit,
            "low_fit_leads":             low_fit,
            "unscored_leads":            unscored,
            "email_ready":               with_email,
            "leads_with_email":          with_email,
            "leads_with_phone":          with_phone,
            "whatsapp_verified":         wa_verified,
            "whatsapp_estimated":        wa_estimated,
            "whatsapp_ready":            wa_ready,
            "call_ready":                call_ready,
            "ai_call_ready":             ai_call_ready,
            "whatsapp_sent":             wa_sent,
            "leads_with_decision_maker": with_dm,
            "deals_closed":              deals_closed,
            "total_revenue":             total_revenue,
            "manual_review_needed":      manual_review,
            "unrouted_leads":            unrouted,
            "by_country":                by_country,
            "by_market":                 by_market,
        }

    except Exception:
        try:
            return get_dashboard_stats() or {}
        except Exception:
            return {}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# FILTERED LEADS FOR KPI DRILL-DOWN
# ─────────────────────────────────────────────────────────
def _get_leads_for_metric(
    metric_key: str,
    country: str   = "All",
    market: str    = "All",
    channel: str   = "All",
    wa_status: str = "All",
    min_fit: int   = 0,
    limit: int     = 500,
) -> list:
    if get_connection is None:
        return []

    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()

        where_parts = []
        params      = []

        if country != "All":
            where_parts.append("LOWER(TRIM(country)) = LOWER(?)")
            params.append(country.strip())
        if market != "All":
            where_parts.append("LOWER(TRIM(market)) = LOWER(?)")
            params.append(market.strip())
        if channel != "All":
            where_parts.append("LOWER(TRIM(best_channel)) = LOWER(?)")
            params.append(channel.strip())
        if wa_status != "All":
            where_parts.append("LOWER(TRIM(wa_status)) = LOWER(?)")
            params.append(wa_status.strip())
        if min_fit > 0:
            where_parts.append(
                "CAST(COALESCE(fit_score,0) AS INTEGER) >= ?"
            )
            params.append(min_fit)

        metric_conditions = {
            "total_leads":    "",
            "high_fit":       "LOWER(TRIM(lead_priority)) IN ('high','hot')",
            "high_fit_r2":    "LOWER(TRIM(lead_priority)) IN ('high','hot')",
            "medium_fit":     "LOWER(TRIM(lead_priority)) IN ('medium','warm')",
            "low_fit":        "LOWER(TRIM(lead_priority)) = 'low'",
            "unscored":       "(fit_score IS NULL OR lead_priority IS NULL OR TRIM(lead_priority) = '')",
            "emails":         "email IS NOT NULL AND TRIM(email) != ''",
            "phones":         "phone IS NOT NULL AND TRIM(phone) != ''",
            "deals_closed":   "LOWER(TRIM(contact_status)) = 'deal_closed'",
            "wa_verified":    "LOWER(TRIM(wa_status)) IN ('verified','opted_in')",
            "wa_estimated":   "LOWER(TRIM(wa_status)) = 'estimated'",
            "wa_ready":       "CAST(COALESCE(whatsapp_ready,0) AS INTEGER) = 1",
            "ai_call_ready":  "LOWER(TRIM(best_channel)) = 'ai_call'",
            "email_ready":    "email IS NOT NULL AND TRIM(email) != ''",
            "wa_sent":        "LOWER(TRIM(wa_status)) = 'sent'",
            "decision_makers":"decision_maker_name IS NOT NULL AND TRIM(decision_maker_name) != ''",
            "manual_review":  "LOWER(TRIM(best_channel)) = 'manual'",
            "unrouted":       "(best_channel IS NULL OR TRIM(best_channel) = '')",
        }

        metric_cond = metric_conditions.get(metric_key, "")
        if metric_cond:
            where_parts.append(metric_cond)

        where_sql = (
            "WHERE " + " AND ".join(where_parts)
            if where_parts else ""
        )

        cur.execute(
            "SELECT "
            "  id, business_name, city, country, phone, email, "
            "  website, fit_score, lead_priority, best_channel, "
            "  decision_maker_name, wa_status, market, industry "
            "FROM leads "
            + where_sql
            + " ORDER BY COALESCE(fit_score,0) DESC LIMIT ?",
            params + [limit],
        )

        cols = [
            "id", "business_name", "city", "country",
            "phone", "email", "website", "fit_score",
            "lead_priority", "best_channel",
            "decision_maker_name", "wa_status",
            "market", "industry",
        ]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# FILTERED GEOGRAPHY
# ─────────────────────────────────────────────────────────
def _get_filtered_cities(
    country: str  = "All",
    market: str   = "All",
    limit: int    = 3,
) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        where_parts = []
        params      = []
        if country != "All":
            where_parts.append("LOWER(TRIM(country)) = LOWER(?)")
            params.append(country.strip())
        if market != "All":
            where_parts.append("LOWER(TRIM(market)) = LOWER(?)")
            params.append(market.strip())
        where_sql = (
            "WHERE " + " AND ".join(where_parts)
            if where_parts else ""
        )
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(city),''),'Unknown') AS place, "
            "COUNT(*) AS total FROM leads "
            + where_sql
            + " GROUP BY place ORDER BY total DESC, place ASC LIMIT ?",
            params + [limit],
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


def _get_filtered_countries(
    market: str = "All",
    limit: int  = 5,
) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        where_parts = []
        params      = []
        if market != "All":
            where_parts.append("LOWER(TRIM(market)) = LOWER(?)")
            params.append(market.strip())
        where_sql = (
            "WHERE " + " AND ".join(where_parts)
            if where_parts else ""
        )
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(country),''),'Unknown') "
            "AS country_name, COUNT(*) AS total FROM leads "
            + where_sql
            + " GROUP BY country_name "
            + "ORDER BY total DESC, country_name ASC LIMIT ?",
            params + [limit],
        )
        return [{"country_name": r[0], "total": r[1]} for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _get_filtered_markets(
    country: str = "All",
    limit: int   = 5,
) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        where_parts = []
        params      = []
        if country != "All":
            where_parts.append("LOWER(TRIM(country)) = LOWER(?)")
            params.append(country.strip())
        where_sql = (
            "WHERE " + " AND ".join(where_parts)
            if where_parts else ""
        )
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(market),''),'Unknown') "
            "AS market_name, COUNT(*) AS total FROM leads "
            + where_sql
            + " GROUP BY market_name "
            + "ORDER BY total DESC, market_name ASC LIMIT ?",
            params + [limit],
        )
        return [{"market_name": r[0], "total": r[1]} for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# FILTERED PROSPECT LIST
# ─────────────────────────────────────────────────────────
def _get_filtered_prospects(
    country: str   = "All",
    market: str    = "All",
    channel: str   = "All",
    wa_status: str = "All",
    min_fit: int   = 0,
    limit: int     = 8,
) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        where_parts = [
            "fit_score IS NOT NULL",
            "LOWER(TRIM(lead_priority)) IN ('high','hot','medium','warm')",
        ]
        params = []
        if country != "All":
            where_parts.append("LOWER(TRIM(country)) = LOWER(?)")
            params.append(country.strip())
        if market != "All":
            where_parts.append("LOWER(TRIM(market)) = LOWER(?)")
            params.append(market.strip())
        if channel != "All":
            where_parts.append("LOWER(TRIM(best_channel)) = LOWER(?)")
            params.append(channel.strip())
        if wa_status != "All":
            where_parts.append("LOWER(TRIM(wa_status)) = LOWER(?)")
            params.append(wa_status.strip())
        if min_fit > 0:
            where_parts.append(
                "CAST(COALESCE(fit_score,0) AS INTEGER) >= ?"
            )
            params.append(min_fit)
        where_sql = "WHERE " + " AND ".join(where_parts)
        cur.execute(
            "SELECT * FROM leads " + where_sql
            + " ORDER BY fit_score DESC LIMIT ?",
            params + [limit],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# DB query helpers (unscored IDs)
# ─────────────────────────────────────────────────────────
def _get_unscored_lead_ids(limit: int) -> list:
    if get_connection is None:
        return []
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT id, business_name FROM leads
            WHERE fit_score IS NULL
               OR lead_priority IS NULL
               OR lead_priority = ''
            ORDER BY id ASC LIMIT ?
            """,
            (limit,),
        )
        return [
            {"id": row[0], "name": row[1] or "Unknown"}
            for row in cur.fetchall()
        ]
    except Exception:
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# KPI DRILL-DOWN TABLE
# ─────────────────────────────────────────────────────────
def _render_kpi_drilldown(
    metric_key: str,
    metric_label: str,
    filters: dict,
) -> None:
    leads = _get_leads_for_metric(
        metric_key  = metric_key,
        country     = filters.get("country",   "All"),
        market      = filters.get("market",    "All"),
        channel     = filters.get("channel",   "All"),
        wa_status   = filters.get("wa_status", "All"),
        min_fit     = filters.get("min_fit",   0),
        limit       = 500,
    )

    count = len(leads)

    if not leads:
        st.markdown(
            "<div style='color:" + _TEXT_MUT + ";font-size:13px;"
            "padding:12px 0;'>No leads match this metric "
            "with current filters.</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        "<div style='font-size:12px;color:" + _TEXT_SEC + ";"
        "margin-bottom:8px;font-weight:600;'>"
        + str(count) + " leads</div>",
        unsafe_allow_html=True,
    )

    rows = []
    for l in leads:
        rows.append({
            "Business Name":  l.get("business_name") or "—",
            "City":           l.get("city")          or "—",
            "Country":        l.get("country")       or "—",
            "Phone":          l.get("phone")         or "—",
            "Email":          l.get("email")         or "—",
            "Website":        l.get("website")       or "—",
            "Score":          _safe_int(l.get("fit_score")),
            "Priority":       l.get("lead_priority") or "unscored",
            "Channel":        l.get("best_channel")  or "—",
            "Decision Maker": l.get("decision_maker_name") or "—",
            "WA Status":      l.get("wa_status")     or "—",
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(500, 40 + len(rows) * 35),
    )

    st.download_button(
        "Export " + metric_label + " CSV",
        data=df.to_csv(index=False),
        file_name=metric_key + "_leads.csv",
        mime="text/csv",
        key="dl_" + metric_key + "_drilldown",
    )


# ─────────────────────────────────────────────────────────
# Label helpers
# ─────────────────────────────────────────────────────────
def _get_priority_label(priority: str) -> tuple:
    p = (priority or "unscored").lower()
    return {
        "high":   ("HIGH",   _GREEN),
        "hot":    ("HIGH",   _GREEN),
        "medium": ("MEDIUM", _GOLD),
        "warm":   ("MEDIUM", _GOLD),
        "low":    ("LOW",    _TEXT_MUT),
    }.get(p, ("UNSCORED", _TEXT_SEC))


def _get_channel_label(channel: str) -> tuple:
    c = (channel or "manual").lower()
    return {
        "whatsapp": ("WhatsApp", "#25D366"),
        "ai_call":  ("AI Call",  _BRAND_PUR),
        "email":    ("Email",    _BRAND_BLUE),
        "manual":   ("Manual",   _TEXT_MUT),
    }.get(c, ("Unknown", _TEXT_SEC))


def _wa_badge_html(wa_status: str) -> str:
    cfg = {
        "verified":           ("&#9989; WA Verified",    _GREEN,      "rgba(16,185,129,0.12)"),
        "opted_in":           ("&#9989; WA Opted-In",    _GREEN,      "rgba(16,185,129,0.12)"),
        "estimated":          ("&#128172; WA Estimated", _BRAND_BLUE, "rgba(91,140,255,0.12)"),
        "business_confirmed": ("&#128992; WA Business",  _GOLD,       "rgba(245,158,11,0.12)"),
        "unknown":            ("&#9898; WA Unknown",     _TEXT_MUT,   "rgba(71,85,105,0.12)"),
    }
    label, color, bg = cfg.get(
        wa_status,
        ("&#9898; WA Unknown", _TEXT_MUT, "rgba(71,85,105,0.12)"),
    )
    return (
        "<span style='background:" + bg + ";color:" + color + ";"
        "border:1px solid " + color + "33;border-radius:999px;"
        "font-size:9px;font-weight:700;text-transform:uppercase;"
        "letter-spacing:0.5px;padding:2px 8px;'>" + label + "</span>"
    )


def _priority_badge_html(priority: str) -> str:
    label, color = _get_priority_label(priority)
    return (
        "<span style='background:" + color + "1a;color:" + color + ";"
        "border:1px solid " + color + "33;border-radius:999px;"
        "font-size:9px;font-weight:700;text-transform:uppercase;"
        "letter-spacing:0.5px;padding:2px 8px;'>" + label + "</span>"
    )


# ─────────────────────────────────────────────────────────
# Page CSS
# ─────────────────────────────────────────────────────────
def _render_page_css() -> None:
    st.markdown("""
<style>

div[data-testid="stButton"].kpi-btn > button,
div[data-testid="stButton"].kpi-btn > button:focus,
div[data-testid="stButton"].kpi-btn > button:active {
    background: linear-gradient(145deg, #0b1628 0%, #101f38 100%) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 18px !important;
    padding: 18px 18px 14px 18px !important;
    width: 100% !important;
    height: auto !important;
    text-align: left !important;
    color: #f1f5f9 !important;
    font-family: inherit !important;
    cursor: pointer !important;
    transition: transform 0.22s ease, border-color 0.25s ease,
                box-shadow 0.25s ease !important;
    white-space: pre-wrap !important;
    line-height: 1.4 !important;
    box-shadow: none !important;
    outline: none !important;
}

div[data-testid="stButton"].kpi-btn > button:hover {
    transform: translateY(-3px) !important;
    border-color: rgba(91,140,255,0.28) !important;
    box-shadow: 0 14px 40px rgba(91,140,255,0.14),
                0 4px 12px rgba(0,0,0,0.3) !important;
    background: linear-gradient(145deg, #0c1830 0%, #112240 100%) !important;
}

div[data-testid="stButton"].kpi-btn-active > button {
    border-color: rgba(91,140,255,0.45) !important;
    box-shadow: 0 0 0 2px rgba(91,140,255,0.18),
                0 14px 40px rgba(91,140,255,0.18) !important;
    background: linear-gradient(145deg, #0d1f3c 0%, #132540 100%) !important;
}

.db-drilldown-panel {
    background: linear-gradient(145deg,
        rgba(91,140,255,0.04),
        rgba(91,140,255,0.02));
    border: 1px solid rgba(91,140,255,0.18);
    border-radius: 16px;
    padding: 20px 20px 16px 20px;
    margin-bottom: 8px;
    animation: panel-in 0.18s ease;
}
@keyframes panel-in {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: translateY(0);    }
}
.db-drilldown-title {
    font-size: 13px;
    font-weight: 700;
    color: #5b8cff;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.db-drilldown-close-hint {
    font-size: 10px;
    color: #475569;
    font-weight: 600;
    margin-left: auto;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

.rh-card {
    background: linear-gradient(145deg, #0b1628, #101f38);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 16px 14px 12px 14px;
    transition: transform 0.2s ease, border-color 0.25s ease,
                box-shadow 0.25s ease;
    cursor: default;
}
.rh-card:hover {
    transform: translateY(-2px);
    border-color: rgba(91,140,255,0.25);
    box-shadow: 0 10px 28px rgba(0,0,0,0.25);
}
.rh-card-icon  { font-size: 22px; margin-bottom: 8px; }
.rh-card-label {
    color: #475569; font-size: 9.5px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px;
}
.rh-card-value {
    font-size: 22px; font-weight: 900; color: #f1f5f9;
    line-height: 1; margin-bottom: 4px;
}
.rh-card-sub { font-size: 11px; font-weight: 600; }

.spot-card {
    background: linear-gradient(145deg, #0b1628, #0f1c32);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 18px; padding: 0; overflow: hidden;
    transition: transform 0.22s ease, border-color 0.28s ease,
                box-shadow 0.28s ease;
}
.spot-card:hover {
    transform: translateY(-4px);
    border-color: rgba(91,140,255,0.30);
    box-shadow: 0 18px 50px rgba(91,140,255,0.14),
                0 4px 16px rgba(0,0,0,0.35);
}
.spot-card-bar  { height: 4px; width: 100%; }
.spot-card-body { padding: 18px 20px 16px 20px; }
.spot-card-name {
    font-size: 16px; font-weight: 800; color: #f1f5f9;
    letter-spacing: -0.3px; margin-bottom: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.spot-card-meta   { font-size: 11px; color: #475569; margin-bottom: 10px; }
.spot-card-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.spot-score-row   {
    display: grid; grid-template-columns: 1fr 1fr 1fr;
    gap: 8px; margin-bottom: 12px;
}
.spot-score-cell {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px; padding: 8px; text-align: center;
}
.spot-score-num {
    font-size: 18px; font-weight: 900; color: #f1f5f9; line-height: 1;
}
.spot-score-lbl {
    font-size: 9px; color: #475569; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.8px; margin-top: 3px;
}
.spot-reason {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px; padding: 10px 12px;
    font-size: 12px; color: #94a3b8; line-height: 1.6; margin-bottom: 10px;
}
.spot-opener {
    background: rgba(91,140,255,0.06);
    border: 1px solid rgba(91,140,255,0.14);
    border-radius: 10px; padding: 10px 12px;
    font-size: 12px; color: #cbd5e1; line-height: 1.6; font-style: italic;
}
.spot-dm { font-size: 11px; color: #64748b; margin-top: 8px; }

.score-progress-item {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px; padding: 8px 12px;
    font-size: 12px; color: #cbd5e1; line-height: 1.5;
    margin-bottom: 4px;
}

.backlog-banner {
    background: linear-gradient(135deg,
        rgba(91,140,255,0.08),
        rgba(124,92,255,0.05));
    border: 1px solid rgba(91,140,255,0.22);
    border-radius: 18px; padding: 20px 22px; margin-bottom: 14px;
}

.db-hero {
    background: linear-gradient(145deg,
        #0b1628 0%, #0e1e35 50%, #0b1628 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 22px;
    padding: 28px 32px;
    margin-bottom: 24px;
    position: relative;
    overflow: hidden;
}
.db-hero-orb-right {
    position: absolute; top: -60px; right: -60px;
    width: 280px; height: 280px;
    background: radial-gradient(circle,
        rgba(91,140,255,0.10) 0%, transparent 70%);
    pointer-events: none; border-radius: 50%;
}
.db-hero-orb-left {
    position: absolute; bottom: -80px; left: -40px;
    width: 220px; height: 220px;
    background: radial-gradient(circle,
        rgba(124,92,255,0.07) 0%, transparent 70%);
    pointer-events: none; border-radius: 50%;
}
.db-hero-eyebrow {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 2px; color: #5b8cff; margin-bottom: 8px;
}
.db-hero-title {
    font-size: 30px; font-weight: 900; color: #f1f5f9;
    letter-spacing: -1px; line-height: 1.15; margin: 0 0 8px 0;
}
.db-hero-sub {
    font-size: 13px; color: #64748b;
    font-weight: 500; line-height: 1.7;
}
.db-hero-revenue-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.5px; color: #f59e0b;
    margin-bottom: 6px; opacity: 0.85;
}
.db-hero-revenue-value {
    font-size: 32px; font-weight: 900; color: #f1f5f9;
    letter-spacing: -1px; line-height: 1;
}

.db-sec-head  { margin: 4px 0 16px 0; }
.db-sec-title {
    font-size: 20px; font-weight: 800; color: #f1f5f9;
    letter-spacing: -0.5px; line-height: 1.2; margin: 0 0 3px 0;
}
.db-sec-sub { font-size: 12px; color: #475569; font-weight: 500; margin: 0; }

.db-filter-bar {
    background: linear-gradient(135deg,
        rgba(91,140,255,0.05),
        rgba(124,92,255,0.03));
    border: 1px solid rgba(91,140,255,0.12);
    border-radius: 16px;
    padding: 16px 18px 4px 18px;
    margin-bottom: 20px;
}
.db-filter-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.2px; color: #5b8cff; margin-bottom: 10px;
}

.db-filter-active {
    display: inline-block;
    background: rgba(91,140,255,0.12);
    border: 1px solid rgba(91,140,255,0.25);
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    color: #5b8cff;
    margin: 0 4px 8px 0;
}

.geo-table {
    background: #0b1628;
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px;
    padding: 14px 16px;
}
.geo-table-title {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #475569; margin-bottom: 12px;
}
.geo-row {
    display: flex; justify-content: space-between;
    align-items: center; padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}
.geo-row:last-child { border-bottom: none; }
.geo-rank {
    font-size: 10px; font-weight: 700;
    width: 18px; text-align: center;
    margin-right: 8px; flex-shrink: 0;
}
.geo-name  { font-size: 13px; color: #94a3b8; flex: 1; }
.geo-count { font-size: 13px; font-weight: 700; color: #f1f5f9; margin-left: 8px; }

.db-empty {
    background: linear-gradient(145deg,
        rgba(255,255,255,0.015),
        rgba(255,255,255,0.008));
    border: 1px dashed rgba(91,140,255,0.18);
    border-radius: 20px;
    padding: 48px 32px;
    text-align: center;
}
.db-empty-icon  { font-size: 36px; margin-bottom: 14px; }
.db-empty-title {
    font-size: 16px; font-weight: 700; color: #94a3b8;
    margin-bottom: 8px; letter-spacing: -0.2px;
}
.db-empty-sub { font-size: 13px; color: #475569; line-height: 1.6; }

@keyframes dot-blink {
    0%,100% { opacity: 1; }
    50%      { opacity: 0.3; }
}

div[data-testid="stButton"].kpi-btn > button:focus:not(:focus-visible) {
    outline: none !important;
    box-shadow: none !important;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# KPI card
# BUG-DB-03 FIX: no_drilldown=True suppresses click strip
# for revenue and live_clock cards — avoids wasted reruns
# ─────────────────────────────────────────────────────────
def _kpi_card(
    label: str,
    value: str,
    subtitle: str,
    accent: str,
    icon: str,
    metric_key: str,
    no_drilldown: bool = False,
) -> bool:
    is_active = st.session_state.get("kpi_open_" + metric_key, False)

    indicator       = "&#9650; open" if is_active else "&#9660; click"
    indicator_color = _BRAND_BLUE if is_active else _TEXT_MUT
    active_border   = (
        "rgba(91,140,255,0.45)" if is_active
        else "rgba(255,255,255,0.06)"
    )
    active_shadow = (
        "0 0 0 2px rgba(91,140,255,0.18),"
        "0 14px 40px rgba(91,140,255,0.18);"
        if is_active else "none"
    )
    active_bg = (
        "linear-gradient(145deg,#0d1f3c 0%,#132540 100%)"
        if is_active
        else "linear-gradient(145deg,#0b1628 0%,#101f38 100%)"
    )

    # Card visual
    st.markdown(
        "<div style='"
        "background:" + active_bg + ";"
        "border:1px solid " + active_border + ";"
        "border-radius:" + ("18px" if no_drilldown else "18px 18px 0 0") + ";"
        "padding:18px 18px 10px 18px;"
        "position:relative;overflow:hidden;"
        "box-shadow:" + active_shadow + ";"
        "margin-bottom:0;'>"
        "<div style='position:absolute;top:0;left:0;right:0;"
        "height:3px;border-radius:18px 18px 0 0;"
        "background:" + accent + ";'></div>"
        "<div style='font-size:20px;margin-bottom:10px;line-height:1;'>"
        + icon + "</div>"
        "<div style='color:#475569;font-size:9.5px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:1.2px;"
        "margin-bottom:6px;'>"
        + _esc(label) + "</div>"
        "<div style='font-size:26px;font-weight:900;color:#f1f5f9;"
        "letter-spacing:-1px;line-height:1;margin-bottom:6px;'>"
        + _esc(value) + "</div>"
        "<div style='font-size:11px;color:#64748b;font-weight:500;'>"
        + _esc(subtitle) + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # BUG-DB-03 FIX: skip click strip entirely for non-drilldown cards
    if no_drilldown:
        return False

    clicked = st.button(
        indicator,
        key        = "kpi_btn_" + metric_key,
        use_container_width = True,
        help       = "Click to open or close leads for " + label,
    )

    st.markdown(
        "<style>"
        "div[data-testid='stButton']:has(button[kind='secondary']"
        "[data-testid='baseButton-secondary']) {"
        "    margin-top: -1px !important;"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )

    if clicked:
        st.session_state["kpi_open_" + metric_key] = not is_active
        st.rerun()

    return st.session_state.get("kpi_open_" + metric_key, False)


# ─────────────────────────────────────────────────────────
# Routing health section
# ─────────────────────────────────────────────────────────
def _render_routing_health(stats: dict) -> None:
    wa_verified   = _safe_int(stats.get("whatsapp_verified", 0))
    wa_estimated  = _safe_int(stats.get("whatsapp_estimated", 0))
    call_ready    = _safe_int(stats.get("call_ready", 0))
    email_ready   = _safe_int(stats.get("email_ready", 0))
    manual_needed = _safe_int(stats.get("manual_review_needed", 0))
    unrouted      = _safe_int(stats.get("unrouted_leads", 0))
    total         = _safe_int(stats.get("total_leads", 0))

    if not any([wa_verified, wa_estimated, call_ready,
                email_ready, manual_needed, unrouted]):
        return

    st.markdown(
        '<div class="db-sec-head">'
        '<div class="db-sec-title">Routing Health</div>'
        '<div class="db-sec-sub">'
        'WhatsApp verification, country/market split '
        'and channel assignment coverage.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    cards = [
        ("&#128172;", "WA Verified",   wa_verified,   "Agent confirmed",  _GREEN),
        ("&#128993;", "WA Estimated",  wa_estimated,  "Heuristic ready",  _GOLD),
        ("&#128222;", "Call Ready",    call_ready,    "Valid phone",       _BRAND_BLUE),
        ("&#128231;", "Email Ready",   email_ready,   "Valid email",       "#0ea5e9"),
        ("&#128269;", "Manual Review", manual_needed, "Research needed",   _RED),
        ("&#9888;",   "Unrouted",      unrouted,      "Needs route run",  _GOLD),
    ]

    cols = st.columns(6)
    for col, (icon, label, val, sub, color) in zip(cols, cards):
        with col:
            st.markdown(
                '<div class="rh-card">'
                '<div class="rh-card-icon">' + icon + '</div>'
                '<div class="rh-card-label">' + _esc(label) + '</div>'
                '<div class="rh-card-value" style="color:' + color + ';">'
                + "{:,}".format(val) +
                '</div>'
                '<div class="rh-card-sub" style="color:' + color + ';">'
                + _esc(sub) +
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    if total > 0 and wa_verified > 0:
        pct = round((wa_verified / total) * 100, 1)
        st.markdown(
            "<div style='font-size:11px;color:" + _TEXT_MUT + ";margin-top:8px;'>"
            "WA verification coverage: "
            "<b style='color:" + _TEXT_SEC + ";'>"
            + "{:,}".format(wa_verified) + "/" + "{:,}".format(total) +
            "</b>"
            " leads (" + str(pct) + "%) manually confirmed by agents.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Spotlight card
# BUG-DB-01 FIX: fit_score thresholds updated to 0-100 scale
# ─────────────────────────────────────────────────────────
def _render_spotlight_card(lead: dict, channel_color: str) -> None:
    fit_score    = _safe_int(lead.get("fit_score", 0))
    urgency      = _safe_int(lead.get("urgency_score", 0))
    reachability = _safe_int(lead.get("reachability_score", 0))
    priority     = str(
        lead.get("lead_priority", "unscored") or "unscored"
    ).lower()
    wa_status    = _lead_wa_status(lead)

    dm = (
        lead.get("decision_maker_name")
        or lead.get("decision_maker_first")
        or "Not found"
    )
    fit_reason = _truncate_text(
        lead.get("fit_reason") or "No fit reason yet.", 160
    )
    opener = _truncate_text(
        lead.get("recommended_opener") or "No opener yet.", 160
    )
    pain_points = _parse_json_list(lead.get("pain_points"))

    name     = _esc(lead.get("business_name", "Unknown"))
    industry = _esc(lead.get("industry", "Unknown"))
    city     = _esc(lead.get("city", ""))
    country  = _esc(lead.get("country", ""))
    phone    = _esc(lead.get("phone", "") or "—")

    # BUG-DB-01 FIX: updated from 1-10 to 0-100 scale
    if fit_score >= 70:   fit_color = _GREEN
    elif fit_score >= 50: fit_color = _GOLD
    else:                 fit_color = _RED

    pain_html = ""
    if pain_points:
        items = "".join(
            "<div style='font-size:11px;color:" + _TEXT_MUT
            + ";padding:2px 0;'>&#8226; " + _esc(str(p)) + "</div>"
            for p in pain_points[:3]
        )
        pain_html = (
            "<div style='margin-top:8px;"
            "border-top:1px solid " + _BORDER + ";"
            "padding-top:8px;'>" + items + "</div>"
        )

    st.markdown(
        '<div class="spot-card">'
        '<div class="spot-card-bar" style="background:linear-gradient(90deg,'
        + channel_color + ',' + _BRAND_PUR + ');"></div>'
        '<div class="spot-card-body">'
        '<div class="spot-card-name">' + name + '</div>'
        '<div class="spot-card-meta">'
        + industry + ' &bull; ' + city + ' ' + country +
        '</div>'
        '<div class="spot-card-badges">'
        + _priority_badge_html(priority)
        + _wa_badge_html(wa_status) +
        '</div>'
        '<div class="spot-score-row">'
        '<div class="spot-score-cell">'
        '<div class="spot-score-num" style="color:' + fit_color + ';">'
        + str(fit_score) + '</div>'
        '<div class="spot-score-lbl">Fit</div>'
        '</div>'
        '<div class="spot-score-cell">'
        '<div class="spot-score-num">' + str(urgency) + '</div>'
        '<div class="spot-score-lbl">Urgency</div>'
        '</div>'
        '<div class="spot-score-cell">'
        '<div class="spot-score-num">' + str(reachability) + '</div>'
        '<div class="spot-score-lbl">Reach</div>'
        '</div>'
        '</div>'
        '<div class="spot-reason">' + _esc(fit_reason) + '</div>'
        '<div class="spot-opener">&ldquo;' + _esc(opener) + '&rdquo;</div>'
        '<div class="spot-dm">&#128100; ' + _esc(dm)
        + ' &ensp;|&ensp; &#128222; ' + phone + '</div>'
        + pain_html +
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Routing batch helper
# ─────────────────────────────────────────────────────────
def _route_unrouted_batch(limit: int) -> dict:
    if not _ROUTER_AVAILABLE:
        return {"total": 0, "routed": 0, "error": "router_unavailable"}
    if get_connection is None:
        return {"total": 0, "routed": 0, "error": "db_unavailable"}
    if apply_routing_update is None:
        return {"total": 0, "routed": 0, "error": "db_update_unavailable"}

    leads = get_unrouted_leads(limit=limit) or []
    total = len(leads)
    if total == 0:
        return {"total": 0, "routed": 0}

    routed = 0
    for lead in leads:
        lead_id = lead.get("id")
        if not lead_id:
            continue
        try:
            updates = _router_assign_channel(lead) or {}
            if updates and apply_routing_update(lead_id, updates):
                routed += 1
        except Exception:
            continue

    return {"total": total, "routed": routed}


# ─────────────────────────────────────────────────────────
# Score + route with progress UI
# BUG-DB-02 FIX: thresholds updated to 0-100 scale
#                "/10" label changed to "/100"
# ─────────────────────────────────────────────────────────
def _score_with_progress(
    unscored_count: int,
    unrouted_count: int,
    batch_size: int,
) -> dict:
    result: dict = {
        "scored_total": 0, "scored_done": 0,
        "routed_total": 0, "routed_done": 0,
        "router_used": _ROUTER_AVAILABLE,
        "high": 0, "medium": 0, "low": 0,
    }

    score_limit = (
        min(batch_size, unscored_count) if unscored_count > 0 else 0
    )

    if score_limit > 0:
        if _SCORER_AVAILABLE:
            lead_ids = _get_unscored_lead_ids(score_limit)
            total    = len(lead_ids)
            result["scored_total"] = total

            if total > 0:
                st.markdown(
                    "<div style='font-size:11px;font-weight:700;"
                    "text-transform:uppercase;letter-spacing:1px;"
                    "color:" + _BRAND_BLUE + ";margin-bottom:8px;'>"
                    "&#128202; Scoring Progress</div>",
                    unsafe_allow_html=True,
                )

                progress_bar  = st.progress(0.0)
                status_slot   = st.empty()
                feedback_slot = st.empty()

                scored_count = high = medium = low = 0

                for i, info in enumerate(lead_ids):
                    lead_id   = info["id"]
                    lead_name = info["name"]

                    progress_bar.progress(
                        float(i) / float(max(total, 1))
                    )
                    status_slot.markdown(
                        "<div style='color:" + _TEXT_SEC + ";"
                        "font-size:12px;padding:4px 0;'>"
                        "&#9203; Lead <b>" + str(i + 1) + "</b> / <b>"
                        + str(total) + "</b> &mdash; "
                        + _esc(lead_name) + "</div>",
                        unsafe_allow_html=True,
                    )

                    try:
                        res = score_and_save_lead(lead_id)
                        if res:
                            scored_count += 1
                            fit      = _safe_int(res.get("fit_score", 0))
                            priority = str(
                                res.get("lead_priority", "unscored")
                                or "unscored"
                            ).lower()
                            audit = _safe_int(
                                res.get("website_audit_score", 0)
                            )

                            # BUG-DB-02 FIX: updated from 1-10 to 0-100 scale
                            if priority in ("high", "hot") or fit >= 70:
                                high += 1; dot = "&#128994;"; fc = _GREEN
                            elif priority in ("medium", "warm") or fit >= 50:
                                medium += 1; dot = "&#128993;"; fc = _GOLD
                            else:
                                low += 1; dot = "&#128308;"; fc = _RED

                            feedback_slot.markdown(
                                "<div class='score-progress-item'>"
                                + dot + " <b style='color:" + fc + ";'>"
                                + _esc(lead_name) + "</b>"
                                # BUG-DB-02 FIX: "/10" → "/100"
                                " &ensp;fit: <b>" + str(fit) + "/100</b>"
                                " &ensp;audit: <b>" + str(audit) + "/10</b>"
                                " &ensp;priority: <b>"
                                + _esc(priority) + "</b>"
                                "</div>",
                                unsafe_allow_html=True,
                            )
                    except Exception as e:
                        feedback_slot.markdown(
                            "<div style='color:" + _RED + ";"
                            "font-size:12px;padding:4px 0;'>"
                            "&#9888; Failed: " + _esc(lead_name)
                            + " &mdash; " + _esc(str(e)) + "</div>",
                            unsafe_allow_html=True,
                        )

                progress_bar.progress(1.0)
                status_slot.markdown(
                    "<div style='color:" + _GREEN + ";font-size:13px;"
                    "font-weight:700;padding:6px 0;'>"
                    "&#9989; Scoring complete &mdash; "
                    + str(scored_count) + "/" + str(total)
                    + " leads scored</div>",
                    unsafe_allow_html=True,
                )
                feedback_slot.empty()

                result.update({
                    "scored_done": scored_count,
                    "high": high, "medium": medium, "low": low,
                })

        else:
            score_res = score_unscored_leads_batch(
                limit=score_limit, use_ai=True
            ) or {}
            result["scored_total"] = _safe_int(score_res.get("total", 0))
            result["scored_done"]  = _safe_int(score_res.get("scored", 0))
            result["high"]   = _safe_int(score_res.get("high", 0))
            result["medium"] = _safe_int(score_res.get("medium", 0))
            result["low"]    = _safe_int(score_res.get("low", 0))

    if unrouted_count > 0:
        route_limit = min(400, unrouted_count)
        st.markdown(
            "<div style='font-size:11px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px;"
            "color:" + _BRAND_BLUE + ";margin:14px 0 8px 0;'>"
            "&#128256; Routing Unrouted Leads</div>",
            unsafe_allow_html=True,
        )
        route_bar  = st.progress(0.0)
        route_slot = st.empty()
        route_slot.markdown(
            "<div style='color:" + _TEXT_SEC + ";font-size:12px;"
            "padding:4px 0;'>"
            "&#9203; Routing " + str(route_limit) + " leads&#8230;</div>",
            unsafe_allow_html=True,
        )

        route_res = _route_unrouted_batch(limit=route_limit) or {}

        route_bar.progress(1.0)
        route_slot.markdown(
            "<div style='color:" + _GREEN + ";font-size:13px;"
            "font-weight:700;padding:6px 0;'>"
            "&#9989; Routing complete &mdash; "
            + str(route_res.get("routed", 0)) + "/"
            + str(route_res.get("total", route_limit))
            + " leads routed</div>",
            unsafe_allow_html=True,
        )

        result["routed_total"] = _safe_int(route_res.get("total", route_limit))
        result["routed_done"]  = _safe_int(route_res.get("routed", 0))
        if route_res.get("error"):
            result["route_error"] = route_res["error"]

    return result


# ─────────────────────────────────────────────────────────
# Geography table
# ─────────────────────────────────────────────────────────
def _geo_table(rows: list, key_col: str, label: str) -> None:
    if not rows:
        st.markdown(
            "<div style='font-size:12px;color:" + _TEXT_MUT + ";"
            "padding:12px 0;'>No data</div>",
            unsafe_allow_html=True,
        )
        return

    rank_colors = ["#f59e0b", "#94a3b8", "#cd7f32"]
    rows_html   = ""

    for i, row in enumerate(rows):
        name       = _esc(str(row.get(key_col, "Unknown") or "Unknown"))
        total      = _safe_int(row.get("total", 0))
        rank_color = rank_colors[i] if i < 3 else _TEXT_MUT
        rank_num   = "#" + str(i + 1)
        rows_html += (
            '<div class="geo-row">'
            '<span class="geo-rank" style="color:' + rank_color + ';">'
            + rank_num + '</span>'
            '<span class="geo-name">' + name + '</span>'
            '<span class="geo-count">' + "{:,}".format(total) + '</span>'
            '</div>'
        )

    st.markdown(
        '<div class="geo-table">'
        '<div class="geo-table-title">' + label + '</div>'
        + rows_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Active filter badge row
# ─────────────────────────────────────────────────────────
def _render_active_filter_badges(
    country: str,
    market: str,
    channel: str,
    wa_status: str,
    min_fit: int,
) -> None:
    badges = []
    if country   != "All": badges.append("Country: " + country)
    if market    != "All": badges.append("Market: " + market)
    if channel   != "All": badges.append("Channel: " + channel)
    if wa_status != "All": badges.append("WA: " + wa_status)
    if min_fit    > 0:     badges.append("Min Fit: " + str(min_fit))

    if not badges:
        st.markdown(
            "<div style='font-size:11px;color:" + _TEXT_MUT + ";"
            "margin-bottom:12px;'>Showing all leads — no filters active.</div>",
            unsafe_allow_html=True,
        )
        return

    html_badges = "".join(
        '<span class="db-filter-active">' + _esc(b) + '</span>'
        for b in badges
    )
    st.markdown(
        "<div style='margin-bottom:12px;'>"
        "<span style='font-size:10px;color:" + _TEXT_MUT + ";"
        "font-weight:700;text-transform:uppercase;letter-spacing:1px;"
        "margin-right:8px;'>Active filters:</span>"
        + html_badges +
        "</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Drill-down panel
# ─────────────────────────────────────────────────────────
def _render_drilldown_panel(
    metric_key: str,
    label: str,
    active_filters: dict,
) -> None:
    st.markdown(
        "<div class='db-drilldown-panel'>"
        "<div class='db-drilldown-title'>"
        "&#128202; " + _esc(label) + " &mdash; leads"
        "<span class='db-drilldown-close-hint'>"
        "&#9650; click card to close"
        "</span>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    _render_kpi_drilldown(metric_key, label, active_filters)


# ─────────────────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────────────────
def render_dashboard() -> None:

    _render_page_css()

    now = datetime.now()

    try:
        _raw_stats  = get_dashboard_stats() or {}
        _by_country = _raw_stats.get("by_country") or {}
        _by_market  = _raw_stats.get("by_market")  or {}
        if not isinstance(_by_country, dict): _by_country = {}
        if not isinstance(_by_market,  dict): _by_market  = {}
    except Exception:
        _by_country = {}
        _by_market  = {}

    country_options = ["All"] + sorted(
        {str(k).strip() for k in _by_country.keys() if str(k).strip()}
    )
    market_options = ["All"] + sorted(
        {str(k).strip() for k in _by_market.keys() if str(k).strip()}
    )

    st.markdown(
        '<div class="db-filter-bar">'
        '<div class="db-filter-label">&#128269; Filters</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    f1, f2, f3, f4, f5, f6 = st.columns([1.2, 1.2, 1, 1, 1, 1.1])
    with f1:
        selected_country = st.selectbox(
            "Country", country_options, index=0, key="db_country",
        )
    with f2:
        selected_market = st.selectbox(
            "Market", market_options, index=0, key="db_market",
        )
    with f3:
        selected_channel = st.selectbox(
            "Channel",
            ["All", "whatsapp", "ai_call", "email", "manual"],
            index=0, key="db_channel",
        )
    with f4:
        selected_wa = st.selectbox(
            "WA Status",
            ["All", "verified", "estimated",
             "business_confirmed", "opted_in", "unknown"],
            index=0, key="db_wa",
        )
    with f5:
        min_fit = st.selectbox(
            "Min Fit", [0, 4, 5, 6, 7, 8],
            index=0, key="db_min_fit",
        )
    with f6:
        batch_size = st.selectbox(
            "Score Batch", [5, 10, 20, 50, 100],
            index=1, key="db_batch_size",
        )

    active_filters = dict(
        country   = selected_country,
        market    = selected_market,
        channel   = selected_channel,
        wa_status = selected_wa,
        min_fit   = min_fit,
    )

    _render_active_filter_badges(
        selected_country, selected_market,
        selected_channel, selected_wa, min_fit,
    )

    stats = _get_filtered_stats(**active_filters)

    total_leads              = _safe_int(stats.get("total_leads", 0))
    hot_leads                = _safe_int(stats.get("high_fit_leads", 0))
    leads_with_email         = _safe_int(stats.get("email_ready", 0))
    leads_with_phone         = _safe_int(stats.get("leads_with_phone", 0))
    deals_closed             = _safe_int(stats.get("deals_closed", 0))
    total_revenue            = stats.get("total_revenue", 0) or 0
    high_fit_leads           = _safe_int(stats.get("high_fit_leads", 0))
    medium_fit_leads         = _safe_int(stats.get("medium_fit_leads", 0))
    low_fit_leads            = _safe_int(stats.get("low_fit_leads", 0))
    unscored_leads           = _safe_int(stats.get("unscored_leads", 0))
    unrouted_leads           = _safe_int(stats.get("unrouted_leads", 0))
    whatsapp_ready_count     = _safe_int(stats.get("whatsapp_ready", 0))
    whatsapp_verified_count  = _safe_int(stats.get("whatsapp_verified", 0))
    whatsapp_estimated_count = _safe_int(stats.get("whatsapp_estimated", 0))
    ai_call_ready_count      = _safe_int(stats.get("ai_call_ready", 0))
    email_ready_count        = _safe_int(stats.get("email_ready", 0))
    whatsapp_sent_count      = _safe_int(stats.get("whatsapp_sent", 0))
    decision_maker_count     = _safe_int(
        stats.get("leads_with_decision_maker", 0)
    )

    high_intent_pct = (
        round((high_fit_leads / total_leads) * 100)
        if total_leads else 0
    )

    filters_active = (
        selected_country != "All"
        or selected_market != "All"
        or selected_channel != "All"
        or selected_wa != "All"
        or min_fit > 0
    )

    top_cities_filtered = _get_filtered_cities(
        country=selected_country,
        market=selected_market,
        limit=3,
    )

    city_line = ""
    if top_cities_filtered:
        city_line = " &bull; " + " / ".join(
            _esc(c.get("place", "")) for c in top_cities_filtered
        )

    filter_note = ""
    if filters_active:
        filter_note = (
            " &bull; <span style='color:#5b8cff;font-weight:700;'>"
            "Filtered view</span>"
        )

    timestamp_str   = _esc(now.strftime("%d %b %Y, %H:%M"))
    revenue_str     = _fmt_money(total_revenue)
    deals_str       = str(deals_closed)
    total_leads_str = "{:,}".format(total_leads)
    intent_str      = str(high_intent_pct)

    st.markdown(
        '<div class="db-hero">'
        '<div class="db-hero-orb-right"></div>'
        '<div class="db-hero-orb-left"></div>'
        '<div style="display:flex;justify-content:space-between;'
        'gap:24px;align-items:flex-start;flex-wrap:wrap;'
        'position:relative;z-index:1;">'
        '<div style="flex:1;min-width:260px;">'
        '<div class="db-hero-eyebrow">Executive Dashboard</div>'
        '<div class="db-hero-title">'
        + total_leads_str + ' opportunities tracked'
        '</div>'
        '<div class="db-hero-sub">'
        + intent_str + '% high-intent'
        + city_line
        + ' &bull; ' + timestamp_str
        + filter_note +
        '</div>'
        '</div>'
        '<div style="text-align:right;">'
        '<div class="db-hero-revenue-label">Revenue Pipeline</div>'
        '<div class="db-hero-revenue-value">' + revenue_str + '</div>'
        '<div style="font-size:11px;color:' + _TEXT_MUT + ';margin-top:4px;">'
        + deals_str + ' deals closed'
        '</div>'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    def _init_key(mk: str) -> None:
        if "kpi_open_" + mk not in st.session_state:
            st.session_state["kpi_open_" + mk] = False

    # ════════════════════════════════════════════════════
    # KPI ROW 1
    # ════════════════════════════════════════════════════
    st.markdown(
        '<div class="db-sec-head">'
        '<div class="db-sec-title">Overview</div>'
        '<div class="db-sec-sub">'
        'Click any card to see the leads behind it. Click again to close.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    kpis_r1 = [
        ("Total Leads",  "{:,}".format(total_leads),
         "All opportunities",  _BRAND_BLUE, "&#128202;", "total_leads",  False),
        ("High Fit",     "{:,}".format(hot_leads),
         "High-intent leads",  _GREEN,      "&#127919;", "high_fit",     False),
        ("Emails",       "{:,}".format(leads_with_email),
         "Reachable contacts", "#0ea5e9",   "&#9993;",   "emails",       False),
        ("Phones",       "{:,}".format(leads_with_phone),
         "Direct outreach",    "#38bdf8",   "&#128222;", "phones",       False),
        ("Deals Closed", "{:,}".format(deals_closed),
         "Closed won",         _BRAND_PUR,  "&#128176;", "deals_closed", False),
        # BUG-DB-03 FIX: no_drilldown=True — revenue has no lead list
        ("Revenue",      _fmt_money_plain(total_revenue),
         "Total tracked",      _GOLD,       "&#128188;", "revenue",      True),
    ]

    for mk in [t[5] for t in kpis_r1]:
        _init_key(mk)

    r1_cols = st.columns(6)
    for col, (label, val, sub, accent, icon, mkey, no_dd) in zip(r1_cols, kpis_r1):
        with col:
            _kpi_card(label, val, sub, accent, icon, mkey, no_drilldown=no_dd)

    for label, val, sub, accent, icon, mkey, no_dd in kpis_r1:
        if no_dd:
            continue
        if st.session_state.get("kpi_open_" + mkey, False):
            _render_drilldown_panel(mkey, label, active_filters)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # KPI ROW 2
    # ════════════════════════════════════════════════════
    kpis_r2 = [
        ("High Fit",     "{:,}".format(high_fit_leads),
         "Priority prospects",  _GREEN,      "&#128994;", "high_fit_r2",  False),
        ("Medium Fit",   "{:,}".format(medium_fit_leads),
         "Warm prospects",      _GOLD,       "&#128993;", "medium_fit",   False),
        ("Low Fit",      "{:,}".format(low_fit_leads),
         "Lower priority",      _TEXT_MUT,   "&#128308;", "low_fit",      False),
        ("Unscored",     "{:,}".format(unscored_leads),
         "Awaiting scoring",    _BRAND_BLUE, "&#129504;", "unscored",     False),
        ("WA Verified",  "{:,}".format(whatsapp_verified_count),
         "Confirmed by agent",  "#25D366",   "&#9989;",   "wa_verified",  False),
        ("WA Estimated", "{:,}".format(whatsapp_estimated_count),
         "Heuristic assigned",  _GOLD,       "&#128172;", "wa_estimated", False),
    ]

    for mk in [t[5] for t in kpis_r2]:
        _init_key(mk)

    r2_cols = st.columns(6)
    for col, (label, val, sub, accent, icon, mkey, no_dd) in zip(r2_cols, kpis_r2):
        with col:
            _kpi_card(label, val, sub, accent, icon, mkey, no_drilldown=no_dd)

    for label, val, sub, accent, icon, mkey, no_dd in kpis_r2:
        if no_dd:
            continue
        if st.session_state.get("kpi_open_" + mkey, False):
            _render_drilldown_panel(mkey, label, active_filters)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # KPI ROW 3
    # ════════════════════════════════════════════════════
    kpis_r3 = [
        ("WA Ready",        "{:,}".format(whatsapp_ready_count),
         "All WA-ready",      "#25D366",   "&#128172;",  "wa_ready",       False),
        ("AI Call Ready",   "{:,}".format(ai_call_ready_count),
         "Call queue",        _BRAND_PUR,  "&#128242;",  "ai_call_ready",  False),
        ("Email Ready",     "{:,}".format(email_ready_count),
         "Cold email queue",  _BRAND_BLUE, "&#128232;",  "email_ready",    False),
        ("WA Sent",         "{:,}".format(whatsapp_sent_count),
         "Already contacted", _GREEN,      "&#9989;",    "wa_sent",        False),
        ("Decision Makers", "{:,}".format(decision_maker_count),
         "Named contacts",    "#fb7185",   "&#128100;",  "decision_makers",False),
        # BUG-DB-03 FIX: no_drilldown=True — clock has no lead list
        ("Live Clock",      now.strftime("%H:%M"),
         "Dashboard time",    "#e879f9",   "&#128336;",  "live_clock",     True),
    ]

    for mk in [t[5] for t in kpis_r3]:
        _init_key(mk)

    r3_cols = st.columns(6)
    for col, (label, val, sub, accent, icon, mkey, no_dd) in zip(r3_cols, kpis_r3):
        with col:
            _kpi_card(label, val, sub, accent, icon, mkey, no_drilldown=no_dd)

    for label, val, sub, accent, icon, mkey, no_dd in kpis_r3:
        if no_dd:
            continue
        if st.session_state.get("kpi_open_" + mkey, False):
            _render_drilldown_panel(mkey, label, active_filters)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # ROUTING HEALTH
    # ════════════════════════════════════════════════════
    _render_routing_health(stats)

    # ════════════════════════════════════════════════════
    # SCORING / ROUTING BACKLOG
    # ════════════════════════════════════════════════════
    if unscored_leads > 0 or unrouted_leads > 0:

        col_info, col_btn = st.columns([3, 1])

        with col_info:
            st.markdown(
                '<div class="backlog-banner">'
                '<div style="font-size:16px;font-weight:800;'
                'color:' + _TEXT_PRI + ';margin-bottom:6px;">'
                '&#129504; Scoring + Routing Backlog'
                '</div>'
                '<div style="font-size:13px;color:' + _TEXT_SEC
                + ';line-height:1.7;">'
                'Unscored: <b style="color:' + _TEXT_PRI + ';">'
                + "{:,}".format(unscored_leads) +
                '</b>'
                ' &ensp;&bull;&ensp; '
                'Unrouted: <b style="color:' + _TEXT_PRI + ';">'
                + "{:,}".format(unrouted_leads) +
                '</b>'
                '<br>'
                'Click to score the next '
                '<b style="color:' + _BRAND_BLUE + ';">'
                + str(batch_size) + '</b>'
                ' leads and route any unrouted leads.'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

        with col_btn:
            st.markdown(
                "<div style='height:14px'></div>",
                unsafe_allow_html=True,
            )
            if st.button(
                "&#129504; Score " + str(batch_size) + " Leads",
                use_container_width=True,
                type="primary",
                key="score_existing_leads_dashboard",
            ):
                with st.container():
                    result = _score_with_progress(
                        unscored_count=unscored_leads,
                        unrouted_count=unrouted_leads,
                        batch_size=batch_size,
                    )

                scored  = result.get("scored_done", 0)
                s_total = result.get("scored_total", 0)
                routed  = result.get("routed_done", 0)

                st.success(
                    "Scored " + str(scored) + "/" + str(s_total)
                    + " leads — " + str(routed) + " routed."
                )
                if result.get("route_error"):
                    st.warning(
                        "Routing notice: " + str(result["route_error"])
                    )
                st.rerun()

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # AI PROSPECT SPOTLIGHT
    # ════════════════════════════════════════════════════
    st.markdown(
        '<div class="db-sec-head">'
        '<div class="db-sec-title">AI Prospect Queue</div>'
        '<div class="db-sec-sub">'
        'Best leads to contact first &mdash; ranked by fit score, '
        'priority, reachability and channel.'
        + (' &bull; <span style="color:#5b8cff;">Filtered</span>'
           if filters_active else '') +
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    top_prospects = _get_filtered_prospects(**active_filters, limit=8)

    if top_prospects:
        spotlight = top_prospects[:3]
        cols      = st.columns(len(spotlight))
        for idx, lead in enumerate(spotlight):
            _, ch_color = _get_channel_label(lead.get("best_channel"))
            with cols[idx]:
                _render_spotlight_card(lead, ch_color)
    else:
        st.markdown(
            '<div class="db-empty">'
            '<div class="db-empty-icon">&#129504;</div>'
            '<div class="db-empty-title">No scored prospects yet</div>'
            '<div class="db-empty-sub">'
            'Run the scoring backlog above to generate AI-ranked '
            'prospects.<br>'
            'High-fit leads will appear here automatically.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # LIVE SCRAPING MONITOR
    # ════════════════════════════════════════════════════
    running_job = _safe_call(get_running_job, {}) or {}
    if running_job:
        job_summary = (
            _safe_call(
                lambda: get_job_status_summary(running_job.get("id")),
                {}
            ) or {}
        )

        st.markdown(
            '<div class="db-sec-head">'
            '<div class="db-sec-title">Live Scraping Activity</div>'
            '<div class="db-sec-sub">'
            'Current active job &mdash; progress tracked in real time.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        prog_int  = _safe_int(job_summary.get("progress", 0))
        prog_frac = max(0.0, min(1.0, prog_int / 100.0))
        leads_n   = _safe_int(job_summary.get("leads", 0))
        message   = _esc(job_summary.get("message", "Running&#8230;"))
        source    = _esc(str(running_job.get("source", "")))
        keyword   = _esc(str(running_job.get("keyword", "")))
        location  = _esc(str(running_job.get("location", "")))

        st.markdown(
            '<div style="'
            'background:linear-gradient(135deg,'
            'rgba(16,185,129,0.06),rgba(16,185,129,0.02));'
            'border:1px solid rgba(16,185,129,0.14);'
            'border-radius:16px;'
            'padding:20px 24px;'
            'margin-bottom:12px;">'
            '<div style="display:flex;justify-content:space-between;'
            'align-items:flex-start;gap:16px;flex-wrap:wrap;">'
            '<div style="flex:1;min-width:200px;">'
            '<div style="display:flex;align-items:center;'
            'gap:8px;margin-bottom:8px;">'
            '<span style="display:inline-block;width:8px;height:8px;'
            'border-radius:50%;background:' + _GREEN + ';'
            'animation:dot-blink 1.6s ease infinite;"></span>'
            '<span style="font-size:10px;font-weight:700;'
            'text-transform:uppercase;letter-spacing:1px;'
            'color:' + _GREEN + ';">Active Job</span>'
            '</div>'
            '<div style="font-size:14px;font-weight:700;'
            'color:' + _TEXT_PRI + ';margin-bottom:4px;">'
            + message +
            '</div>'
            '<div style="font-size:11px;color:' + _TEXT_MUT + ';">'
            + source + ' &bull; ' + keyword + ' &bull; ' + location +
            '</div>'
            '</div>'
            '<div style="display:flex;gap:20px;align-items:center;">'
            '<div style="text-align:center;">'
            '<div style="font-size:28px;font-weight:900;'
            'color:' + _TEXT_PRI + ';">' + str(prog_int) + '%</div>'
            '<div style="font-size:9px;color:' + _TEXT_MUT + ';'
            'font-weight:700;text-transform:uppercase;letter-spacing:1px;">'
            'Progress</div>'
            '</div>'
            '<div style="text-align:center;">'
            '<div style="font-size:28px;font-weight:900;'
            'color:' + _GREEN + ';">' + "{:,}".format(leads_n) + '</div>'
            '<div style="font-size:9px;color:' + _TEXT_MUT + ';'
            'font-weight:700;text-transform:uppercase;letter-spacing:1px;">'
            'Leads</div>'
            '</div>'
            '</div>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.progress(prog_frac)

        current_lead = running_job.get("current_lead", "")
        if current_lead:
            st.markdown(
                "<div style='font-size:11px;color:" + _TEXT_MUT + ";"
                "margin-top:4px;'>"
                "Processing: <b style='color:" + _TEXT_SEC + ";'>"
                + _esc(str(current_lead)) + "</b></div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════
    # GEOGRAPHY SNAPSHOT
    # ════════════════════════════════════════════════════
    top_countries = _get_filtered_countries(market=selected_market, limit=5)
    top_markets   = _get_filtered_markets(country=selected_country, limit=5)
    top_cities    = _get_filtered_cities(
        country=selected_country,
        market=selected_market,
        limit=5,
    )

    if top_countries or top_markets or top_cities:
        st.markdown(
            '<div class="db-sec-head">'
            '<div class="db-sec-title">Geography Snapshot</div>'
            '<div class="db-sec-sub">'
            'Where your leads are concentrated'
            + (' &mdash; filtered view.' if filters_active
               else ' &mdash; all leads.')
            + '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        gc1, gc2, gc3 = st.columns(3)
        with gc1:
            _geo_table(top_countries, "country_name", "Top Countries")
        with gc2:
            _geo_table(top_markets, "market_name", "Top Markets")
        with gc3:
            _geo_table(top_cities, "place", "Top Cities")

    # ════════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════════
    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:11px;color:" + _TEXT_MUT + ";"
        "border-top:1px solid " + _BORDER + ";"
        "padding-top:14px;'>"
        "Last refreshed: "
        "<b style='color:" + _TEXT_SEC + ";'>"
        + _esc(now.strftime("%A, %d %B %Y, %H:%M:%S")) +
        "</b>"
        " &bull; All figures read live from SQLite."
        + (" &bull; <b style='color:#5b8cff;'>Filtered view active.</b>"
           if filters_active else "")
        + "</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
def render() -> None:
    render_dashboard()