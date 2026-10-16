# pages/reports.py
# KNCS LeadGen Pro — v9.9
# Base: v9.7 Fixed
# v9.9 changes (marked ── v9.9):
#   1. render_hero() — version string updated to v9.9
#   2. render_overview() — DM coverage stat block added
#   3. _render_dm_source_breakdown() — new helper
#      called from Overview tab
#   4. _render_cqc_rating_breakdown() — new helper
#      called from Overview tab (only if CQC data exists)
#   5. render_drilldown() — CQC fields + dm_source added

from __future__ import annotations

from datetime import datetime, timedelta
import html
import json
import math
import re
from typing import Optional

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────
# Safe DB imports
# ─────────────────────────────────────────────────────────
try:
    from database import (
        get_connection,
        get_dashboard_stats,
    )
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    get_connection    = None
    def get_dashboard_stats() -> dict: return {}

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
_WA_GREEN   = "#25D366"
_TEXT_PRI   = "#f1f5f9"
_TEXT_SEC   = "#94a3b8"
_TEXT_MUT   = "#475569"
_BORDER     = "rgba(255,255,255,0.05)"
_BORDER_MID = "rgba(255,255,255,0.08)"
_BORDER_BRD = "rgba(91,140,255,0.28)"
_GLOW_BLUE  = "rgba(91,140,255,0.18)"

# ─────────────────────────────────────────────────────────
# Page CSS — unchanged from v9.7
# ─────────────────────────────────────────────────────────
def _render_page_css() -> None:
    st.markdown(f"""
<style>
.block-container {{
    padding-top: 1.4rem !important;
    padding-bottom: 3rem !important;
    max-width: 1440px !important;
}}
.rp-hero {{
    background: linear-gradient(145deg,
        {_BG_SURFACE} 0%, {_BG_RAISED} 50%, {_BG_SURFACE} 100%);
    border: 1px solid {_BORDER};
    border-left: 4px solid {_BRAND_BLUE};
    border-radius: 20px;
    padding: 24px 28px;
    margin-bottom: 24px;
    position: relative;
    overflow: hidden;
}}
.rp-hero::before {{
    content: '';
    position: absolute;
    top: -80px; right: -80px;
    width: 280px; height: 280px;
    background: radial-gradient(
        circle, {_GLOW_BLUE} 0%, transparent 70%);
    pointer-events: none;
}}
.rp-hero-eyebrow {{
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 2px;
    color: {_BRAND_BLUE}; margin-bottom: 8px;
}}
.rp-hero-title {{
    font-size: 30px; font-weight: 900;
    color: {_TEXT_PRI}; letter-spacing: -1px;
    line-height: 1.15; margin: 0 0 8px 0;
}}
.rp-hero-sub {{
    font-size: 13px; color: {_TEXT_MUT};
    font-weight: 500; line-height: 1.7;
}}
.rp-sec {{ margin: 0 0 16px 0; }}
.rp-sec-title {{
    font-size: 20px; font-weight: 800;
    color: {_TEXT_PRI}; letter-spacing: -0.5px;
    line-height: 1.2; margin: 0 0 4px 0;
}}
.rp-sec-sub {{
    font-size: 12px; color: {_TEXT_MUT};
    font-weight: 500; margin: 0;
}}
.rp-kpi {{
    background: linear-gradient(
        145deg, {_BG_SURFACE} 0%, {_BG_RAISED} 100%);
    border: 1px solid {_BORDER};
    border-radius: 18px;
    padding: 18px 18px 14px 18px;
    position: relative; overflow: hidden;
    transition: transform 0.22s ease, border-color 0.25s ease,
                box-shadow 0.25s ease;
    cursor: default;
}}
.rp-kpi:hover {{
    transform: translateY(-3px);
    border-color: {_BORDER_BRD};
    box-shadow: 0 14px 40px rgba(91,140,255,0.14),
                0 4px 12px rgba(0,0,0,0.3);
}}
.rp-kpi-accent {{
    position: absolute; top: 0; left: 0; right: 0;
    height: 3px; border-radius: 18px 18px 0 0;
}}
.rp-kpi-icon {{
    font-size: 22px; margin-bottom: 10px; line-height: 1;
}}
.rp-kpi-label {{
    color: {_TEXT_MUT}; font-size: 9.5px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1.2px;
    margin-bottom: 6px;
}}
.rp-kpi-value {{
    font-size: 28px; font-weight: 900; color: {_TEXT_PRI};
    letter-spacing: -1px; line-height: 1; margin-bottom: 6px;
}}
.rp-kpi-sub {{ font-size: 11px; font-weight: 600; }}
.rp-stat-card {{
    background: linear-gradient(
        145deg, {_BG_SURFACE} 0%, {_BG_RAISED} 100%);
    border: 1px solid {_BORDER};
    border-radius: 16px;
    padding: 16px 18px;
    margin-bottom: 10px;
    transition: transform 0.2s ease, border-color 0.25s ease,
                box-shadow 0.25s ease;
}}
.rp-stat-card:hover {{
    transform: translateY(-2px);
    border-color: {_BORDER_BRD};
    box-shadow: 0 10px 32px rgba(91,140,255,0.12);
}}
.rp-bar-row {{
    display: flex; align-items: center;
    gap: 12px; padding: 10px 0;
    border-bottom: 1px solid {_BORDER};
}}
.rp-bar-row:last-child {{ border-bottom: none; }}
.rp-bar-label {{
    width: 140px; font-size: 12px; font-weight: 600;
    color: {_TEXT_SEC}; flex-shrink: 0;
    white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
}}
.rp-bar-track {{
    flex: 1; height: 8px;
    background: rgba(255,255,255,0.05);
    border-radius: 999px; overflow: hidden;
}}
.rp-bar-fill {{
    height: 100%; border-radius: 999px;
    transition: width 0.4s ease;
}}
.rp-bar-count {{
    width: 40px; text-align: right;
    font-size: 13px; font-weight: 800;
    color: {_TEXT_PRI}; flex-shrink: 0;
}}
.rp-bar-pct {{
    width: 38px; text-align: right;
    font-size: 11px; color: {_TEXT_MUT};
    flex-shrink: 0;
}}
.rp-drill {{
    background: linear-gradient(
        145deg, {_BG_SURFACE} 0%, {_BG_RAISED} 100%);
    border: 1px solid {_BORDER};
    border-radius: 18px;
    padding: 22px;
    margin-bottom: 14px;
}}
.rp-drill-name {{
    font-size: 22px; font-weight: 900;
    color: {_TEXT_PRI}; letter-spacing: -0.5px;
    margin-bottom: 6px;
}}
.rp-drill-meta {{
    font-size: 12px; color: {_TEXT_MUT}; margin-bottom: 14px;
}}
.rp-drill-grid {{
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 10px; margin-bottom: 14px;
}}
.rp-drill-cell {{
    background: rgba(255,255,255,0.025);
    border: 1px solid {_BORDER};
    border-radius: 12px; padding: 11px 13px;
}}
.rp-drill-cell-label {{
    color: {_TEXT_MUT}; font-size: 9.5px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 5px;
}}
.rp-drill-cell-value {{
    color: {_TEXT_PRI}; font-size: 13px;
    font-weight: 600; word-break: break-word;
}}
.rp-drill-score-row {{
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 8px; margin-bottom: 14px;
}}
.rp-drill-score {{
    background: rgba(255,255,255,0.025);
    border: 1px solid {_BORDER};
    border-radius: 12px; padding: 10px;
    text-align: center;
}}
.rp-drill-score-num {{
    font-size: 20px; font-weight: 900;
    color: {_TEXT_PRI}; line-height: 1; margin-bottom: 4px;
}}
.rp-drill-score-lbl {{
    font-size: 9px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.8px;
    color: {_TEXT_MUT};
}}
.rp-badge {{
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 9px; border-radius: 999px;
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.5px;
    white-space: nowrap;
}}
.rp-prog-track {{
    height: 6px; background: rgba(255,255,255,0.05);
    border-radius: 999px; overflow: hidden; margin-top: 6px;
}}
.rp-prog-fill {{
    height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, {_BRAND_BLUE}, {_BRAND_PUR});
}}
.rp-export-card {{
    background: linear-gradient(
        145deg, {_BG_SURFACE} 0%, {_BG_RAISED} 100%);
    border: 1px solid {_BORDER};
    border-left: 3px solid {_BRAND_BLUE};
    border-radius: 0 14px 14px 0;
    padding: 16px 18px;
    margin-bottom: 12px;
    transition: border-color 0.25s ease, transform 0.2s ease;
}}
.rp-export-card:hover {{
    border-left-color: {_BRAND_PUR};
    transform: translateX(3px);
}}
.rp-export-title {{
    font-size: 14px; font-weight: 800;
    color: {_TEXT_PRI}; margin-bottom: 4px;
}}
.rp-export-desc {{
    font-size: 12px; color: {_TEXT_MUT}; margin-bottom: 10px;
}}
.rp-revenue {{
    background: linear-gradient(135deg,
        rgba(245,158,11,0.08), rgba(245,158,11,0.03));
    border: 1px solid rgba(245,158,11,0.20);
    border-left: 4px solid {_GOLD};
    border-radius: 0 16px 16px 0;
    padding: 18px 22px;
    margin-bottom: 16px;
}}
.rp-revenue-label {{
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1.5px;
    color: {_GOLD}; margin-bottom: 6px; opacity: 0.85;
}}
.rp-revenue-value {{
    font-size: 36px; font-weight: 900;
    color: {_TEXT_PRI}; letter-spacing: -1.5px; line-height: 1;
}}
.rp-revenue-sub {{
    font-size: 12px; color: {_GOLD};
    margin-top: 6px; font-weight: 600; opacity: 0.7;
}}
.rp-empty {{
    text-align: center; padding: 40px 24px;
    background: rgba(255,255,255,0.02);
    border: 1px dashed {_BORDER_MID};
    border-radius: 16px; margin: 16px 0;
}}
.rp-empty-icon {{
    font-size: 32px; margin-bottom: 12px;
}}
.rp-empty-title {{
    font-size: 15px; font-weight: 700;
    color: {_TEXT_SEC}; margin-bottom: 6px;
}}
.rp-empty-sub {{ font-size: 13px; color: {_TEXT_MUT}; }}
.stTabs [data-baseweb="tab-list"] {{
    gap: 6px; border-bottom: 1px solid {_BORDER};
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent !important;
    border-radius: 10px 10px 0 0 !important;
    padding: 10px 18px !important;
    color: {_TEXT_MUT} !important;
    border: none !important;
    font-weight: 600 !important; font-size: 13px !important;
    transition: color 0.2s ease, background 0.2s ease !important;
}}
.stTabs [data-baseweb="tab"]:hover {{
    color: {_TEXT_SEC} !important;
    background: rgba(91,140,255,0.06) !important;
}}
.stTabs [aria-selected="true"] {{
    background: rgba(91,140,255,0.10) !important;
    color: {_BRAND_BLUE} !important;
    font-weight: 700 !important;
    border-bottom: 2px solid {_BRAND_BLUE} !important;
}}
.stButton > button {{
    border-radius: 12px !important; border: none !important;
    background: linear-gradient(
        135deg, {_BRAND_BLUE}, {_BRAND_PUR}) !important;
    color: white !important; font-weight: 700 !important;
    font-size: 13px !important; padding: 11px 18px !important;
    transition: opacity 0.2s ease, transform 0.15s ease,
                box-shadow 0.25s ease !important;
    width: 100%;
}}
.stButton > button:hover {{
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 28px rgba(91,140,255,0.32) !important;
    opacity: 0.94 !important;
}}
.stDownloadButton > button {{
    border-radius: 10px !important;
    background: transparent !important;
    border: 1px solid {_BORDER_BRD} !important;
    color: {_BRAND_BLUE} !important;
    font-weight: 700 !important; font-size: 12px !important;
    padding: 9px 14px !important; width: 100%;
}}
.stDownloadButton > button:hover {{
    background: rgba(91,140,255,0.08) !important;
    box-shadow: 0 4px 16px rgba(91,140,255,0.15) !important;
}}
.stTextInput input, .stNumberInput input,
.stSelectbox > div > div {{
    background: {_BG_INPUT} !important;
    border: 1px solid {_BORDER_MID} !important;
    border-radius: 10px !important;
    color: {_TEXT_PRI} !important; font-size: 13px !important;
}}
.stTextInput input:focus, .stNumberInput input:focus {{
    border-color: {_BRAND_BLUE} !important;
    box-shadow: 0 0 0 3px rgba(91,140,255,0.15) !important;
}}
.stTextInput label, .stNumberInput label,
.stSelectbox label {{
    color: {_TEXT_MUT} !important; font-size: 10px !important;
    font-weight: 700 !important; text-transform: uppercase !important;
    letter-spacing: 0.8px !important;
}}
.stExpander {{
    background: {_BG_SURFACE} !important;
    border: 1px solid {_BORDER} !important;
    border-radius: 14px !important;
}}
.stExpander:hover {{ border-color: {_BORDER_BRD} !important; }}
.stExpander summary {{
    color: {_TEXT_SEC} !important; font-weight: 600 !important;
    font-size: 13px !important; padding: 12px 16px !important;
}}
.stDataFrame {{
    border-radius: 14px !important; overflow: hidden !important;
    border: 1px solid {_BORDER} !important;
}}
.stDataFrame th {{
    background: {_BG_RAISED} !important;
    color: {_TEXT_MUT} !important;
    font-size: 10px !important; font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.8px !important;
    border-bottom: 1px solid {_BORDER} !important;
    padding: 10px 14px !important;
}}
.stDataFrame td {{
    background: {_BG_SURFACE} !important;
    color: {_TEXT_PRI} !important;
    font-size: 12px !important; padding: 9px 14px !important;
    border-bottom: 1px solid {_BORDER} !important;
}}
.stDataFrame tr:hover td {{
    background: {_BG_RAISED} !important;
}}
[data-testid="stMetric"] {{
    background: linear-gradient(145deg, {_BG_SURFACE}, {_BG_RAISED});
    border: 1px solid {_BORDER};
    border-radius: 12px; padding: 12px 14px;
}}
[data-testid="stMetricValue"] {{
    color: {_TEXT_PRI} !important; font-weight: 900 !important;
    font-size: 20px !important;
}}
[data-testid="stMetricLabel"] {{
    color: {_TEXT_MUT} !important; font-size: 9.5px !important;
    font-weight: 700 !important; text-transform: uppercase !important;
    letter-spacing: 1px !important;
}}
.stProgress > div > div > div {{
    background: linear-gradient(
        90deg, {_BRAND_BLUE}, {_BRAND_PUR}) !important;
    border-radius: 999px !important;
}}
.stProgress > div > div {{
    background: rgba(255,255,255,0.05) !important;
    border-radius: 999px !important; height: 6px !important;
}}
.stSuccess {{
    background: rgba(16,185,129,0.08)  !important;
    border-left: 3px solid {_GREEN}    !important;
    border-radius: 10px !important;
}}
.stError {{
    background: rgba(239,68,68,0.08)   !important;
    border-left: 3px solid {_RED}      !important;
    border-radius: 10px !important;
}}
.stWarning {{
    background: rgba(245,158,11,0.08)  !important;
    border-left: 3px solid {_GOLD}     !important;
    border-radius: 10px !important;
}}
.stInfo {{
    background: rgba(91,140,255,0.08)  !important;
    border-left: 3px solid {_BRAND_BLUE} !important;
    border-radius: 10px !important;
}}
::-webkit-scrollbar {{ width: 4px; height: 4px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
    background: {_BG_RAISED}; border-radius: 20px;
}}
::-webkit-scrollbar-thumb:hover {{ background: {_BRAND_BLUE}; }}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Helpers — unchanged
# ─────────────────────────────────────────────────────────
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

def _fmt_money(value) -> str:
    try:
        if isinstance(value, str) and _norm(value) in (
            "", "none", "null"
        ):
            return "₦0"
        v = float(value or 0)
        return f"₦{int(v):,}" if v == int(v) else f"₦{v:,.2f}"
    except Exception:
        return "₦0"

def _fmt_pct(num: int, denom: int) -> str:
    if not denom: return "0%"
    return f"{round(num / denom * 100, 1)}%"

def _sanitize_key(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(s or "unknown"))[:40]

def _parse_json_list(value) -> list:
    if isinstance(value, list): return value
    if not value: return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
            if isinstance(parsed, list): return parsed
        except Exception: pass
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(value)]

def _section(title: str, subtitle: str = "") -> None:
    sub = (
        f"<div class='rp-sec-sub'>{_esc(subtitle)}</div>"
        if subtitle else ""
    )
    st.markdown(f"""
<div class="rp-sec">
    <div class="rp-sec-title">{_esc(title)}</div>
    {sub}
</div>
""", unsafe_allow_html=True)

def _badge(label: str, color: str, bg: str = "") -> str:
    if not bg:
        bg = color + "1a"
    return (
        f"<span class='rp-badge' style='"
        f"background:{bg};color:{color};"
        f"border:1px solid {color}33;'>{_esc(label)}</span>"
    )

def _kpi(
    icon: str, label: str, value: str,
    sub: str, accent: str,
) -> None:
    st.markdown(f"""
<div class="rp-kpi">
    <div class="rp-kpi-accent" style="background:{accent};"></div>
    <div class="rp-kpi-icon">{icon}</div>
    <div class="rp-kpi-label">{_esc(label)}</div>
    <div class="rp-kpi-value">{_esc(value)}</div>
    <div class="rp-kpi-sub" style="color:{accent};">
        {_esc(sub)}
    </div>
</div>
""", unsafe_allow_html=True)

def _empty(icon: str, title: str, sub: str) -> None:
    st.markdown(f"""
<div class="rp-empty">
    <div class="rp-empty-icon">{icon}</div>
    <div class="rp-empty-title">{_esc(title)}</div>
    <div class="rp-empty-sub">{_esc(sub)}</div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# DB query helpers — unchanged
# ─────────────────────────────────────────────────────────
def _query(sql: str, params: tuple = ()) -> list:
    if get_connection is None: return []
    conn = None
    try:
        conn = get_connection()
        conn.row_factory = __import__("sqlite3").Row
        cur  = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception: return []
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def _scalar(sql: str, params: tuple = (), default=0):
    if get_connection is None: return default
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception: return default
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def _get_overview_stats() -> dict:
    try:
        stats = get_dashboard_stats() or {}
    except Exception:
        stats = {}
    return stats

def _get_leads_over_time() -> list:
    return _query("""
        SELECT DATE(created_at) AS day, COUNT(*) AS total
        FROM leads
        WHERE created_at IS NOT NULL
        GROUP BY day ORDER BY day ASC
        LIMIT 90
    """)

def _get_scoring_breakdown() -> dict:
    rows = _query("""
        SELECT
            CASE
                WHEN fit_score IS NULL OR lead_priority IS NULL
                     OR lead_priority IN ('','unscored') THEN 'Unscored'
                WHEN fit_score >= 8 THEN 'High'
                WHEN fit_score >= 5 THEN 'Medium'
                ELSE 'Low'
            END AS bucket,
            COUNT(*) AS total
        FROM leads
        GROUP BY bucket
    """)
    return {r["bucket"]: r["total"] for r in rows}

def _get_contact_completeness_dist() -> dict:
    rows = _query("""
        SELECT
            (CASE WHEN phone IS NOT NULL
                       AND TRIM(phone) != '' THEN 1 ELSE 0 END
           + CASE WHEN email IS NOT NULL
                       AND TRIM(email) != '' THEN 1 ELSE 0 END
           + CASE WHEN website IS NOT NULL
                       AND TRIM(website) != '' THEN 1 ELSE 0 END
           + CASE WHEN address IS NOT NULL
                       AND TRIM(address) != '' THEN 1 ELSE 0 END
            ) AS field_count,
            COUNT(*) AS total
        FROM leads
        GROUP BY field_count ORDER BY field_count DESC
    """)
    labels = {
        0: "None",
        1: "Minimal (1/4)",
        2: "Partial (2/4)",
        3: "Good (3/4)",
        4: "Complete (4/4)",
    }
    return {
        labels.get(r["field_count"], str(r["field_count"])): r["total"]
        for r in rows
    }

def _get_wa_breakdown() -> dict:
    rows = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(wa_status),''), 'unknown') AS ws,
            COUNT(*) AS total
        FROM leads GROUP BY ws ORDER BY total DESC
    """)
    return {r["ws"]: r["total"] for r in rows}

def _get_channel_breakdown() -> dict:
    rows = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(best_channel),''),
                     'unassigned') AS ch,
            COUNT(*) AS total
        FROM leads GROUP BY ch ORDER BY total DESC
    """)
    return {r["ch"]: r["total"] for r in rows}

def _get_source_breakdown() -> dict:
    rows = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(source),''), 'unknown') AS src,
            COUNT(*) AS total
        FROM leads GROUP BY src ORDER BY total DESC
    """)
    return {r["src"]: r["total"] for r in rows}

def _get_country_breakdown(limit: int = 10) -> list:
    return _query("""
        SELECT
            COALESCE(NULLIF(TRIM(country),''), 'Unknown') AS name,
            COUNT(*) AS total
        FROM leads GROUP BY name ORDER BY total DESC LIMIT ?
    """, (limit,))

def _get_market_breakdown() -> list:
    return _query("""
        SELECT
            COALESCE(NULLIF(TRIM(market),''), 'Unknown') AS name,
            COUNT(*) AS total
        FROM leads GROUP BY name ORDER BY total DESC
    """)

def _get_city_breakdown(limit: int = 10) -> list:
    return _query("""
        SELECT
            COALESCE(NULLIF(TRIM(city),''), 'Unknown') AS name,
            COUNT(*) AS total
        FROM leads GROUP BY name ORDER BY total DESC LIMIT ?
    """, (limit,))

def _get_outreach_history(limit: int = 200) -> list:
    return _query("""
        SELECT
            o.id, o.sent_at, o.channel, o.status, o.message_text,
            l.business_name, l.industry, l.city, l.country,
            l.fit_score, l.lead_priority, l.best_channel
        FROM outreach_log o
        LEFT JOIN leads l ON o.lead_id = l.id
        ORDER BY o.sent_at DESC LIMIT ?
    """, (limit,))

def _get_outreach_summary() -> dict:
    rows = _query("""
        SELECT channel, COUNT(*) AS total
        FROM outreach_log GROUP BY channel
    """)
    return {r["channel"]: r["total"] for r in rows}

def _get_revenue_rows(limit: int = 100) -> list:
    return _query("""
        SELECT
            r.id, r.business_name, r.amount, r.service_type,
            r.payment_status, r.closed_at,
            l.city, l.country, l.market,
            l.fit_score, l.lead_priority
        FROM revenue r
        LEFT JOIN leads l ON r.lead_id = l.id
        ORDER BY r.closed_at DESC LIMIT ?
    """, (limit,))

def _get_revenue_total() -> float:
    return _safe_float(_scalar("SELECT SUM(amount) FROM revenue"))

def _get_deals_count() -> int:
    return _safe_int(_scalar("SELECT COUNT(*) FROM revenue"))

def _get_lead_by_id(lead_id: int) -> Optional[dict]:
    rows = _query(
        "SELECT * FROM leads WHERE id = ? LIMIT 1", (lead_id,)
    )
    return rows[0] if rows else None

def _get_pipeline_summary() -> dict:
    rows = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(contact_status),''), 'new') AS st,
            COUNT(*) AS total
        FROM leads GROUP BY st
    """)
    return {r["st"]: r["total"] for r in rows}

def _get_enrichment_summary() -> dict:
    rows = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(enrichment_status),''),
                     'pending') AS es,
            COUNT(*) AS total
        FROM leads GROUP BY es
    """)
    return {r["es"]: r["total"] for r in rows}

def _get_all_leads_for_export(status_filter: str = "all") -> list:
    if status_filter == "all":
        return _query("SELECT * FROM leads ORDER BY id DESC")
    if status_filter == "whatsapp":
        return _query("""
            SELECT * FROM leads
            WHERE wa_status IN (
                'verified','estimated','opted_in',
                'business_confirmed'
            )
            AND phone IS NOT NULL AND TRIM(phone) != ''
            ORDER BY fit_score DESC
        """)
    if status_filter == "high_fit":
        return _query("""
            SELECT * FROM leads WHERE fit_score >= 8
            ORDER BY fit_score DESC
        """)
    if status_filter == "email":
        return _query("""
            SELECT * FROM leads
            WHERE email IS NOT NULL AND TRIM(email) != ''
            ORDER BY fit_score DESC
        """)
    if status_filter == "called":
        return _query("""
            SELECT * FROM leads
            WHERE contact_status IN ('called','callback','interested')
            ORDER BY id DESC
        """)
    return _query("SELECT * FROM leads ORDER BY id DESC")


# ─────────────────────────────────────────────────────────
# Bar chart component — unchanged
# ─────────────────────────────────────────────────────────
def _bar_chart(
    data: dict,
    title: str,
    color: str = _BRAND_BLUE,
    show_pct: bool = True,
    max_items: int = 10,
) -> None:
    if not data:
        _empty(
            "📊", f"No {title.lower()} data yet",
            "Run searches to populate this chart.",
        )
        return

    total = sum(data.values())
    items = sorted(
        data.items(), key=lambda x: x[1], reverse=True
    )[:max_items]
    max_v = items[0][1] if items else 1

    rows_html = ""
    for label, count in items:
        bar_w = round(count / max_v * 100, 1)
        pct   = _fmt_pct(count, total)
        rows_html += f"""
<div class="rp-bar-row">
    <div class="rp-bar-label">
        {_esc(str(label).replace("_"," ").title())}
    </div>
    <div class="rp-bar-track">
        <div class="rp-bar-fill"
             style="width:{bar_w}%;background:{color};">
        </div>
    </div>
    <div class="rp-bar-count">{count:,}</div>
    {"<div class='rp-bar-pct'>" + pct + "</div>"
     if show_pct else ""}
</div>
"""

    st.markdown(f"""
<div class="rp-stat-card">
    <div style="font-size:10px;font-weight:700;
    color:{_TEXT_SEC};margin-bottom:14px;
    text-transform:uppercase;letter-spacing:1px;">
        {_esc(title)}
    </div>
    {rows_html}
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Sparkline — unchanged
# ─────────────────────────────────────────────────────────
def _sparkline(
    data: list,
    color: str = _BRAND_BLUE,
    height: int = 60,
) -> str:
    if len(data) < 2:
        return ""
    vals  = [_safe_float(d.get("total", 0)) for d in data]
    mn, mx = min(vals), max(vals)
    rng   = mx - mn or 1
    w, h  = 600, height
    pts   = []
    for i, v in enumerate(vals):
        x = round(i / (len(vals) - 1) * w, 1)
        y = round(h - (v - mn) / rng * (h - 8) - 4, 1)
        pts.append(f"{x},{y}")
    polyline  = " ".join(pts)
    area_pts  = f"0,{h} " + polyline + f" {w},{h}"
    return f"""
<svg viewBox="0 0 {w} {h}"
     xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:{h}px;display:block;">
    <defs>
        <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stop-color="{color}"
                  stop-opacity="0.25"/>
            <stop offset="100%" stop-color="{color}"
                  stop-opacity="0.02"/>
        </linearGradient>
    </defs>
    <polygon points="{area_pts}" fill="url(#sg)"/>
    <polyline points="{polyline}"
              fill="none" stroke="{color}"
              stroke-width="2" stroke-linejoin="round"
              stroke-linecap="round"/>
</svg>
"""


# ─────────────────────────────────────────────────────────
# v9.9 NEW HELPERS
# ─────────────────────────────────────────────────────────

# ── v9.9 Helper 1: DM Coverage Stat ──────────────────────
def _render_dm_coverage_stat(stats: dict) -> None:
    """
    Show decision maker coverage: total / with_dm / percentage.
    Uses st.progress() with explicit float (Rule 1).
    Called from render_overview().
    Only shown if total_leads > 0.
    """
    total   = _safe_int(stats.get("total_leads", 0))
    with_dm = _safe_int(stats.get("leads_with_decision_maker", 0))

    if total == 0:
        return

    pct = round((with_dm / total) * 100, 1)

    _section(
        "Decision Maker Coverage",
        "What percentage of leads have a named contact person.",
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Leads",         f"{total:,}")
    col2.metric("Decision Maker Found", f"{with_dm:,}")
    col3.metric("DM Coverage",          f"{pct}%")

    # Rule 1: st.progress() MUST receive a float 0.0–1.0
    progress_val = float(with_dm / total)
    progress_val = max(0.0, min(1.0, progress_val))
    st.progress(progress_val)

    st.markdown(
        "<div style='height:8px'></div>", unsafe_allow_html=True
    )


# ── v9.9 Helper 2: DM Source Breakdown ───────────────────
def _render_dm_source_breakdown(stats: dict) -> None:
    """
    Show where DM names came from:
      CQC Register / Companies House / Website / Google Maps
    Uses dm_by_source from get_dashboard_stats().
    Only shown if any DM names exist.
    Called from render_overview().
    """
    dm_by_source = stats.get("dm_by_source", {})

    if not dm_by_source:
        return

    # Map raw db values to readable labels
    source_labels = {
        "cqc":             "CQC Register",
        "companies_house": "Companies House",
        "website":         "Website Parse",
        "google_maps":     "Google Maps",
        "not_found":       "Not Found",
    }
    source_colors = {
        "cqc":             _GOLD,
        "companies_house": "#3b82f6",
        "website":         _GREEN,
        "google_maps":     _BRAND_BLUE,
        "not_found":       _TEXT_MUT,
    }

    # Remap keys to readable labels
    display_data = {}
    for raw_key, count in dm_by_source.items():
        label = source_labels.get(raw_key, raw_key.replace("_", " ").title())
        color = source_colors.get(raw_key, _TEXT_MUT)
        display_data[label] = (count, color)

    total_dm = sum(v[0] for v in display_data.values())

    rows_html = ""
    for label, (count, color) in sorted(
        display_data.items(),
        key=lambda x: x[1][0],
        reverse=True,
    ):
        bar_w = round(count / total_dm * 100, 1) if total_dm else 0
        pct   = _fmt_pct(count, total_dm)
        rows_html += f"""
<div class="rp-bar-row">
    <div class="rp-bar-label">{_esc(label)}</div>
    <div class="rp-bar-track">
        <div class="rp-bar-fill"
             style="width:{bar_w}%;background:{color};">
        </div>
    </div>
    <div class="rp-bar-count">{count:,}</div>
    <div class="rp-bar-pct">{pct}</div>
</div>
"""

    st.markdown(f"""
<div class="rp-stat-card">
    <div style="font-size:10px;font-weight:700;
    color:{_TEXT_SEC};margin-bottom:14px;
    text-transform:uppercase;letter-spacing:1px;">
        Decision Maker Source Breakdown
    </div>
    {rows_html}
</div>
""", unsafe_allow_html=True)


# ── v9.9 Helper 3: CQC Rating Breakdown ──────────────────
def _render_cqc_rating_breakdown(stats: dict) -> None:
    """
    Show CQC rating distribution for UK care home leads.
    Only shown if CQC data exists in the database.
    Uses by_cqc_rating from get_dashboard_stats().
    Called from render_overview().
    """
    by_cqc_rating = stats.get("by_cqc_rating", {})

    if not by_cqc_rating:
        return   # No CQC data yet — don't show section at all

    _section(
        "CQC Rating Breakdown",
        "Quality distribution of UK care home leads "
        "(from CQC public register).",
    )

    rating_config = {
        "Outstanding":          ("🟢", _GREEN),
        "Good":                 ("🔵", _BRAND_BLUE),
        "Requires Improvement": ("🟡", _GOLD),
        "Inadequate":           ("🔴", _RED),
    }

    total_cqc = sum(by_cqc_rating.values())

    cols = st.columns(min(len(by_cqc_rating), 4))

    # Sort by a defined order (not alphabetically)
    order = [
        "Outstanding", "Good",
        "Requires Improvement", "Inadequate",
    ]
    sorted_ratings = sorted(
        by_cqc_rating.items(),
        key=lambda x: order.index(x[0])
        if x[0] in order else 99,
    )

    for col, (rating, count) in zip(cols, sorted_ratings):
        emoji, color = rating_config.get(
            rating, ("⚪", _TEXT_MUT)
        )
        pct = _fmt_pct(count, total_cqc)
        with col:
            st.markdown(f"""
<div class="rp-stat-card" style="text-align:center;
border-top:3px solid {color};">
    <div style="font-size:20px;margin-bottom:8px;">
        {emoji}
    </div>
    <div style="font-size:9.5px;font-weight:700;
    text-transform:uppercase;letter-spacing:1px;
    color:{_TEXT_MUT};margin-bottom:6px;">
        {_esc(rating)}
    </div>
    <div style="font-size:24px;font-weight:900;
    color:{color};line-height:1;">{count:,}</div>
    <div style="font-size:11px;color:{_TEXT_MUT};
    margin-top:4px;">{pct} of CQC leads</div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:8px'></div>", unsafe_allow_html=True
    )


# ─────────────────────────────────────────────────────────
# Hero — v9.9: version string updated
# ─────────────────────────────────────────────────────────
def render_hero() -> None:
    now   = datetime.now()
    total = _safe_int(_scalar("SELECT COUNT(*) FROM leads"))
    rev   = _fmt_money(_get_revenue_total())
    st.markdown(f"""
<div class="rp-hero">
    <div style="display:flex;justify-content:space-between;
    align-items:flex-start;gap:24px;flex-wrap:wrap;">
        <div style="flex:1;min-width:260px;">
            <div class="rp-hero-eyebrow">
                Analytics &amp; Reports
                &middot; KNCS LeadGen Pro v9.9
            </div>
            <div class="rp-hero-title">
                Performance Intelligence
            </div>
            <div class="rp-hero-sub">
                Full pipeline analytics — scoring breakdown,
                geography, WhatsApp coverage, channel
                readiness, CQC care home data and revenue
                tracking.
                <br>
                <span style="color:{_TEXT_SEC};">
                    {_esc(now.strftime("%A, %d %B %Y, %H:%M"))}
                </span>
            </div>
        </div>
        <div style="text-align:right;">
            <div style="font-size:10px;font-weight:700;
            text-transform:uppercase;letter-spacing:1.5px;
            color:{_TEXT_MUT};margin-bottom:6px;">
                Total Leads
            </div>
            <div style="font-size:36px;font-weight:900;
            color:{_TEXT_PRI};letter-spacing:-1.5px;
            line-height:1;">
                {total:,}
            </div>
            <div style="font-size:12px;color:{_GOLD};
            margin-top:6px;font-weight:600;">
                {rev} revenue tracked
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# TAB 1 — Overview — v9.9: 3 new stat blocks added at bottom
# ─────────────────────────────────────────────────────────
def render_overview(stats: dict) -> None:
    _section("Overview", "Core pipeline metrics at a glance.")

    total    = _safe_int(stats.get("total_leads",              0))
    high     = _safe_int(stats.get("high_fit_leads",           0))
    medium   = _safe_int(stats.get("medium_fit_leads",         0))
    low      = _safe_int(stats.get("low_fit_leads",            0))
    unscored = _safe_int(stats.get("unscored_leads",           0))
    wa_ready = _safe_int(stats.get("whatsapp_ready",           0))
    wa_ver   = _safe_int(stats.get("whatsapp_verified",        0))
    email_r  = _safe_int(stats.get("email_ready",              0))
    phone_r  = _safe_int(stats.get("leads_with_phone",         0))
    dm_count = _safe_int(stats.get("leads_with_decision_maker",0))
    deals    = _get_deals_count()
    revenue  = _get_revenue_total()

    r1 = st.columns(6)
    kpis_r1 = [
        ("📊", "Total Leads",  f"{total:,}",    "In database",          _BRAND_BLUE),
        ("🎯", "High Fit",     f"{high:,}",     _fmt_pct(high, total),  _GREEN),
        ("🟡", "Medium Fit",   f"{medium:,}",   _fmt_pct(medium, total),_GOLD),
        ("🔴", "Low Fit",      f"{low:,}",      _fmt_pct(low, total),   _RED),
        ("🧠", "Unscored",     f"{unscored:,}", "Need scoring",         _TEXT_MUT),
        ("💰", "Deals Closed", f"{deals:,}",    _fmt_money(revenue),    _GOLD),
    ]
    for col, (icon, label, val, sub, accent) in zip(r1, kpis_r1):
        with col: _kpi(icon, label, val, sub, accent)

    st.markdown(
        "<div style='height:10px'></div>", unsafe_allow_html=True
    )

    r2 = st.columns(6)
    kpis_r2 = [
        ("💬", "WA Ready",
         f"{wa_ready:,}", "All WA-ready", _WA_GREEN),
        ("✅", "WA Verified",
         f"{wa_ver:,}", "Agent confirmed", _GREEN),
        ("📧", "Email Ready",
         f"{email_r:,}", _fmt_pct(email_r, total), _BRAND_BLUE),
        ("📞", "Has Phone",
         f"{phone_r:,}", _fmt_pct(phone_r, total), "#38bdf8"),
        ("👤", "Decision Makers",
         f"{dm_count:,}", _fmt_pct(dm_count, total), _BRAND_PUR),
        ("📈", "Contact Rate",
         _fmt_pct(
             _safe_int(stats.get("contacted_leads", 0)), total
         ),
         "Outreach done", _ORANGE),
    ]
    for col, (icon, label, val, sub, accent) in zip(r2, kpis_r2):
        with col: _kpi(icon, label, val, sub, accent)

    st.markdown(
        "<div style='height:20px'></div>", unsafe_allow_html=True
    )

    st.markdown(f"""
<div class="rp-revenue">
    <div class="rp-revenue-label">💰 Total Revenue Tracked</div>
    <div class="rp-revenue-value">{_fmt_money(revenue)}</div>
    <div class="rp-revenue-sub">
        {deals} deals closed &middot;
        avg {_fmt_money(revenue / deals if deals else 0)} per deal
    </div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:10px'></div>", unsafe_allow_html=True
    )
    _section(
        "Pipeline Funnel",
        "Lead progression from captured to closed.",
    )

    pipeline = _get_pipeline_summary()
    funnel_steps = [
        ("📋", "Captured",  total,             _BRAND_BLUE),
        ("🧠", "Scored",    total - unscored,   _BRAND_PUR),
        ("💬", "WA Ready",  wa_ready,           _WA_GREEN),
        ("📞", "Contacted",
         _safe_int(pipeline.get("called",     0))
         + _safe_int(pipeline.get("callback", 0))
         + _safe_int(pipeline.get("interested", 0)),
         _GOLD),
        ("✅", "Interested",
         _safe_int(pipeline.get("interested", 0)), _GREEN),
        ("💰", "Closed",
         _safe_int(pipeline.get("deal_closed", 0)), _RED),
    ]

    cols = st.columns(len(funnel_steps))
    for col, (icon, label, val, color) in zip(cols, funnel_steps):
        with col:
            pct_of_total = _fmt_pct(val, total)
            st.markdown(f"""
<div class="rp-stat-card" style="text-align:center;
border-top:3px solid {color};">
    <div style="font-size:24px;margin-bottom:8px;">{icon}</div>
    <div style="font-size:9.5px;font-weight:700;
    text-transform:uppercase;letter-spacing:1px;
    color:{_TEXT_MUT};margin-bottom:6px;">{_esc(label)}</div>
    <div style="font-size:24px;font-weight:900;
    color:{color};line-height:1;">{val:,}</div>
    <div style="font-size:11px;color:{_TEXT_MUT};
    margin-top:4px;">{pct_of_total} of total</div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:20px'></div>", unsafe_allow_html=True
    )

    time_data = _get_leads_over_time()
    if time_data:
        _section(
            "Lead Acquisition Trend",
            "New leads added over last 90 days.",
        )
        spark = _sparkline(time_data, color=_BRAND_BLUE, height=80)
        if spark:
            first_day    = time_data[0].get("day", "")
            last_day     = time_data[-1].get("day", "")
            total_period = sum(d.get("total", 0) for d in time_data)
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:12px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_SEC};">
            {_esc(first_day)} → {_esc(last_day)}
        </div>
        <div style="font-size:13px;font-weight:800;
        color:{_TEXT_PRI};">{total_period:,} leads total</div>
    </div>
    {spark}
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:10px'></div>", unsafe_allow_html=True
    )

    enrich = _get_enrichment_summary()
    if enrich:
        enrich_colors = {
            "done":      _GREEN,
            "attempted": _GOLD,
            "pending":   _TEXT_MUT,
            "failed":    _RED,
        }
        _section(
            "Enrichment Status",
            "How well contact data has been filled in.",
        )
        ec = st.columns(len(enrich))
        for col, (status, count) in zip(ec, enrich.items()):
            color = enrich_colors.get(_norm(status), _TEXT_MUT)
            with col:
                _kpi(
                    "✨", status.title(), f"{count:,}",
                    _fmt_pct(count, total), color,
                )

    # ── v9.9: Three new stat blocks ───────────────────────
    st.markdown(
        "<div style='height:24px'></div>", unsafe_allow_html=True
    )

    # Block 1: DM coverage with st.progress() float
    _render_dm_coverage_stat(stats)

    # Block 2: DM source breakdown (where names came from)
    col_dm, col_cqc = st.columns(2)
    with col_dm:
        _render_dm_source_breakdown(stats)

    # Block 3: CQC rating breakdown
    # Only renders if CQC data exists — _render_cqc_rating_breakdown
    # returns immediately if by_cqc_rating is empty
    with col_cqc:
        _render_cqc_rating_breakdown(stats)


# ─────────────────────────────────────────────────────────
# TAB 2 — Scoring — unchanged
# ─────────────────────────────────────────────────────────
def render_scoring(stats: dict) -> None:
    _section(
        "AI Scoring Breakdown",
        "How the AI has classified your lead database.",
    )

    total     = _safe_int(stats.get("total_leads", 0))
    scoring   = _get_scoring_breakdown()
    comp_dist = _get_contact_completeness_dist()

    priority_data = {
        "High Fit (8–10)":  scoring.get("High",     0),
        "Medium Fit (5–7)": scoring.get("Medium",   0),
        "Low Fit (1–4)":    scoring.get("Low",      0),
        "Unscored":         scoring.get("Unscored", 0),
    }
    priority_colors = {
        "High Fit (8–10)":  _GREEN,
        "Medium Fit (5–7)": _GOLD,
        "Low Fit (1–4)":    _RED,
        "Unscored":         _TEXT_MUT,
    }

    col_a, col_b = st.columns(2)
    with col_a:
        for label, count in priority_data.items():
            color = priority_colors[label]
            pct_w = round(count / total * 100, 1) if total else 0
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">{_esc(label)}</div>
        <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:18px;font-weight:900;
            color:{color};">{count:,}</span>
            <span style="font-size:11px;color:{_TEXT_MUT};">
                {pct_w}%
            </span>
        </div>
    </div>
    <div class="rp-prog-track">
        <div class="rp-prog-fill"
             style="width:{pct_w}%;background:{color};">
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

    with col_b:
        _bar_chart(
            comp_dist, "Contact Completeness Distribution",
            color=_BRAND_BLUE, show_pct=True,
        )

        avg_fit = _safe_float(_scalar(
            "SELECT AVG(fit_score) FROM leads "
            "WHERE fit_score IS NOT NULL"
        ))
        avg_opp = _safe_float(_scalar(
            "SELECT AVG(opportunity_score) FROM leads "
            "WHERE opportunity_score IS NOT NULL"
        ))
        avg_urg = _safe_float(_scalar(
            "SELECT AVG(urgency_score) FROM leads "
            "WHERE urgency_score IS NOT NULL"
        ))
        avg_reach = _safe_float(_scalar(
            "SELECT AVG(reachability_score) FROM leads "
            "WHERE reachability_score IS NOT NULL"
        ))

        st.markdown(
            "<div style='height:8px'></div>", unsafe_allow_html=True
        )
        _section("Average AI Scores", "Across all scored leads.")
        ac = st.columns(4)
        avgs = [
            ("Fit",         f"{avg_fit:.1f}/10",   _GREEN),
            ("Opportunity", f"{avg_opp:.0f}/100",  _BRAND_BLUE),
            ("Urgency",     f"{avg_urg:.0f}/100",  _GOLD),
            ("Reach",       f"{avg_reach:.0f}/100",_BRAND_PUR),
        ]
        for col, (label, val, color) in zip(ac, avgs):
            with col:
                st.markdown(f"""
<div class="rp-stat-card" style="text-align:center;
border-top:3px solid {color};">
    <div style="font-size:9.5px;font-weight:700;
    text-transform:uppercase;letter-spacing:1px;
    color:{_TEXT_MUT};margin-bottom:6px;">{_esc(label)}</div>
    <div style="font-size:22px;font-weight:900;
    color:{color};">{_esc(val)}</div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    _section(
        "Top 20 Scored Leads",
        "Highest fit scores in the database.",
    )
    top_leads = _query("""
        SELECT id, business_name, industry, city, country,
               fit_score, lead_priority, best_channel,
               wa_status, decision_maker_name, phone
        FROM leads
        WHERE fit_score IS NOT NULL
        ORDER BY fit_score DESC LIMIT 20
    """)
    if top_leads:
        st.dataframe(
            pd.DataFrame(top_leads),
            use_container_width=True,
            hide_index=True,
            height=420,
        )
    else:
        _empty(
            "🧠", "No scored leads yet",
            "Run the scoring pipeline from Dashboard or Call Center.",
        )


# ─────────────────────────────────────────────────────────
# TAB 3 — Geography — unchanged
# ─────────────────────────────────────────────────────────
def render_geography() -> None:
    _section(
        "Geographic Distribution",
        "Where your leads are located across countries, "
        "markets and cities.",
    )

    countries = _get_country_breakdown(10)
    markets   = _get_market_breakdown()
    cities    = _get_city_breakdown(10)

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        country_dict = {r["name"]: r["total"] for r in countries}
        _bar_chart(
            country_dict, "Top Countries",
            color=_BRAND_BLUE, show_pct=True,
        )

    with col_b:
        market_dict = {r["name"]: r["total"] for r in markets}
        _bar_chart(
            market_dict, "Markets",
            color=_BRAND_PUR, show_pct=True,
        )

    with col_c:
        city_dict = {r["name"]: r["total"] for r in cities}
        _bar_chart(
            city_dict, "Top Cities",
            color=_GREEN, show_pct=True,
        )

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    ng_cities = _query("""
        SELECT COALESCE(NULLIF(TRIM(city),''),'Unknown') AS name,
               COUNT(*) AS total
        FROM leads
        WHERE LOWER(TRIM(country)) IN ('nigeria','ng')
        GROUP BY name ORDER BY total DESC LIMIT 10
    """)
    if ng_cities:
        _section(
            "Nigeria City Breakdown",
            "Primary market — city-level distribution.",
        )
        ng_dict = {r["name"]: r["total"] for r in ng_cities}
        _bar_chart(
            ng_dict, "Nigeria — Top Cities",
            color=_GREEN, show_pct=True,
        )

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    industries = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(industry),''), 'Unknown') AS name,
            COUNT(*) AS total
        FROM leads GROUP BY name ORDER BY total DESC LIMIT 12
    """)
    if industries:
        _section(
            "Industry Breakdown",
            "Business categories in your lead pool.",
        )
        ind_dict = {r["name"]: r["total"] for r in industries}
        _bar_chart(
            ind_dict, "Industries",
            color=_BRAND_BLUE, show_pct=True, max_items=12,
        )


# ─────────────────────────────────────────────────────────
# TAB 4 — WhatsApp & Channels — unchanged
# ─────────────────────────────────────────────────────────
def render_whatsapp_channels() -> None:
    _section(
        "WhatsApp Intelligence",
        "WA status coverage and channel readiness.",
    )

    total   = _safe_int(_scalar("SELECT COUNT(*) FROM leads"))
    wa_data = _get_wa_breakdown()
    ch_data = _get_channel_breakdown()

    wa_colors = {
        "verified":           _GREEN,
        "opted_in":           _GREEN,
        "business_confirmed": _GOLD,
        "estimated":          _GOLD,
        "unknown":            _TEXT_MUT,
        "unverified":         _RED,
    }
    ch_colors = {
        "whatsapp":   _WA_GREEN,
        "ai_call":    _BRAND_PUR,
        "email":      _BRAND_BLUE,
        "manual":     _TEXT_MUT,
        "unassigned": _TEXT_MUT,
    }

    col_a, col_b = st.columns(2)

    with col_a:
        _section("WA Status Breakdown", "")
        for status, count in sorted(
            wa_data.items(), key=lambda x: x[1], reverse=True
        ):
            color = wa_colors.get(_norm(status), _TEXT_MUT)
            pct_w = round(count / total * 100, 1) if total else 0
            wa_labels = {
                "verified":           "✅ Verified by Agent",
                "opted_in":           "✅ Opted-In",
                "business_confirmed": "🟠 Business Confirmed",
                "estimated":          "💬 Estimated (Heuristic)",
                "unknown":            "⚪ Unknown",
                "unverified":         "❌ Unverified",
            }
            display = wa_labels.get(
                _norm(status),
                status.replace("_", " ").title(),
            )
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">{_esc(display)}</div>
        <div style="display:flex;gap:10px;align-items:center;">
            <span style="font-size:18px;font-weight:900;
            color:{color};">{count:,}</span>
            <span style="font-size:11px;color:{_TEXT_MUT};">
                {pct_w}%
            </span>
        </div>
    </div>
    <div class="rp-prog-track">
        <div class="rp-prog-fill"
             style="width:{pct_w}%;background:{color};">
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

    with col_b:
        _section("Best Channel Assignment", "")
        for channel, count in sorted(
            ch_data.items(), key=lambda x: x[1], reverse=True
        ):
            color = ch_colors.get(_norm(channel), _TEXT_MUT)
            pct_w = round(count / total * 100, 1) if total else 0
            ch_labels = {
                "whatsapp":   "💬 WhatsApp",
                "ai_call":    "🤖 AI Call",
                "email":      "📧 Email",
                "manual":     "📞 Manual Call",
                "unassigned": "⚠️ Unassigned",
            }
            display = ch_labels.get(
                _norm(channel),
                channel.replace("_", " ").title(),
            )
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">{_esc(display)}</div>
        <div style="display:flex;gap:10px;align-items:center;">
            <span style="font-size:18px;font-weight:900;
            color:{color};">{count:,}</span>
            <span style="font-size:11px;color:{_TEXT_MUT};">
                {pct_w}%
            </span>
        </div>
    </div>
    <div class="rp-prog-track">
        <div class="rp-prog-fill"
             style="width:{pct_w}%;background:{color};">
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    _section(
        "WA Coverage by Market",
        "How many leads per market have WA-ready status.",
    )
    wa_by_market = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(market),''), 'Unknown')
                AS market_name,
            COUNT(*) AS total,
            SUM(CASE WHEN wa_status IN (
                'verified','opted_in','estimated',
                'business_confirmed'
            ) THEN 1 ELSE 0 END) AS wa_ready
        FROM leads
        GROUP BY market_name ORDER BY total DESC
    """)
    if wa_by_market:
        rows_html = ""
        for r in wa_by_market:
            mkt   = _esc(
                str(r.get("market_name", "Unknown"))
                .replace("_", " ").title()
            )
            tot   = _safe_int(r.get("total",    0))
            wa_r  = _safe_int(r.get("wa_ready", 0))
            pct_w = round(wa_r / tot * 100, 1) if tot else 0
            rows_html += f"""
<div class="rp-bar-row">
    <div class="rp-bar-label">{mkt}</div>
    <div class="rp-bar-track">
        <div class="rp-bar-fill"
             style="width:{pct_w}%;background:{_WA_GREEN};">
        </div>
    </div>
    <div class="rp-bar-count">{wa_r:,}</div>
    <div class="rp-bar-pct">{pct_w}%</div>
</div>
"""
        st.markdown(f"""
<div class="rp-stat-card">{rows_html}</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# TAB 5 — Sources — unchanged
# ─────────────────────────────────────────────────────────
def render_sources() -> None:
    _section(
        "Lead Sources",
        "Where your leads are coming from and their quality "
        "per source.",
    )

    source_data = _get_source_breakdown()
    src_colors  = {
        "google_maps":     _BRAND_BLUE,
        "b2bhint":         _BRAND_PUR,
        "yelp":            _RED,
        "yellowpages":     _GREEN,
        "companies_house": "#3b82f6",
        "unknown":         _TEXT_MUT,
    }
    src_icons = {
        "google_maps":     "🗺️",
        "b2bhint":         "🏢",
        "yelp":            "⭐",
        "yellowpages":     "📒",
        "companies_house": "🇬🇧",
    }

    col_a, col_b = st.columns([1.4, 1])
    with col_a:
        _bar_chart(
            source_data, "Leads by Source",
            color=_BRAND_BLUE, show_pct=True,
        )

    with col_b:
        _section("Source Quality", "")
        for src, count in sorted(
            source_data.items(), key=lambda x: x[1], reverse=True
        ):
            color = src_colors.get(_norm(src), _TEXT_MUT)
            icon  = src_icons.get(_norm(src), "📋")
            avg_fit = _safe_float(_scalar(
                "SELECT AVG(fit_score) FROM leads "
                "WHERE source = ? AND fit_score IS NOT NULL",
                (src,),
            ))
            has_phone = _safe_int(_scalar(
                "SELECT COUNT(*) FROM leads "
                "WHERE source = ? "
                "AND phone IS NOT NULL AND TRIM(phone) != ''",
                (src,),
            ))
            phone_pct = (
                round(has_phone / count * 100, 1) if count else 0
            )
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">
            {icon} {_esc(src.replace("_"," ").title())}
        </div>
        <span class="rp-badge" style="background:{color}1a;
        color:{color};border:1px solid {color}33;">
            {count:,} leads
        </span>
    </div>
    <div style="display:flex;gap:16px;font-size:11px;
    color:{_TEXT_MUT};">
        <span>Avg fit: <b style="color:{_TEXT_SEC};">
            {avg_fit:.1f}/10</b></span>
        <span>Phone: <b style="color:{_TEXT_SEC};">
            {phone_pct}%</b></span>
    </div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    _section("Campaign History", "All scraping jobs that have been run.")
    jobs = _query("""
        SELECT id, keyword, location, source, status,
               leads_found, leads_inserted, leads_skipped,
               leads_updated, progress, started_at, completed_at
        FROM scrape_jobs ORDER BY id DESC LIMIT 50
    """)
    if jobs:
        st.dataframe(
            pd.DataFrame(jobs),
            use_container_width=True, hide_index=True, height=380,
        )
    else:
        _empty(
            "🔍", "No campaigns yet",
            "Run your first search from Lead Search.",
        )


# ─────────────────────────────────────────────────────────
# TAB 6 — Outreach — unchanged
# ─────────────────────────────────────────────────────────
def render_outreach() -> None:
    _section(
        "Outreach Activity",
        "All WhatsApp, email and call actions logged.",
    )

    outreach_summary = _get_outreach_summary()
    pipeline         = _get_pipeline_summary()

    total_outreach = sum(outreach_summary.values())
    col_a, col_b, col_c, col_d = st.columns(4)
    ch_vals = [
        ("💬", "WhatsApp", outreach_summary.get("whatsapp", 0), _WA_GREEN),
        ("📧", "Email",    outreach_summary.get("email",    0), _BRAND_BLUE),
        ("📞", "AI Call",  outreach_summary.get("ai_call",  0), _BRAND_PUR),
        ("📱", "Manual",   outreach_summary.get("manual",   0), _TEXT_MUT),
    ]
    for col, (icon, label, val, color) in zip(
        [col_a, col_b, col_c, col_d], ch_vals
    ):
        with col:
            _kpi(
                icon, label, f"{val:,}",
                _fmt_pct(val, total_outreach)
                if total_outreach else "0%",
                color,
            )

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )

    _section("Contact Status Breakdown", "")
    col_e, col_f = st.columns(2)
    status_colors = {
        "new":            _TEXT_MUT,
        "called":         _BRAND_BLUE,
        "callback":       _GOLD,
        "interested":     _GREEN,
        "not_interested": _RED,
        "deal_closed":    _WA_GREEN,
        "wrong_number":   _RED,
    }
    total_leads = _safe_int(_scalar("SELECT COUNT(*) FROM leads"))

    with col_e:
        for status, count in sorted(
            pipeline.items(), key=lambda x: x[1], reverse=True
        ):
            color = status_colors.get(_norm(status), _TEXT_MUT)
            pct_w = (
                round(count / total_leads * 100, 1)
                if total_leads else 0
            )
            st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;
    align-items:center;margin-bottom:6px;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">
            {_esc(status.replace("_"," ").title())}
        </div>
        <div style="display:flex;gap:10px;">
            <span style="font-size:16px;font-weight:900;
            color:{color};">{count:,}</span>
            <span style="font-size:11px;
            color:{_TEXT_MUT};">{pct_w}%</span>
        </div>
    </div>
    <div class="rp-prog-track">
        <div class="rp-prog-fill"
             style="width:{pct_w}%;background:{color};">
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

    with col_f:
        rows = _get_outreach_history(limit=200)
        if rows:
            recent_df = pd.DataFrame(rows)[[
                "sent_at", "channel", "status",
                "business_name", "city", "country",
            ]]
            st.dataframe(
                recent_df, use_container_width=True,
                hide_index=True, height=420,
            )
        else:
            _empty(
                "📋", "No outreach logged yet",
                "Log calls and WhatsApp messages from Call Center.",
            )


# ─────────────────────────────────────────────────────────
# TAB 7 — Revenue — unchanged
# ─────────────────────────────────────────────────────────
def render_revenue() -> None:
    revenue_total = _get_revenue_total()
    deals_count   = _get_deals_count()
    avg_deal      = revenue_total / deals_count if deals_count else 0
    rows          = _get_revenue_rows(limit=100)

    _section(
        "Revenue & Closed Deals",
        "All converted opportunities and deal values.",
    )

    r1 = st.columns(4)
    rev_kpis = [
        ("💰", "Total Revenue",  _fmt_money(revenue_total), "All tracked",  _GOLD),
        ("🤝", "Deals Closed",   f"{deals_count:,}",        "Won",          _GREEN),
        ("📊", "Avg Deal Size",  _fmt_money(avg_deal),      "Per deal",     _BRAND_BLUE),
        ("🎯", "Best Deal",      _fmt_money(
            _safe_float(
                _scalar("SELECT MAX(amount) FROM revenue")
            )
        ), "Single deal", _BRAND_PUR),
    ]
    for col, (icon, label, val, sub, color) in zip(r1, rev_kpis):
        with col: _kpi(icon, label, val, sub, color)

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )

    rev_by_market = _query("""
        SELECT
            COALESCE(NULLIF(TRIM(l.market),''), 'Unknown')
                AS market_name,
            COUNT(*) AS deals,
            SUM(r.amount) AS total_rev
        FROM revenue r LEFT JOIN leads l ON r.lead_id = l.id
        GROUP BY market_name ORDER BY total_rev DESC
    """)
    if rev_by_market:
        col_a, col_b = st.columns(2)
        with col_a:
            _section("Revenue by Market", "")
            for r in rev_by_market:
                mkt = _esc(
                    str(r.get("market_name", "Unknown"))
                    .replace("_", " ").title()
                )
                rev = _fmt_money(r.get("total_rev", 0))
                d   = _safe_int(r.get("deals", 0))
                st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">{mkt}</div>
        <div style="text-align:right;">
            <div style="font-size:14px;font-weight:900;
            color:{_GOLD};">{rev}</div>
            <div style="font-size:10px;
            color:{_TEXT_MUT};">{d} deals</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

        with col_b:
            _section("Revenue by Service Type", "")
            rev_by_type = _query("""
                SELECT
                    COALESCE(NULLIF(TRIM(service_type),''),
                             'Unknown') AS stype,
                    COUNT(*) AS deals,
                    SUM(amount) AS total_rev
                FROM revenue GROUP BY stype ORDER BY total_rev DESC
            """)
            if rev_by_type:
                for r in rev_by_type:
                    stype = _esc(str(r.get("stype", "Unknown")))
                    rev   = _fmt_money(r.get("total_rev", 0))
                    d     = _safe_int(r.get("deals", 0))
                    st.markdown(f"""
<div class="rp-stat-card">
    <div style="display:flex;justify-content:space-between;">
        <div style="font-size:13px;font-weight:700;
        color:{_TEXT_PRI};">{stype}</div>
        <div style="text-align:right;">
            <div style="font-size:14px;font-weight:900;
            color:{_GREEN};">{rev}</div>
            <div style="font-size:10px;
            color:{_TEXT_MUT};">{d} deals</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)
            else:
                _empty("💼", "No service type data", "")

    st.markdown(
        "<div style='height:16px'></div>", unsafe_allow_html=True
    )
    _section("All Closed Deals", "")
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True, height=420,
        )
    else:
        _empty(
            "💰", "No deals closed yet",
            "Log a deal from the Call Center to track revenue.",
        )


# ─────────────────────────────────────────────────────────
# TAB 8 — Lead Drilldown — v9.9: CQC + dm_source added
# ─────────────────────────────────────────────────────────
def render_drilldown() -> None:
    _section(
        "Lead Drilldown",
        "Enter any lead ID to inspect the full AI analysis "
        "and contact profile.",
    )

    if "drilldown_id" not in st.session_state:
        st.session_state["drilldown_id"] = 1

    col_in, col_btn = st.columns([2, 1])
    with col_in:
        lead_id_input = st.number_input(
            "Lead ID", min_value=1,
            value=st.session_state["drilldown_id"],
            step=1, key="drill_id_input",
        )
    with col_btn:
        st.markdown(
            "<div style='height:22px'></div>", unsafe_allow_html=True
        )
        if st.button(
            "🔍 Load Lead",
            use_container_width=True,
            key="drill_load_btn",
        ):
            st.session_state["drilldown_id"] = int(lead_id_input)
            st.rerun()

    lead = _get_lead_by_id(st.session_state["drilldown_id"])

    if not lead:
        _empty(
            "🔍",
            f"Lead #{st.session_state['drilldown_id']} not found",
            "Check the ID or run a search to create leads.",
        )
        return

    name     = lead.get("business_name", "Unknown")
    industry = lead.get("industry", "—")
    city     = lead.get("city", "")
    country  = lead.get("country", "")
    market   = lead.get("market", "")
    fit      = _safe_float(lead.get("fit_score", 0))
    priority = str(lead.get("lead_priority") or "unscored").lower()
    wa_stat  = str(lead.get("wa_status") or "unknown").lower()
    channel  = str(lead.get("best_channel") or "manual").lower()

    # ── v9.9: read CQC and DM source fields
    cqc_rating    = lead.get("cqc_rating",        "")
    cqc_url       = lead.get("cqc_url",           "")
    dm_source_raw = lead.get("dm_source",         "")
    dm_name       = lead.get("decision_maker_name","")

    _dm_source_labels = {
        "cqc":             "CQC Register",
        "companies_house": "Companies House",
        "website":         "Website Parse",
        "google_maps":     "Google Maps",
        "not_found":       "",
    }
    dm_source_label = _dm_source_labels.get(dm_source_raw, "")

    fit_color  = _GREEN if fit >= 8 else _GOLD if fit >= 5 else _RED
    pri_colors = {
        "high":    _GREEN, "medium": _GOLD,
        "low":     _RED,   "unscored": _TEXT_MUT,
    }
    wa_colors  = {
        "verified":           _GREEN, "opted_in": _GREEN,
        "business_confirmed": _GOLD,  "estimated": _GOLD,
        "unknown":            _TEXT_MUT,
    }
    ch_colors  = {
        "whatsapp": _WA_GREEN, "ai_call": _BRAND_PUR,
        "email":    _BRAND_BLUE, "manual": _TEXT_MUT,
    }

    loc = " · ".join(p for p in [city, country, market] if p)

    # ── v9.9: CQC rating badge in drilldown header
    cqc_badge_html = ""
    if cqc_rating:
        cqc_rating_colors = {
            "Outstanding":          _GREEN,
            "Good":                 _BRAND_BLUE,
            "Requires Improvement": _GOLD,
            "Inadequate":           _RED,
        }
        cqc_color = cqc_rating_colors.get(cqc_rating, _TEXT_MUT)
        cqc_badge_html = _badge(
            f"🏥 CQC: {cqc_rating}", cqc_color
        )

    st.markdown(f"""
<div class="rp-drill">
    <div style="display:flex;justify-content:space-between;
    align-items:flex-start;margin-bottom:14px;">
        <div style="flex:1;">
            <div class="rp-drill-name">{_esc(name)}</div>
            <div class="rp-drill-meta">
                {_esc(industry)}
                {"&ensp;&middot;&ensp;" + _esc(loc) if loc else ""}
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:5px;">
                {_badge(priority.upper(),
                        pri_colors.get(priority, _TEXT_MUT))}
                {_badge("WA: " + wa_stat.replace("_"," ").title(),
                        wa_colors.get(wa_stat, _TEXT_MUT))}
                {_badge(channel.replace("_"," ").title(),
                        ch_colors.get(channel, _TEXT_MUT))}
                {_badge("Lead #" + str(lead.get("id","")),
                        _BRAND_BLUE)}
                {cqc_badge_html}
            </div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
            <div style="font-size:42px;font-weight:900;
            color:{fit_color};letter-spacing:-2px;line-height:1;">
                {fit:.0f}
            </div>
            <div style="font-size:10px;font-weight:700;
            text-transform:uppercase;letter-spacing:1.5px;
            color:{_TEXT_MUT};">FIT / 10</div>
        </div>
    </div>

    <div class="rp-drill-score-row">
        <div class="rp-drill-score">
            <div class="rp-drill-score-num"
                 style="color:{fit_color};">{fit:.0f}</div>
            <div class="rp-drill-score-lbl">Fit</div>
        </div>
        <div class="rp-drill-score">
            <div class="rp-drill-score-num">
                {_safe_int(lead.get("opportunity_score",0))}
            </div>
            <div class="rp-drill-score-lbl">Opportunity</div>
        </div>
        <div class="rp-drill-score">
            <div class="rp-drill-score-num">
                {_safe_int(lead.get("urgency_score",0))}
            </div>
            <div class="rp-drill-score-lbl">Urgency</div>
        </div>
        <div class="rp-drill-score">
            <div class="rp-drill-score-num">
                {_safe_int(lead.get("reachability_score",0))}
            </div>
            <div class="rp-drill-score-lbl">Reach</div>
        </div>
        <div class="rp-drill-score"
             style="border-top:3px solid {
                 _RED  if _safe_int(lead.get('website_audit_score',0)) <= 2
                 else _GOLD if _safe_int(lead.get('website_audit_score',0)) <= 5
                 else _GREEN
             };">
            <div class="rp-drill-score-num"
                 style="color:{
                     _RED  if _safe_int(lead.get('website_audit_score',0)) <= 2
                     else _GOLD if _safe_int(lead.get('website_audit_score',0)) <= 5
                     else _GREEN
                 };">
                {_safe_int(lead.get("website_audit_score",0))}
            </div>
            <div class="rp-drill-score-lbl">Web Audit</div>
        </div>
    </div>

    <div class="rp-drill-grid">
        <div class="rp-drill-cell">
            <div class="rp-drill-cell-label">📞 Phone</div>
            <div class="rp-drill-cell-value">
                {_esc(lead.get("phone") or "—")}
            </div>
        </div>
        <div class="rp-drill-cell">
            <div class="rp-drill-cell-label">📧 Email</div>
            <div class="rp-drill-cell-value">
                {_esc(lead.get("email") or "—")}
            </div>
        </div>
        <div class="rp-drill-cell">
            <div class="rp-drill-cell-label">🌐 Website</div>
            <div class="rp-drill-cell-value">
                {_esc((lead.get("website") or "—")[:50])}
            </div>
        </div>
        <div class="rp-drill-cell">
            <div class="rp-drill-cell-label">👤 Decision Maker</div>
            <div class="rp-drill-cell-value">
                {_esc(dm_name or "Not found")}
                {
                    "<br><span style='font-size:10px;color:"
                    + _TEXT_MUT
                    + ";font-weight:600;'>Source: "
                    + _esc(dm_source_label)
                    + "</span>"
                    if dm_name and dm_source_label else ""
                }
            </div>
        </div>
    </div>

    {
        # ── v9.9: CQC profile link in drilldown
        "<div style='padding:0 0 14px 0;'>"
        "<a href='" + _esc(cqc_url) + "' target='_blank' "
        "style='color:" + _GOLD + ";font-weight:600;"
        "font-size:12px;text-decoration:none;'>"
        "🔗 View CQC Profile →</a></div>"
        if cqc_url else ""
    }
</div>
""", unsafe_allow_html=True)

    fb = lead.get("social_facebook_url", "")
    ig = lead.get("social_instagram_url", "")
    li = lead.get("social_linkedin_url",  "")
    if fb or ig or li:
        social_html = (
            "<div style='display:flex;gap:8px;"
            "flex-wrap:wrap;margin-bottom:14px;'>"
        )
        if fb:
            social_html += (
                f"<a href='{_esc(fb)}' target='_blank' style='"
                f"background:#3b82f61a;"
                f"border:1px solid #3b82f633;"
                f"color:#3b82f6;padding:6px 14px;"
                f"border-radius:10px;font-size:12px;"
                f"font-weight:700;text-decoration:none;'>"
                f"📘 Facebook</a>"
            )
        if ig:
            social_html += (
                f"<a href='{_esc(ig)}' target='_blank' style='"
                f"background:#ec48991a;"
                f"border:1px solid #ec489933;"
                f"color:#ec4899;padding:6px 14px;"
                f"border-radius:10px;font-size:12px;"
                f"font-weight:700;text-decoration:none;'>"
                f"📸 Instagram</a>"
            )
        if li:
            social_html += (
                f"<a href='{_esc(li)}' target='_blank' style='"
                f"background:#0ea5e91a;"
                f"border:1px solid #0ea5e933;"
                f"color:#0ea5e9;padding:6px 14px;"
                f"border-radius:10px;font-size:12px;"
                f"font-weight:700;text-decoration:none;'>"
                f"💼 LinkedIn</a>"
            )
        social_html += "</div>"
        st.markdown(social_html, unsafe_allow_html=True)

    reason = lead.get("fit_reason") or "No AI analysis yet."
    opener = lead.get("recommended_opener") or "No opener generated yet."
    pains  = _parse_json_list(lead.get("pain_points"))

    col_c, col_d = st.columns(2)
    with col_c:
        with st.expander("💡 Why This Lead", expanded=True):
            st.markdown(
                f"<div style='color:{_TEXT_SEC};font-size:13px;"
                f"line-height:1.7;'>{_esc(reason)}</div>",
                unsafe_allow_html=True,
            )
        with st.expander("💬 Recommended Opener", expanded=True):
            st.markdown(
                f"<div style='color:{_TEXT_SEC};font-size:13px;"
                f"line-height:1.7;font-style:italic;'>"
                f"&ldquo;{_esc(opener)}&rdquo;</div>",
                unsafe_allow_html=True,
            )
    with col_d:
        if pains:
            with st.expander("🔴 Pain Points", expanded=True):
                for p in pains[:6]:
                    st.markdown(
                        f"<div style='color:#fca5a5;font-size:12px;"
                        f"padding:3px 0;'>• {_esc(str(p))}</div>",
                        unsafe_allow_html=True,
                    )

        with st.expander("🔧 All Fields (Raw)", expanded=False):
            raw_items = {
                k: v for k, v in lead.items()
                if v is not None
                and str(v).strip() not in ("", "null", "None")
            }
            for k, v in raw_items.items():
                st.markdown(
                    f"<div style='display:flex;gap:10px;"
                    f"padding:4px 0;"
                    f"border-bottom:1px solid {_BORDER};"
                    f"font-size:12px;'>"
                    f"<span style='color:{_TEXT_MUT};width:160px;"
                    f"flex-shrink:0;font-weight:600;'>"
                    f"{_esc(k)}</span>"
                    f"<span style='color:{_TEXT_SEC};"
                    f"word-break:break-all;'>"
                    f"{_esc(str(v)[:120])}</span></div>",
                    unsafe_allow_html=True,
                )

    st.markdown(
        "<div style='height:10px'></div>", unsafe_allow_html=True
    )
    outreach_rows = _query("""
        SELECT sent_at, channel, status, message_text
        FROM outreach_log WHERE lead_id = ?
        ORDER BY sent_at DESC LIMIT 20
    """, (lead.get("id"),))
    if outreach_rows:
        _section("Outreach History for This Lead", "")
        st.dataframe(
            pd.DataFrame(outreach_rows),
            use_container_width=True, hide_index=True,
        )


# ─────────────────────────────────────────────────────────
# TAB 9 — Exports — unchanged
# ─────────────────────────────────────────────────────────
def render_exports() -> None:
    _section(
        "CSV Exports",
        "Download your lead data segmented by quality and status.",
    )

    export_configs = [
        {
            "key":   "all",
            "icon":  "📊",
            "title": "All Leads",
            "desc":  "Complete database — every lead regardless of status or score.",
            "color": _BRAND_BLUE,
            "fname": "all_leads.csv",
        },
        {
            "key":   "high_fit",
            "icon":  "🎯",
            "title": "High Fit Leads",
            "desc":  "Only leads with fit score ≥ 8 — your best prospects.",
            "color": _GREEN,
            "fname": "high_fit_leads.csv",
        },
        {
            "key":   "whatsapp",
            "icon":  "💬",
            "title": "WhatsApp Ready",
            "desc":  "Leads with verified, estimated or opted-in WA status + phone.",
            "color": _WA_GREEN,
            "fname": "whatsapp_leads.csv",
        },
        {
            "key":   "email",
            "icon":  "📧",
            "title": "Email Ready",
            "desc":  "Leads where an email address has been found or enriched.",
            "color": _BRAND_BLUE,
            "fname": "email_leads.csv",
        },
        {
            "key":   "called",
            "icon":  "📞",
            "title": "Contacted Leads",
            "desc":  "Leads marked as called, callback or interested.",
            "color": _GOLD,
            "fname": "contacted_leads.csv",
        },
    ]

    for cfg in export_configs:
        col_info, col_btn = st.columns([3, 1])
        with col_info:
            st.markdown(f"""
<div class="rp-export-card"
     style="border-left-color:{cfg['color']};">
    <div class="rp-export-title">
        {cfg['icon']} {_esc(cfg['title'])}
    </div>
    <div class="rp-export-desc">
        {_esc(cfg['desc'])}
    </div>
</div>
""", unsafe_allow_html=True)

        with col_btn:
            st.markdown(
                "<div style='height:14px'></div>",
                unsafe_allow_html=True,
            )
            count_queries = {
                "all":
                    "SELECT COUNT(*) FROM leads",
                "high_fit":
                    "SELECT COUNT(*) FROM leads WHERE fit_score >= 8",
                "whatsapp":
                    "SELECT COUNT(*) FROM leads "
                    "WHERE wa_status IN ("
                    "'verified','opted_in','estimated',"
                    "'business_confirmed') "
                    "AND phone IS NOT NULL AND TRIM(phone) != ''",
                "email":
                    "SELECT COUNT(*) FROM leads "
                    "WHERE email IS NOT NULL AND TRIM(email) != ''",
                "called":
                    "SELECT COUNT(*) FROM leads "
                    "WHERE contact_status IN ("
                    "'called','callback','interested')",
            }
            count = _safe_int(
                _scalar(count_queries.get(cfg["key"], "SELECT 0"))
            )

            if count > 0:
                rows = _get_all_leads_for_export(cfg["key"])
                if rows:
                    df_export = pd.DataFrame(rows)
                    st.download_button(
                        label=f"⬇️ Download ({count:,})",
                        data=df_export.to_csv(index=False),
                        file_name=cfg["fname"],
                        mime="text/csv",
                        key=_sanitize_key(f"export_{cfg['key']}"),
                        use_container_width=True,
                    )
                else:
                    st.markdown(
                        f"<div style='font-size:11px;"
                        f"color:{_TEXT_MUT};padding:12px 0;'>"
                        f"No data</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    f"<div style='font-size:11px;"
                    f"color:{_TEXT_MUT};padding:12px 0;'>"
                    f"0 leads</div>",
                    unsafe_allow_html=True,
                )

    st.markdown(
        "<div style='height:8px'></div>", unsafe_allow_html=True
    )
    col_rev_info, col_rev_btn = st.columns([3, 1])
    with col_rev_info:
        st.markdown(f"""
<div class="rp-export-card" style="border-left-color:{_GOLD};">
    <div class="rp-export-title">💰 Revenue & Deals</div>
    <div class="rp-export-desc">
        All closed deals with deal value, service type
        and payment status.
    </div>
</div>
""", unsafe_allow_html=True)
    with col_rev_btn:
        st.markdown(
            "<div style='height:14px'></div>", unsafe_allow_html=True
        )
        rev_rows = _get_revenue_rows(limit=10000)
        if rev_rows:
            st.download_button(
                label=f"⬇️ Download ({len(rev_rows):,})",
                data=pd.DataFrame(rev_rows).to_csv(index=False),
                file_name="revenue.csv",
                mime="text/csv",
                key="export_revenue",
                use_container_width=True,
            )
        else:
            st.markdown(
                f"<div style='font-size:11px;"
                f"color:{_TEXT_MUT};padding:12px 0;'>"
                f"0 deals</div>",
                unsafe_allow_html=True,
            )

    col_out_info, col_out_btn = st.columns([3, 1])
    with col_out_info:
        st.markdown(f"""
<div class="rp-export-card"
     style="border-left-color:{_BRAND_PUR};">
    <div class="rp-export-title">📋 Full Outreach Log</div>
    <div class="rp-export-desc">
        Every WhatsApp, email and call action ever logged.
    </div>
</div>
""", unsafe_allow_html=True)
    with col_out_btn:
        st.markdown(
            "<div style='height:14px'></div>", unsafe_allow_html=True
        )
        out_rows = _get_outreach_history(limit=10000)
        if out_rows:
            st.download_button(
                label=f"⬇️ Download ({len(out_rows):,})",
                data=pd.DataFrame(out_rows).to_csv(index=False),
                file_name="outreach_log.csv",
                mime="text/csv",
                key="export_outreach",
                use_container_width=True,
            )
        else:
            st.markdown(
                f"<div style='font-size:11px;"
                f"color:{_TEXT_MUT};padding:12px 0;'>"
                f"0 actions</div>",
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────
# Main render — unchanged
# ─────────────────────────────────────────────────────────
def render_reports() -> None:
    _render_page_css()
    render_hero()

    col_space, col_ref = st.columns([5, 1])
    with col_ref:
        if st.button(
            "🔄 Refresh Data",
            use_container_width=True,
            key="rp_refresh",
        ):
            st.cache_data.clear()
            st.rerun()

    st.markdown(
        "<div style='height:8px'></div>", unsafe_allow_html=True
    )

    try:
        stats = get_dashboard_stats() or {}
    except Exception:
        stats = {}

    tabs = st.tabs([
        "📊  Overview",
        "🧠  Scoring",
        "🌍  Geography",
        "💬  WhatsApp & Channels",
        "🔎  Sources",
        "📋  Outreach",
        "💰  Revenue",
        "🔍  Lead Drilldown",
        "📥  Exports",
    ])

    with tabs[0]: render_overview(stats)
    with tabs[1]: render_scoring(stats)
    with tabs[2]: render_geography()
    with tabs[3]: render_whatsapp_channels()
    with tabs[4]: render_sources()
    with tabs[5]: render_outreach()
    with tabs[6]: render_revenue()
    with tabs[7]: render_drilldown()
    with tabs[8]: render_exports()


def render() -> None:
    """Entry point called by app.py page router."""
    render_reports()