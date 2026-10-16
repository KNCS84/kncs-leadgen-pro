# msp_intelligence_dashboard.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Streamlit dashboard page for MSP Intelligence
#             Displays digital health summary, cyber risk alerts,
#             website health, tech stack insights, and opportunity briefs
# Dependencies:
#   engine.msp.msp_opportunity_engine
#   engine.msp.digital_maturity_scorer
#   engine.msp.website_health_scanner
#   engine.msp.cyber_risk_analyzer
#   engine.msp.tech_stack_detector
# Critical rules observed:
#   - logger not print()
#   - No raw exceptions exposed to UI
#   - Graceful degradation — partial failure is OK
#   - Safe defaults on all data fetch failures

import logging
import streamlit as st
import pandas as pd
from datetime import datetime

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# MODULE IMPORTS — graceful degradation if any module missing
# ─────────────────────────────────────────────────────────────

def _safe_import_msp_engine():
    try:
        import engine.msp.msp_opportunity_engine as moe
        return moe
    except Exception as exc:
        log.error("Failed to import msp_opportunity_engine: %s", exc)
        return None


def _safe_import_maturity_scorer():
    try:
        import engine.msp.digital_maturity_scorer as dms
        return dms
    except Exception as exc:
        log.error("Failed to import digital_maturity_scorer: %s", exc)
        return None


def _safe_import_health_scanner():
    try:
        import engine.msp.website_health_scanner as whs
        return whs
    except Exception as exc:
        log.error("Failed to import website_health_scanner: %s", exc)
        return None


def _safe_import_cyber_analyzer():
    try:
        import engine.msp.cyber_risk_analyzer as cra
        return cra
    except Exception as exc:
        log.error("Failed to import cyber_risk_analyzer: %s", exc)
        return None


def _safe_import_tech_detector():
    try:
        import engine.msp.tech_stack_detector as tsd
        return tsd
    except Exception as exc:
        log.error("Failed to import tech_stack_detector: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────
# STYLE
# ─────────────────────────────────────────────────────────────

def _inject_css():
    """Inject custom CSS for MSP Intelligence dashboard."""
    st.markdown(
        """
        <style>
        /* ── Page header ── */
        .msp-header {
            background: linear-gradient(135deg, #1b2631 0%, #2e4057 100%);
            padding: 1.5rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            color: white;
        }
        .msp-header h1 {
            margin: 0;
            font-size: 1.8rem;
            font-weight: 700;
            color: white;
        }
        .msp-header p {
            margin: 0.3rem 0 0 0;
            opacity: 0.85;
            font-size: 0.95rem;
            color: white;
        }

        /* ── Metric cards ── */
        .metric-card {
            background: white;
            border: 1px solid #e8eaf0;
            border-radius: 10px;
            padding: 1.2rem 1rem;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            margin-bottom: 0.5rem;
        }
        .metric-card .metric-value {
            font-size: 2rem;
            font-weight: 800;
            color: #2e4057;
            line-height: 1;
        }
        .metric-card .metric-label {
            font-size: 0.78rem;
            color: #666;
            margin-top: 0.3rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .metric-card.alert   .metric-value { color: #c0392b; }
        .metric-card.success .metric-value { color: #1e8449; }
        .metric-card.warning .metric-value { color: #d35400; }
        .metric-card.info    .metric-value { color: #1a5276; }

        /* ── Section headers ── */
        .section-header {
            font-size: 1.1rem;
            font-weight: 700;
            color: #2e4057;
            border-left: 4px solid #5dade2;
            padding-left: 0.75rem;
            margin: 1.5rem 0 0.75rem 0;
        }

        /* ── Cyber risk card ── */
        .cyber-card {
            background: #fdecea;
            border: 1px solid #e74c3c;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .cyber-card.medium {
            background: #fef9e7;
            border-color: #f39c12;
        }
        .cyber-card h4 {
            margin: 0 0 0.25rem 0;
            color: #922b21;
            font-size: 0.92rem;
        }
        .cyber-card.medium h4 { color: #784212; }
        .cyber-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Health card ── */
        .health-card {
            background: #fef9e7;
            border: 1px solid #f39c12;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .health-card h4 {
            margin: 0 0 0.25rem 0;
            color: #784212;
            font-size: 0.92rem;
        }
        .health-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Tech card ── */
        .tech-card {
            background: #f4f6f7;
            border: 1px solid #aab7b8;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .tech-card h4 {
            margin: 0 0 0.25rem 0;
            color: #2e4057;
            font-size: 0.92rem;
        }
        .tech-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Opportunity brief card ── */
        .opp-brief {
            background: white;
            border: 1px solid #5dade2;
            border-radius: 10px;
            padding: 1.1rem 1.2rem;
            margin-bottom: 0.75rem;
            box-shadow: 0 2px 6px rgba(0,0,0,0.07);
        }
        .opp-brief h3 {
            margin: 0 0 0.4rem 0;
            color: #2e4057;
            font-size: 1rem;
        }
        .opp-brief .opp-meta {
            color: #666;
            font-size: 0.82rem;
            margin-bottom: 0.5rem;
        }
        .opp-brief .opp-problems {
            background: #eaf4fb;
            border-radius: 6px;
            padding: 0.5rem 0.75rem;
            margin-top: 0.5rem;
            font-size: 0.83rem;
            color: #1a5276;
        }
        .opp-brief .opp-service {
            font-weight: 700;
            color: #1e8449;
            font-size: 0.88rem;
            margin-top: 0.4rem;
        }

        /* ── Info / error boxes ── */
        .info-box {
            background: #eaf4fb;
            border: 1px solid #aed6f1;
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            color: #1a5276;
            font-size: 0.88rem;
            margin-bottom: 1rem;
        }

        /* ── Maturity band pills ── */
        .band-critical  { background:#fdecea; color:#c0392b; border:1px solid #e74c3c; border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700; }
        .band-poor      { background:#fef5e7; color:#d35400; border:1px solid #e67e22; border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700; }
        .band-fair      { background:#fef9e7; color:#b7950b; border:1px solid #f1c40f; border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700; }
        .band-good      { background:#eafaf1; color:#1e8449; border:1px solid #27ae60; border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700; }
        .band-excellent { background:#d5f5e3; color:#145a32; border:1px solid #1e8449; border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700; }

        /* ── Score bar ── */
        .score-bar-wrap {
            background: #ecf0f1;
            border-radius: 4px;
            height: 8px;
            width: 100%;
            margin-top: 4px;
        }
        .score-bar-fill {
            height: 8px;
            border-radius: 4px;
            background: linear-gradient(90deg, #e74c3c, #f39c12, #27ae60);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_date(value) -> str:
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y")
        return str(value)
    except Exception:
        return str(value)


def _metric_card(
    value,
    label: str,
    card_class: str = "",
    suffix: str = "",
) -> str:
    css = f"metric-card {card_class}".strip()
    return (
        f'<div class="{css}">'
        f'<div class="metric-value">{value}{suffix}</div>'
        f'<div class="metric-label">{label}</div>'
        f"</div>"
    )


def _maturity_band_pill(band: str) -> str:
    """Return a coloured HTML pill for a maturity band."""
    band_lower = str(band).lower()
    css_map = {
        "critical":  "band-critical",
        "poor":      "band-poor",
        "fair":      "band-fair",
        "good":      "band-good",
        "excellent": "band-excellent",
    }
    css = css_map.get(band_lower, "band-fair")
    return f'<span class="{css}">{band.upper()}</span>'


def _score_colour(score: int) -> str:
    """Return CSS colour string for a 0–100 score."""
    if score >= 75:
        return "background-color:#eafaf1;color:#1e8449;font-weight:700"
    elif score >= 50:
        return "background-color:#fef9e7;color:#d35400"
    elif score >= 30:
        return "background-color:#fef5e7;color:#d35400"
    return "background-color:#fdecea;color:#c0392b;font-weight:700"


def _colour_score_cell(val):
    """Pandas Styler applymap function."""
    try:
        return _score_colour(int(val))
    except (TypeError, ValueError):
        return ""


def _colour_severity(val):
    """Pandas Styler applymap for severity column."""
    v = str(val).lower()
    if v in ("critical", "high"):
        return "background-color:#fdecea;color:#c0392b;font-weight:700"
    elif v == "medium":
        return "background-color:#fef9e7;color:#d35400;font-weight:700"
    return "background-color:#eafaf1;color:#1e8449"


def _colour_band(val):
    """Pandas Styler applymap for maturity band column."""
    v = str(val).lower()
    if v == "critical":
        return "background-color:#fdecea;color:#c0392b;font-weight:700"
    elif v == "poor":
        return "background-color:#fef5e7;color:#d35400;font-weight:700"
    elif v == "fair":
        return "background-color:#fef9e7;color:#b7950b"
    elif v == "good":
        return "background-color:#eafaf1;color:#1e8449"
    elif v == "excellent":
        return "background-color:#d5f5e3;color:#145a32;font-weight:700"
    return ""


# ─────────────────────────────────────────────────────────────
# DATA LOADERS
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_maturity_summary() -> dict:
    """Load digital maturity summary stats. Cached 5 min."""
    dms = _safe_import_maturity_scorer()
    if not dms:
        return {}
    try:
        if hasattr(dms, "get_maturity_summary"):
            return dms.get_maturity_summary() or {}
        return {}
    except Exception as exc:
        log.error("_load_maturity_summary failed: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _load_top_msp_opportunities(limit: int = 25) -> list:
    """Load top MSP opportunities ranked by maturity score. Cached 5 min."""
    moe = _safe_import_msp_engine()
    if not moe:
        return []
    try:
        if hasattr(moe, "get_top_msp_opportunities"):
            return moe.get_top_msp_opportunities(limit=limit) or []
        return []
    except Exception as exc:
        log.error("_load_top_msp_opportunities failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_critical_opportunities(limit: int = 25) -> list:
    """Load critical maturity band opportunities. Cached 5 min."""
    dms = _safe_import_maturity_scorer()
    if not dms:
        return []
    try:
        if hasattr(dms, "get_critical_opportunities"):
            return dms.get_critical_opportunities(limit=limit) or []
        return []
    except Exception as exc:
        log.error("_load_critical_opportunities failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_cyber_risk_alerts(limit: int = 50) -> list:
    """Load high-severity cyber risk profiles. Cached 5 min."""
    cra = _safe_import_cyber_analyzer()
    if not cra:
        return []
    try:
        # Try get_high_risk_profiles first, fall back to get_all_profiles
        if hasattr(cra, "get_high_risk_profiles"):
            return cra.get_high_risk_profiles(limit=limit) or []
        if hasattr(cra, "get_all_profiles"):
            profiles = cra.get_all_profiles(limit=limit) or []
            return [
                p for p in profiles
                if str(p.get("severity", "")).lower()
                in ("critical", "high")
            ]
        return []
    except Exception as exc:
        log.error("_load_cyber_risk_alerts failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_poor_health_sites(limit: int = 50) -> list:
    """Load websites with poor health scores. Cached 5 min."""
    whs = _safe_import_health_scanner()
    if not whs:
        return []
    try:
        if hasattr(whs, "get_poor_health_sites"):
            return whs.get_poor_health_sites(limit=limit) or []
        if hasattr(whs, "get_all_scans"):
            scans = whs.get_all_scans(limit=limit) or []
            return [
                s for s in scans
                if _safe_int(s.get("health_score", 100)) < 50
            ]
        return []
    except Exception as exc:
        log.error("_load_poor_health_sites failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_outdated_tech(limit: int = 50) -> list:
    """Load leads with outdated technology stacks. Cached 5 min."""
    tsd = _safe_import_tech_detector()
    if not tsd:
        return []
    try:
        if hasattr(tsd, "get_outdated_tech_leads"):
            return tsd.get_outdated_tech_leads(limit=limit) or []
        if hasattr(tsd, "get_all_profiles"):
            profiles = tsd.get_all_profiles(limit=limit) or []
            return [
                p for p in profiles
                if _safe_int(p.get("tech_age_score", 0)) > 50
            ]
        return []
    except Exception as exc:
        log.error("_load_outdated_tech failed: %s", exc)
        return []


def _load_opportunity_brief(lead_id: int) -> dict:
    """Load full opportunity brief for a single lead. NOT cached."""
    moe = _safe_import_msp_engine()
    if not moe:
        return {}
    try:
        if hasattr(moe, "generate_opportunity_brief"):
            return moe.generate_opportunity_brief(lead_id=lead_id) or {}
        return {}
    except Exception as exc:
        log.error(
            "_load_opportunity_brief failed lead_id=%s: %s",
            lead_id, exc,
        )
        return {}


# ─────────────────────────────────────────────────────────────
# SECTION 1 — DIGITAL HEALTH SUMMARY
# ─────────────────────────────────────────────────────────────

def _render_digital_health_summary():
    """Render Section 1 — digital maturity metric cards."""
    st.markdown(
        '<div class="section-header">📊 Digital Health Summary</div>',
        unsafe_allow_html=True,
    )

    summary = _load_maturity_summary()

    total_scored    = summary.get("total_scored", 0)
    critical_count  = summary.get("critical_count", 0)
    poor_count      = summary.get("poor_count", 0)
    avg_maturity    = summary.get("avg_maturity_score", 0)
    high_urgency    = summary.get("high_urgency_count", 0)
    excellent_count = summary.get("excellent_count", 0)

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        st.markdown(
            _metric_card(total_scored, "Leads Scored"),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            _metric_card(
                critical_count,
                "Critical Maturity",
                card_class="alert",
            ),
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            _metric_card(
                poor_count,
                "Poor Maturity",
                card_class="warning",
            ),
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            _metric_card(
                high_urgency,
                "High Urgency",
                card_class="alert",
            ),
            unsafe_allow_html=True,
        )
    with col5:
        st.markdown(
            _metric_card(
                f"{_safe_float(avg_maturity):.0f}",
                "Avg Maturity Score",
                suffix="/100",
                card_class="info",
            ),
            unsafe_allow_html=True,
        )
    with col6:
        st.markdown(
            _metric_card(
                excellent_count,
                "Excellent (Deprioritise)",
                card_class="success",
            ),
            unsafe_allow_html=True,
        )

    if not summary:
        st.markdown(
            '<div class="info-box">ℹ️ No maturity scores yet. '
            "Run MSP analysis scans to populate this dashboard.</div>",
            unsafe_allow_html=True,
        )
        return

    # Maturity band distribution chart
    if total_scored > 0:
        with st.expander("📈 Maturity Band Distribution", expanded=False):
            bands = {
                "Critical (0–30)":   summary.get("critical_count", 0),
                "Poor (31–50)":      summary.get("poor_count", 0),
                "Fair (51–70)":      summary.get("fair_count", 0),
                "Good (71–85)":      summary.get("good_count", 0),
                "Excellent (86–100)": summary.get("excellent_count", 0),
            }
            chart_df = pd.DataFrame({
                "Band":  list(bands.keys()),
                "Leads": list(bands.values()),
            })
            st.bar_chart(chart_df.set_index("Band"))

            # Component averages
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.metric(
                    "Avg Website Health",
                    f"{_safe_float(summary.get('avg_health_score', 0)):.0f}/100",
                )
            with col_b:
                st.metric(
                    "Avg Cyber Score",
                    f"{_safe_float(summary.get('avg_cyber_score', 0)):.0f}/100",
                )
            with col_c:
                st.metric(
                    "Avg Tech Age Score",
                    f"{_safe_float(summary.get('avg_tech_age_score', 0)):.0f}/100",
                )


# ─────────────────────────────────────────────────────────────
# SECTION 2 — TOP OPPORTUNITIES
# ─────────────────────────────────────────────────────────────

def _render_top_opportunities():
    """Render Section 2 — MSP opportunities ranked by maturity score."""
    st.markdown(
        '<div class="section-header">🏆 Top MSP Opportunities</div>',
        unsafe_allow_html=True,
    )

    col_limit, col_band, col_urgency, col_search = st.columns([1, 2, 2, 3])
    with col_limit:
        limit = st.selectbox(
            "Show",
            options=[10, 25, 50, 100],
            index=1,
            key="msp_opp_limit",
        )
    with col_band:
        band_filter = st.selectbox(
            "Maturity Band",
            options=["All", "Critical", "Poor", "Fair", "Good", "Excellent"],
            key="msp_band_filter",
        )
    with col_urgency:
        urgency_filter = st.selectbox(
            "Urgency",
            options=["All", "High", "Medium", "Low"],
            key="msp_urgency_filter",
        )
    with col_search:
        search_term = st.text_input(
            "Search by name",
            placeholder="Type to filter...",
            key="msp_opp_search",
        )

    opportunities = _load_top_msp_opportunities(limit=limit)

    if not opportunities:
        # Fall back to critical opportunities list
        opportunities = _load_critical_opportunities(limit=limit)

    if not opportunities:
        st.markdown(
            '<div class="info-box">ℹ️ No MSP opportunities found. '
            "Run website health, cyber risk, and tech stack scans first, "
            "then refresh digital maturity scores.</div>",
            unsafe_allow_html=True,
        )
        return

    # Apply filters
    filtered = opportunities

    if band_filter != "All":
        filtered = [
            o for o in filtered
            if str(o.get("maturity_band", "")).lower()
            == band_filter.lower()
        ]

    if urgency_filter != "All":
        filtered = [
            o for o in filtered
            if str(o.get("urgency_band", "")).lower()
            == urgency_filter.lower()
        ]

    if search_term:
        term = search_term.lower()
        filtered = [
            o for o in filtered
            if term in str(o.get("business_name", "")).lower()
        ]

    st.caption(
        f"Showing {len(filtered)} of {len(opportunities)} opportunities"
    )

    if not filtered:
        st.info("No opportunities match your current filters.")
        return

    rows = []
    for opp in filtered:
        rows.append({
            "Business":        opp.get("business_name", "Unknown"),
            "Maturity Score":  _safe_int(opp.get("maturity_score", 0)),
            "Band":            opp.get("maturity_band", "—"),
            "Health Score":    _safe_int(opp.get("health_score", 0)),
            "Cyber Score":     _safe_int(opp.get("cyber_score", 0)),
            "Tech Age Score":  _safe_int(opp.get("tech_age_score", 0)),
            "Urgency":         str(opp.get("urgency_band", "—")).upper(),
            "Scored":          _safe_date(opp.get("scored_at")),
        })

    df = pd.DataFrame(rows)

    def _colour_urgency(val):
        v = str(val).lower()
        if v == "high":
            return "background-color:#fdecea;color:#c0392b;font-weight:700"
        elif v == "medium":
            return "background-color:#fef9e7;color:#d35400;font-weight:700"
        elif v == "low":
            return "background-color:#eafaf1;color:#1e8449;font-weight:700"
        return ""

    styled = (
        df.style
        .applymap(_colour_score_cell, subset=["Maturity Score"])
        .applymap(_colour_score_cell, subset=["Health Score"])
        .applymap(_colour_band, subset=["Band"])
        .applymap(_colour_urgency, subset=["Urgency"])
        .format({
            "Maturity Score": "{:.0f}",
            "Health Score":   "{:.0f}",
            "Cyber Score":    "{:.0f}",
            "Tech Age Score": "{:.0f}",
        })
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export MSP Opportunities CSV",
        data=csv_data,
        file_name=f"msp_opportunities_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_msp_opps",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 3 — CYBER RISK ALERTS
# ─────────────────────────────────────────────────────────────

def _render_cyber_risk_alerts():
    """Render Section 3 — critical and high cyber risk profiles."""
    st.markdown(
        '<div class="section-header">🔐 Cyber Risk Alerts</div>',
        unsafe_allow_html=True,
    )

    col_limit, col_sev, col_view = st.columns([1, 2, 2])
    with col_limit:
        limit = st.selectbox(
            "Show",
            options=[10, 25, 50],
            index=1,
            key="cyber_limit",
        )
    with col_sev:
        sev_filter = st.selectbox(
            "Severity",
            options=["All", "Critical", "High", "Medium"],
            key="cyber_sev_filter",
        )
    with col_view:
        view_mode = st.radio(
            "View as",
            options=["Table", "Cards"],
            horizontal=True,
            key="cyber_view_mode",
        )

    alerts = _load_cyber_risk_alerts(limit=limit)

    if not alerts:
        st.markdown(
            '<div class="info-box">ℹ️ No cyber risk data found. '
            "Run cyber risk analysis scans on your leads first.</div>",
            unsafe_allow_html=True,
        )
        return

    # Apply severity filter
    if sev_filter != "All":
        alerts = [
            a for a in alerts
            if str(a.get("severity", "")).lower() == sev_filter.lower()
        ]

    # Summary counts
    critical_n = sum(
        1 for a in alerts
        if str(a.get("severity", "")).lower() == "critical"
    )
    high_n = sum(
        1 for a in alerts
        if str(a.get("severity", "")).lower() == "high"
    )
    medium_n = sum(
        1 for a in alerts
        if str(a.get("severity", "")).lower() == "medium"
    )

    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("Total Alerts", len(alerts))
    with col_b:
        st.metric("Critical", critical_n)
    with col_c:
        st.metric("High", high_n)
    with col_d:
        st.metric("Medium", medium_n)

    st.divider()

    if not alerts:
        st.info("No alerts match your current severity filter.")
        return

    if view_mode == "Cards":
        _render_cyber_cards(alerts[:15])
    else:
        _render_cyber_table(alerts)


def _render_cyber_cards(alerts: list):
    """Render cyber risk alert cards."""
    for alert in alerts:
        name     = alert.get("business_name", alert.get("domain", "Unknown"))
        severity = str(alert.get("severity", "medium")).lower()
        score    = _safe_int(alert.get("risk_score", 0))
        domain   = alert.get("domain", "—")
        issues   = alert.get("top_issues", [])

        if isinstance(issues, str):
            try:
                import json
                issues = json.loads(issues)
            except Exception:
                issues = [issues]

        issues_text = ""
        if issues:
            issue_items = "".join(
                f"<li>{str(i)}</li>"
                for i in issues[:3]
            )
            issues_text = f"<ul style='margin:0.3rem 0 0 1rem;padding:0'>{issue_items}</ul>"

        card_class = "cyber-card" if severity == "critical" else "cyber-card medium"

        # Key security flags
        flags = []
        if not alert.get("has_https", True):
            flags.append("No HTTPS")
        if not alert.get("hsts_present", True):
            flags.append("No HSTS")
        if not alert.get("spf_present", True):
            flags.append("No SPF")
        if not alert.get("dmarc_present", True):
            flags.append("No DMARC")
        if alert.get("exposed_admin", False):
            flags.append("Exposed Admin")
        flags_text = " · ".join(flags) if flags else "—"

        st.markdown(
            f"""
            <div class="{card_class}">
                <h4>🔐 {name} — Risk Score: {score} ({severity.upper()})</h4>
                <p>
                    Domain: {domain} &nbsp;|&nbsp;
                    Flags: {flags_text}
                </p>
                {issues_text}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_cyber_table(alerts: list):
    """Render cyber risk alerts as a DataFrame."""
    rows = []
    for a in alerts:
        name = a.get("business_name", a.get("domain", "Unknown"))
        rows.append({
            "Business":    name,
            "Domain":      a.get("domain", "—"),
            "Risk Score":  _safe_int(a.get("risk_score", 0)),
            "Severity":    str(a.get("severity", "—")).upper(),
            "HTTPS":       "✅" if a.get("has_https") else "❌",
            "SPF":         "✅" if a.get("spf_present") else "❌",
            "DMARC":       "✅" if a.get("dmarc_present") else "❌",
            "HSTS":        "✅" if a.get("hsts_present") else "❌",
            "Admin Exposed": "⚠️ YES" if a.get("exposed_admin") else "No",
            "Analysed":    _safe_date(a.get("analyzed_at")),
        })

    df = pd.DataFrame(rows)

    styled = (
        df.style
        .applymap(_colour_severity, subset=["Severity"])
        .applymap(_colour_score_cell, subset=["Risk Score"])
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export Cyber Risk Report CSV",
        data=csv_data,
        file_name=f"msp_cyber_risk_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_cyber_risk",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 4 — WEBSITE HEALTH
# ─────────────────────────────────────────────────────────────

def _render_website_health():
    """Render Section 4 — poor website health scores."""
    st.markdown(
        '<div class="section-header">🌐 Website Health</div>',
        unsafe_allow_html=True,
    )

    col_thresh, col_view = st.columns([2, 2])
    with col_thresh:
        threshold = st.slider(
            "Max health score to show",
            min_value=10,
            max_value=70,
            value=50,
            step=5,
            key="health_threshold",
        )
    with col_view:
        view_mode = st.radio(
            "View as",
            options=["Table", "Cards"],
            horizontal=True,
            key="health_view_mode",
        )

    sites = _load_poor_health_sites(limit=100)

    # Apply threshold filter
    sites = [
        s for s in sites
        if _safe_int(s.get("health_score", 100)) < threshold
    ]

    if not sites:
        st.markdown(
            f'<div class="info-box">ℹ️ No sites with health score '
            f"below {threshold}. "
            "Run website health scans to populate this section.</div>",
            unsafe_allow_html=True,
        )
        return

    # Summary
    broken_n = sum(1 for s in sites if s.get("is_broken"))
    no_ssl_n = sum(1 for s in sites if not s.get("has_ssl"))
    ssl_exp_n = sum(1 for s in sites if s.get("ssl_is_expiring"))
    no_mobile_n = sum(1 for s in sites if not s.get("is_mobile_responsive"))

    col_a, col_b, col_c, col_d, col_e = st.columns(5)
    with col_a:
        st.metric("Sites Below Threshold", len(sites))
    with col_b:
        st.metric("Broken / Unreachable", broken_n)
    with col_c:
        st.metric("No SSL", no_ssl_n)
    with col_d:
        st.metric("SSL Expiring Soon", ssl_exp_n)
    with col_e:
        st.metric("Not Mobile Ready", no_mobile_n)

    st.divider()

    if view_mode == "Cards":
        _render_health_cards(sites[:15])
    else:
        _render_health_table(sites)


def _render_health_cards(sites: list):
    """Render website health problem cards."""
    for site in sites:
        name  = site.get("business_name", site.get("url", "Unknown"))
        score = _safe_int(site.get("health_score", 0))
        url   = site.get("url", "—")

        issues = []
        if site.get("is_broken"):
            issues.append("⛔ Site broken / unreachable")
        if not site.get("has_ssl"):
            issues.append("🔓 No SSL certificate")
        if site.get("ssl_is_expiring"):
            days = _safe_int(site.get("ssl_days_remaining", 0))
            issues.append(f"⚠️ SSL expires in {days} days")
        if not site.get("is_mobile_responsive"):
            issues.append("📱 Not mobile responsive")
        if not site.get("has_contact_form"):
            issues.append("📋 No contact form detected")

        load_time = _safe_float(site.get("load_time_seconds", 0))
        if load_time > 3:
            issues.append(f"🐢 Slow load: {load_time:.1f}s")

        issues_html = "".join(
            f'<div style="font-size:0.82rem;margin:2px 0">{i}</div>'
            for i in issues
        ) if issues else "<div style='font-size:0.82rem'>No specific issues flagged</div>"

        st.markdown(
            f"""
            <div class="health-card">
                <h4>🌐 {name} — Health Score: {score}/100</h4>
                <p style="margin-bottom:0.4rem">URL: {url}</p>
                {issues_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_health_table(sites: list):
    """Render website health as a DataFrame."""
    rows = []
    for s in sites:
        name = s.get("business_name", s.get("url", "Unknown"))
        load = _safe_float(s.get("load_time_seconds", 0))
        rows.append({
            "Business":       name,
            "URL":            s.get("url", "—"),
            "Health Score":   _safe_int(s.get("health_score", 0)),
            "SSL":            "✅" if s.get("has_ssl") else "❌",
            "SSL Expiring":   "⚠️ YES" if s.get("ssl_is_expiring") else "No",
            "Mobile":         "✅" if s.get("is_mobile_responsive") else "❌",
            "Contact Form":   "✅" if s.get("has_contact_form") else "❌",
            "Broken":         "⛔ YES" if s.get("is_broken") else "No",
            "Load Time (s)":  f"{load:.1f}" if load else "—",
            "Scanned":        _safe_date(s.get("scanned_at")),
        })

    df = pd.DataFrame(rows)

    styled = df.style.applymap(
        _colour_score_cell, subset=["Health Score"]
    ).format({"Health Score": "{:.0f}"})

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export Website Health CSV",
        data=csv_data,
        file_name=f"msp_website_health_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_health",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 5 — TECH STACK INSIGHTS
# ─────────────────────────────────────────────────────────────

def _render_tech_stack_insights():
    """Render Section 5 — outdated technology platforms."""
    st.markdown(
        '<div class="section-header">⚙️ Tech Stack Insights</div>',
        unsafe_allow_html=True,
    )

    col_view, _ = st.columns([2, 4])
    with col_view:
        view_mode = st.radio(
            "View as",
            options=["Table", "Cards"],
            horizontal=True,
            key="tech_view_mode",
        )

    tech_leads = _load_outdated_tech(limit=50)

    if not tech_leads:
        st.markdown(
            '<div class="info-box">ℹ️ No tech stack data found. '
            "Run tech stack detection scans on your leads first.</div>",
            unsafe_allow_html=True,
        )
        return

    # Summary stats
    old_cms_n = sum(
        1 for t in tech_leads
        if str(t.get("tech_age_label", "")).lower()
        in ("legacy", "outdated", "very old")
    )
    no_analytics_n = sum(
        1 for t in tech_leads
        if not t.get("analytics_tools")
    )
    jquery_n = sum(
        1 for t in tech_leads
        if t.get("jquery_version")
    )

    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("Outdated Tech Leads", len(tech_leads))
    with col_b:
        st.metric("Legacy CMS Detected", old_cms_n)
    with col_c:
        st.metric("No Analytics", no_analytics_n)
    with col_d:
        st.metric("Old jQuery Detected", jquery_n)

    st.divider()

    if view_mode == "Cards":
        _render_tech_cards(tech_leads[:12])
    else:
        _render_tech_table(tech_leads)


def _render_tech_cards(leads: list):
    """Render tech stack insight cards."""
    for lead in leads:
        name      = lead.get("business_name", lead.get("url", "Unknown"))
        cms       = lead.get("cms", "Unknown")
        age_label = lead.get("tech_age_label", "Unknown")
        age_score = _safe_int(lead.get("tech_age_score", 0))
        analytics = lead.get("analytics_tools", "")
        jquery    = lead.get("jquery_version", "")
        php       = lead.get("php_version", "")
        chat      = lead.get("chat_tools", "")

        details = []
        if cms and cms != "Unknown":
            details.append(f"CMS: {cms}")
        if jquery:
            details.append(f"jQuery: {jquery}")
        if php:
            details.append(f"PHP: {php}")
        if not analytics:
            details.append("No analytics detected")
        if chat:
            details.append(f"Chat: {chat}")

        details_text = " · ".join(details) if details else "Limited tech data"

        st.markdown(
            f"""
            <div class="tech-card">
                <h4>⚙️ {name} — Tech Age: {age_label} ({age_score}/100)</h4>
                <p>{details_text}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_tech_table(leads: list):
    """Render tech stack as a DataFrame."""
    rows = []
    for t in leads:
        name = t.get("business_name", t.get("url", "Unknown"))
        rows.append({
            "Business":     name,
            "CMS":          t.get("cms", "—"),
            "Tech Age":     t.get("tech_age_label", "—"),
            "Age Score":    _safe_int(t.get("tech_age_score", 0)),
            "Analytics":    t.get("analytics_tools", "❌ None") or "❌ None",
            "jQuery":       t.get("jquery_version", "—") or "—",
            "PHP":          t.get("php_version", "—") or "—",
            "Chat Tools":   t.get("chat_tools", "—") or "—",
            "Detected":     _safe_date(t.get("detected_at")),
        })

    df = pd.DataFrame(rows)

    styled = df.style.applymap(
        _colour_score_cell, subset=["Age Score"]
    ).format({"Age Score": "{:.0f}"})

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export Tech Stack CSV",
        data=csv_data,
        file_name=f"msp_tech_stack_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_tech",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 6 — OPPORTUNITY BRIEF
# ─────────────────────────────────────────────────────────────

def _render_opportunity_brief():
    """Render Section 6 — click-through full opportunity brief per lead."""
    st.markdown(
        '<div class="section-header">📋 Opportunity Brief</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="info-box">Select a lead to generate a full '
        "MSP opportunity brief — problems detected, recommended "
        "service, urgency, and estimated deal value.</div>",
        unsafe_allow_html=True,
    )

    opportunities = _load_top_msp_opportunities(limit=50)
    if not opportunities:
        opportunities = _load_critical_opportunities(limit=50)

    if not opportunities:
        st.info(
            "No MSP opportunities available. "
            "Run scans to generate opportunity data."
        )
        return

    # Build select options
    options = {
        f"{o.get('business_name', 'Unknown')} "
        f"(Score: {_safe_int(o.get('maturity_score', 0))})": o.get("lead_id")
        for o in opportunities
        if o.get("lead_id")
    }

    if not options:
        st.info("No leads with IDs available for brief generation.")
        return

    selected_label = st.selectbox(
        "Select lead for full brief:",
        options=list(options.keys()),
        key="brief_lead_select",
    )

    selected_lead_id = options.get(selected_label)

    col_gen, _ = st.columns([2, 6])
    with col_gen:
        generate = st.button(
            "📋 Generate Brief",
            use_container_width=True,
            key="generate_brief_btn",
        )

    if generate and selected_lead_id:
        with st.spinner("Generating opportunity brief..."):
            brief = _load_opportunity_brief(selected_lead_id)

        if not brief:
            st.warning(
                "Brief could not be generated for this lead. "
                "Ensure scans have been run for this business."
            )
            return

        _render_brief_card(brief)

    # ── Pre-loaded top 5 briefs ───────────────────────────────
    with st.expander("📊 Pre-loaded Top 5 Critical Briefs", expanded=False):
        critical = _load_critical_opportunities(limit=5)
        if not critical:
            st.info("No critical opportunities to preview.")
        else:
            for opp in critical:
                lead_id = opp.get("lead_id")
                if not lead_id:
                    continue
                brief = _load_opportunity_brief(lead_id)
                if brief:
                    _render_brief_card(brief)
                else:
                    # Render from maturity data only
                    _render_brief_from_maturity(opp)


def _render_brief_card(brief: dict):
    """Render a full opportunity brief card."""
    name       = brief.get("business_name", "Unknown")
    score      = _safe_int(brief.get("maturity_score", 0))
    band       = brief.get("maturity_band", "—")
    problems   = brief.get("problems", [])
    service    = brief.get("recommended_service", "—")
    urgency    = brief.get("urgency", "—")
    est_value  = brief.get("estimated_value", "—")
    health_s   = _safe_int(brief.get("health_score", 0))
    cyber_s    = _safe_int(brief.get("cyber_score", 0))
    tech_s     = _safe_int(brief.get("tech_age_score", 0))

    problems_html = ""
    if problems:
        items = "".join(
            f"<li>{str(p)}</li>" for p in problems[:3]
        )
        problems_html = (
            f'<div class="opp-problems">'
            f"<strong>Top Problems Detected:</strong>"
            f"<ul style='margin:0.3rem 0 0 1rem;padding:0'>{items}</ul>"
            f"</div>"
        )

    st.markdown(
        f"""
        <div class="opp-brief">
            <h3>📋 {name}</h3>
            <div class="opp-meta">
                Maturity Score: <strong>{score}/100</strong>
                &nbsp;|&nbsp; Band: {_maturity_band_pill(band)}
                &nbsp;|&nbsp; Urgency: <strong>{urgency.upper()}</strong>
            </div>
            <div class="opp-meta">
                Health: {health_s}/100
                &nbsp;|&nbsp; Cyber: {cyber_s}/100
                &nbsp;|&nbsp; Tech Age: {tech_s}/100
            </div>
            {problems_html}
            <div class="opp-service">
                ✅ Recommended Service: {service}
            </div>
            <div style="margin-top:0.4rem;font-size:0.85rem;color:#555">
                💰 Estimated Deal Value: <strong>{est_value}</strong>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_brief_from_maturity(opp: dict):
    """Render a simplified brief card from maturity score data."""
    name  = opp.get("business_name", "Unknown")
    score = _safe_int(opp.get("maturity_score", 0))
    band  = opp.get("maturity_band", "Critical")

    # Infer recommended service from scores
    health = _safe_int(opp.get("health_score", 0))
    cyber  = _safe_int(opp.get("cyber_score", 0))
    tech   = _safe_int(opp.get("tech_age_score", 0))

    if cyber < 40:
        service = "Cyber Security Audit & Remediation"
    elif health < 40:
        service = "Website Recovery & Performance Package"
    elif tech < 40:
        service = "Technology Modernisation Consultancy"
    else:
        service = "Managed IT Services — Full Stack"

    # Estimate value from band
    value_map = {
        "critical": "£2,000–£5,000/month",
        "poor":     "£1,500–£3,000/month",
        "fair":     "£800–£1,500/month",
    }
    est_value = value_map.get(str(band).lower(), "£500–£1,000/month")

    st.markdown(
        f"""
        <div class="opp-brief">
            <h3>📋 {name}</h3>
            <div class="opp-meta">
                Maturity Score: <strong>{score}/100</strong>
                &nbsp;|&nbsp; Band: {_maturity_band_pill(band)}
            </div>
            <div class="opp-meta">
                Health: {health}/100
                &nbsp;|&nbsp; Cyber: {cyber}/100
                &nbsp;|&nbsp; Tech Age: {tech}/100
            </div>
            <div class="opp-service">
                ✅ Recommended Service: {service}
            </div>
            <div style="margin-top:0.4rem;font-size:0.85rem;color:#555">
                💰 Estimated Deal Value: <strong>{est_value}</strong>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────

def _render_sidebar():
    """Render sidebar with refresh, scan triggers, and module status."""
    with st.sidebar:
        st.markdown("### 🖥️ MSP Intelligence")
        st.caption("KNCS LeadGen Pro — Phase 1")
        st.divider()

        if st.button(
            "🔄 Refresh All Data",
            use_container_width=True,
            key="msp_refresh_all",
        ):
            st.cache_data.clear()
            st.success("Cache cleared — reloading...")
            st.rerun()

        st.divider()
        st.markdown("**Run Scans**")

        # Website health scan trigger
        if st.button(
            "🌐 Scan Website Health (Batch)",
            use_container_width=True,
            key="msp_run_health",
        ):
            whs = _safe_import_health_scanner()
            if whs and hasattr(whs, "batch_scan_leads"):
                with st.spinner("Scanning websites..."):
                    try:
                        result = whs.batch_scan_leads(batch_size=20)
                        st.success(
                            f"✅ Scanned {result.get('scanned', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Health scan batch failed: %s", exc)
                        st.error("Health scan failed. Check logs.")
            else:
                st.warning("Health scanner not available.")

        # Cyber risk scan trigger
        if st.button(
            "🔐 Scan Cyber Risk (Batch)",
            use_container_width=True,
            key="msp_run_cyber",
        ):
            cra = _safe_import_cyber_analyzer()
            if cra and hasattr(cra, "batch_analyze_leads"):
                with st.spinner("Analysing cyber risk..."):
                    try:
                        result = cra.batch_analyze_leads(batch_size=20)
                        st.success(
                            f"✅ Analysed {result.get('analyzed', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Cyber scan batch failed: %s", exc)
                        st.error("Cyber scan failed. Check logs.")
            else:
                st.warning("Cyber analyser not available.")

        # Tech stack scan trigger
        if st.button(
            "⚙️ Detect Tech Stacks (Batch)",
            use_container_width=True,
            key="msp_run_tech",
        ):
            tsd = _safe_import_tech_detector()
            if tsd and hasattr(tsd, "batch_detect_leads"):
                with st.spinner("Detecting tech stacks..."):
                    try:
                        result = tsd.batch_detect_leads(batch_size=20)
                        st.success(
                            f"✅ Detected {result.get('detected', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Tech detect batch failed: %s", exc)
                        st.error("Tech detection failed. Check logs.")
            else:
                st.warning("Tech detector not available.")

        # Maturity score refresh trigger
        if st.button(
            "📊 Refresh Maturity Scores",
            use_container_width=True,
            key="msp_run_maturity",
        ):
            dms = _safe_import_maturity_scorer()
            if dms and hasattr(dms, "refresh_all_maturity_scores"):
                with st.spinner("Refreshing maturity scores..."):
                    try:
                        result = dms.refresh_all_maturity_scores(
                            batch_size=50
                        )
                        st.success(
                            f"✅ Refreshed {result.get('refreshed', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Maturity refresh failed: %s", exc)
                        st.error("Maturity refresh failed. Check logs.")
            else:
                st.warning("Maturity scorer not available.")

        # Full MSP analysis trigger
        if st.button(
            "🚀 Run Full MSP Analysis",
            use_container_width=True,
            key="msp_run_full",
        ):
            moe = _safe_import_msp_engine()
            if moe and hasattr(moe, "refresh_all_opportunities"):
                with st.spinner("Running full MSP analysis..."):
                    try:
                        result = moe.refresh_all_opportunities()
                        st.success(
                            f"✅ Processed {result.get('processed', 0)} "
                            f"| Succeeded {result.get('succeeded', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Full MSP analysis failed: %s", exc)
                        st.error("MSP analysis failed. Check logs.")
            else:
                st.warning("MSP opportunity engine not available.")

        st.divider()

        # Module status indicators
        st.markdown("**Module Status**")
        modules = {
            "MSP Engine":        _safe_import_msp_engine(),
            "Maturity Scorer":   _safe_import_maturity_scorer(),
            "Health Scanner":    _safe_import_health_scanner(),
            "Cyber Analyser":    _safe_import_cyber_analyzer(),
            "Tech Detector":     _safe_import_tech_detector(),
        }
        for mod_name, mod in modules.items():
            status = "✅" if mod else "❌"
            st.caption(f"{status} {mod_name}")

        st.divider()
        st.caption(
            f"Last loaded: {datetime.now().strftime('%H:%M:%S')}"
        )


# ─────────────────────────────────────────────────────────────
# MAIN PAGE ENTRY POINT
# ─────────────────────────────────────────────────────────────

def show():
    """
    Main entry point for the MSP Intelligence dashboard page.

    Called by app.py navigation router.
    All 6 sections render independently — failure in one
    does not block the others.
    """
    _inject_css()
    _render_sidebar()

    # Page header
    st.markdown(
        """
        <div class="msp-header">
            <h1>🖥️ MSP Intelligence</h1>
            <p>
                Digital maturity scoring · Website health · Cyber risk ·
                Tech stack detection · Opportunity briefs — Phase 1 vertical module
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Section 1: Digital Health Summary ────────────────────
    try:
        _render_digital_health_summary()
    except Exception as exc:
        log.error("Section 1 failed: %s", exc)
        st.error("Digital health summary failed to load.")

    st.divider()

    # ── Section 2: Top Opportunities ─────────────────────────
    try:
        _render_top_opportunities()
    except Exception as exc:
        log.error("Section 2 failed: %s", exc)
        st.error("Top opportunities failed to load.")

    st.divider()

    # ── Section 3: Cyber Risk Alerts ─────────────────────────
    try:
        _render_cyber_risk_alerts()
    except Exception as exc:
        log.error("Section 3 failed: %s", exc)
        st.error("Cyber risk alerts failed to load.")

    st.divider()

    # ── Section 4: Website Health ─────────────────────────────
    try:
        _render_website_health()
    except Exception as exc:
        log.error("Section 4 failed: %s", exc)
        st.error("Website health section failed to load.")

    st.divider()

    # ── Section 5: Tech Stack Insights ───────────────────────
    try:
        _render_tech_stack_insights()
    except Exception as exc:
        log.error("Section 5 failed: %s", exc)
        st.error("Tech stack insights failed to load.")

    st.divider()

    # ── Section 6: Opportunity Brief ─────────────────────────
    try:
        _render_opportunity_brief()
    except Exception as exc:
        log.error("Section 6 failed: %s", exc)
        st.error("Opportunity brief failed to load.")


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN GUARD
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    show()