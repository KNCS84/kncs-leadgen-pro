# dental_intelligence_dashboard.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Streamlit dashboard page for Dental Intelligence
#             Displays practice summary, reputation problems,
#             growing practices, dental groups, and buyer type filter
# Dependencies:
#   engine.dental.dental_intelligence
#   engine.dental.dental_group_detector
#   engine.dental.dental_review_analyzer
#   engine.dental.dental_growth_signals
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

def _safe_import_dental_intelligence():
    try:
        import engine.dental.dental_intelligence as di
        return di
    except Exception as exc:
        log.error("Failed to import dental_intelligence: %s", exc)
        return None


def _safe_import_group_detector():
    try:
        import engine.dental.dental_group_detector as gd
        return gd
    except Exception as exc:
        log.error("Failed to import dental_group_detector: %s", exc)
        return None


def _safe_import_review_analyzer():
    try:
        import engine.dental.dental_review_analyzer as ra
        return ra
    except Exception as exc:
        log.error("Failed to import dental_review_analyzer: %s", exc)
        return None


def _safe_import_growth_signals():
    try:
        import engine.dental.dental_growth_signals as gs
        return gs
    except Exception as exc:
        log.error("Failed to import dental_growth_signals: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────
# STYLE
# ─────────────────────────────────────────────────────────────

def _inject_css():
    """Inject custom CSS for Dental Intelligence dashboard."""
    st.markdown(
        """
        <style>
        /* ── Page header ── */
        .dental-header {
            background: linear-gradient(135deg, #145a32 0%, #27ae60 100%);
            padding: 1.5rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            color: white;
        }
        .dental-header h1 {
            margin: 0;
            font-size: 1.8rem;
            font-weight: 700;
            color: white;
        }
        .dental-header p {
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
            color: #145a32;
            line-height: 1;
        }
        .metric-card .metric-label {
            font-size: 0.78rem;
            color: #666;
            margin-top: 0.3rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .metric-card.alert .metric-value   { color: #c0392b; }
        .metric-card.success .metric-value { color: #1e8449; }
        .metric-card.warning .metric-value { color: #d35400; }

        /* ── Section headers ── */
        .section-header {
            font-size: 1.1rem;
            font-weight: 700;
            color: #145a32;
            border-left: 4px solid #27ae60;
            padding-left: 0.75rem;
            margin: 1.5rem 0 0.75rem 0;
        }

        /* ── Reputation problem card ── */
        .rep-card {
            background: #fdecea;
            border: 1px solid #e74c3c;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .rep-card h4 {
            margin: 0 0 0.25rem 0;
            color: #922b21;
            font-size: 0.92rem;
        }
        .rep-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Growth signal card ── */
        .growth-card {
            background: #eafaf1;
            border: 1px solid #27ae60;
            border-radius: 8px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
        }
        .growth-card h4 {
            margin: 0 0 0.25rem 0;
            color: #145a32;
            font-size: 0.92rem;
        }
        .growth-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Group card ── */
        .group-card {
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 0.6rem;
        }
        .group-card h4 {
            margin: 0 0 0.3rem 0;
            color: #145a32;
            font-size: 0.95rem;
        }
        .group-card p {
            margin: 0;
            color: #555;
            font-size: 0.82rem;
        }

        /* ── Info / error boxes ── */
        .info-box {
            background: #eafaf1;
            border: 1px solid #a9dfbf;
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            color: #145a32;
            font-size: 0.88rem;
            margin-bottom: 1rem;
        }
        .error-box {
            background: #fdecea;
            border: 1px solid #e74c3c;
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            color: #922b21;
            font-size: 0.88rem;
            margin-bottom: 1rem;
        }

        /* ── Star rating display ── */
        .star-rating {
            color: #f39c12;
            font-size: 1rem;
        }

        /* ── Badges ── */
        .badge-high {
            background: #fdecea; color: #c0392b;
            border: 1px solid #e74c3c;
            border-radius: 4px; padding: 2px 8px;
            font-size: 0.75rem; font-weight: 600;
        }
        .badge-medium {
            background: #fef9e7; color: #d35400;
            border: 1px solid #f39c12;
            border-radius: 4px; padding: 2px 8px;
            font-size: 0.75rem; font-weight: 600;
        }
        .badge-low {
            background: #eafaf1; color: #1e8449;
            border: 1px solid #27ae60;
            border-radius: 4px; padding: 2px 8px;
            font-size: 0.75rem; font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_date(value) -> str:
    """Format a date value safely."""
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y")
        return str(value)
    except Exception:
        return str(value)


def _stars(rating) -> str:
    """Convert numeric rating to star string."""
    try:
        r = float(rating)
        full  = int(r)
        half  = 1 if (r - full) >= 0.5 else 0
        empty = 5 - full - half
        return "★" * full + "½" * half + "☆" * empty
    except (TypeError, ValueError):
        return "—"


def _metric_card(
    value,
    label: str,
    card_class: str = "",
    suffix: str = "",
) -> str:
    """Return HTML for a metric card."""
    css = f"metric-card {card_class}".strip()
    return (
        f'<div class="{css}">'
        f'<div class="metric-value">{value}{suffix}</div>'
        f'<div class="metric-label">{label}</div>'
        f"</div>"
    )


def _colour_score(val):
    """Styler function — colour a score cell."""
    try:
        v = int(val)
        if v >= 75:
            return "background-color:#eafaf1;color:#1e8449;font-weight:700"
        elif v >= 50:
            return "background-color:#fef9e7;color:#d35400"
        return "background-color:#fdecea;color:#c0392b"
    except (TypeError, ValueError):
        return ""


def _colour_rating(val):
    """Styler function — colour a star rating cell."""
    try:
        v = float(str(val).replace("★", "").replace("½", "").replace("☆", "").strip())
        if v >= 4.5:
            return "color:#1e8449;font-weight:700"
        elif v >= 4.0:
            return "color:#d35400"
        elif v >= 3.0:
            return "color:#c0392b"
        return "color:#922b21;font-weight:700"
    except (TypeError, ValueError):
        return ""


# ─────────────────────────────────────────────────────────────
# DATA LOADERS — cached where appropriate
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_practice_summary() -> dict:
    """Load aggregate summary metrics. Cached 5 min."""
    di = _safe_import_dental_intelligence()
    if not di:
        return {}
    try:
        if hasattr(di, "get_intelligence_summary"):
            return di.get_intelligence_summary() or {}
        return {}
    except Exception as exc:
        log.error("_load_practice_summary failed: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _load_review_summary() -> dict:
    """Load review analysis summary. Cached 5 min."""
    ra = _safe_import_review_analyzer()
    if not ra:
        return {}
    try:
        if hasattr(ra, "get_review_summary"):
            return ra.get_review_summary() or {}
        return {}
    except Exception as exc:
        log.error("_load_review_summary failed: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _load_top_opportunities(limit: int = 25) -> list:
    """Load top dental opportunities. Cached 5 min."""
    di = _safe_import_dental_intelligence()
    if not di:
        return []
    try:
        if hasattr(di, "get_top_opportunities"):
            return di.get_top_opportunities(limit=limit) or []
        return []
    except Exception as exc:
        log.error("_load_top_opportunities failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_poor_reputation(threshold: int = 50) -> list:
    """Load practices with poor review scores. Cached 5 min."""
    ra = _safe_import_review_analyzer()
    if not ra:
        return []
    try:
        if hasattr(ra, "get_poor_reputation_practices"):
            return ra.get_poor_reputation_practices(
                threshold=threshold
            ) or []
        return []
    except Exception as exc:
        log.error("_load_poor_reputation failed: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_growing_practices(days: int = 90) -> list:
    """Load practices with growth signals. Cached 5 min."""
    gs = _safe_import_growth_signals()
    if not gs:
        return []
    try:
        if hasattr(gs, "get_growing_practices"):
            return gs.get_growing_practices(days=days) or []
        return []
    except Exception as exc:
        log.error("_load_growing_practices failed: %s", exc)
        return []


@st.cache_data(ttl=600)
def _load_dental_groups() -> list:
    """Load detected dental groups / chains. Cached 10 min."""
    gd = _safe_import_group_detector()
    if not gd:
        return []
    try:
        if hasattr(gd, "identify_dental_groups"):
            return gd.identify_dental_groups() or []
        return []
    except Exception as exc:
        log.error("_load_dental_groups failed: %s", exc)
        return []


@st.cache_data(ttl=600)
def _load_enterprise_groups(min_sites: int = 3) -> list:
    """Load enterprise dental groups. Cached 10 min."""
    gd = _safe_import_group_detector()
    if not gd:
        return []
    try:
        if hasattr(gd, "get_enterprise_opportunities"):
            return gd.get_enterprise_opportunities(
                min_sites=min_sites
            ) or []
        return []
    except Exception as exc:
        log.error("_load_enterprise_groups failed: %s", exc)
        return []


@st.cache_data(ttl=600)
def _load_expanding_groups() -> list:
    """Load practices showing multi-signal expansion. Cached 10 min."""
    gs = _safe_import_growth_signals()
    if not gs:
        return []
    try:
        if hasattr(gs, "get_expanding_groups"):
            return gs.get_expanding_groups() or []
        return []
    except Exception as exc:
        log.error("_load_expanding_groups failed: %s", exc)
        return []


def _load_buyer_type_opportunities(buyer_type: str) -> list:
    """Load opportunities filtered by buyer type. NOT cached."""
    di = _safe_import_dental_intelligence()
    if not di:
        return []
    try:
        if hasattr(di, "get_opportunities_by_buyer_type"):
            return di.get_opportunities_by_buyer_type(
                buyer_type=buyer_type
            ) or []
        # Fallback — return top opps unfiltered
        if hasattr(di, "get_top_opportunities"):
            return di.get_top_opportunities(limit=50) or []
        return []
    except Exception as exc:
        log.error(
            "_load_buyer_type_opportunities failed type=%s: %s",
            buyer_type,
            exc,
        )
        return []


# ─────────────────────────────────────────────────────────────
# SECTION 1 — PRACTICE SUMMARY
# ─────────────────────────────────────────────────────────────

def _render_practice_summary():
    """Render Section 1 — metric card summary row."""
    st.markdown(
        '<div class="section-header">📊 Practice Summary</div>',
        unsafe_allow_html=True,
    )

    summary = _load_practice_summary()
    review_sum = _load_review_summary()

    total_practices    = summary.get("total_leads", 0)
    high_opportunity   = summary.get("high_opportunity_count", 0)
    avg_score          = summary.get("avg_opportunity_score", 0)
    total_analyzed     = review_sum.get("total_analyzed", 0)
    poor_reputation    = review_sum.get("critical_count", 0) + review_sum.get("poor_count", 0)
    practices_growing  = review_sum.get("practices_growing", 0)

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        st.markdown(
            _metric_card(total_practices, "Total Practices"),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            _metric_card(
                high_opportunity,
                "High Opportunity",
                card_class="alert",
            ),
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            _metric_card(
                total_analyzed,
                "Reviews Analysed",
            ),
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            _metric_card(
                poor_reputation,
                "Poor Reputation",
                card_class="alert",
            ),
            unsafe_allow_html=True,
        )
    with col5:
        st.markdown(
            _metric_card(
                practices_growing,
                "Growing Practices",
                card_class="success",
            ),
            unsafe_allow_html=True,
        )
    with col6:
        st.markdown(
            _metric_card(
                f"{avg_score:.0f}",
                "Avg Opp Score",
                suffix="/100",
            ),
            unsafe_allow_html=True,
        )

    if not summary and not review_sum:
        st.markdown(
            '<div class="info-box">ℹ️ No intelligence data yet. '
            "Run a dental practice search to populate this dashboard.</div>",
            unsafe_allow_html=True,
        )

    # Review health breakdown bar chart
    if review_sum.get("total_analyzed", 0) > 0:
        with st.expander("📈 Review Health Distribution", expanded=False):
            chart_data = pd.DataFrame({
                "Band": [
                    "Critical (0–29)",
                    "Poor (30–49)",
                    "Fair (50–69)",
                    "Good (70–84)",
                    "Excellent (85+)",
                ],
                "Practices": [
                    review_sum.get("critical_count", 0),
                    review_sum.get("poor_count", 0),
                    review_sum.get("fair_count", 0),
                    review_sum.get("good_count", 0),
                    review_sum.get("excellent_count", 0),
                ],
            })
            st.bar_chart(chart_data.set_index("Band"))


# ─────────────────────────────────────────────────────────────
# SECTION 2 — TOP OPPORTUNITIES
# ─────────────────────────────────────────────────────────────

def _render_top_opportunities():
    """Render Section 2 — ranked opportunity table with filters."""
    st.markdown(
        '<div class="section-header">🏆 Top Opportunities</div>',
        unsafe_allow_html=True,
    )

    col_limit, col_urgency, col_search = st.columns([1, 2, 3])
    with col_limit:
        limit = st.selectbox(
            "Show",
            options=[10, 25, 50, 100],
            index=1,
            key="dental_opp_limit",
        )
    with col_urgency:
        urgency_filter = st.selectbox(
            "Urgency Filter",
            options=["All", "High", "Medium", "Low"],
            key="dental_opp_urgency",
        )
    with col_search:
        search_term = st.text_input(
            "Search by name",
            placeholder="Type to filter...",
            key="dental_opp_search",
        )

    opportunities = _load_top_opportunities(limit=limit)

    if not opportunities:
        st.markdown(
            '<div class="info-box">ℹ️ No opportunities found. '
            "Run a dental practice search first.</div>",
            unsafe_allow_html=True,
        )
        return

    # Apply filters
    filtered = opportunities

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
        rating = opp.get("rating", 0) or 0
        rows.append({
            "Practice":       opp.get("business_name", "Unknown"),
            "Location":       opp.get("address", "—"),
            "Rating":         f"{float(rating):.1f}" if rating else "—",
            "Reviews":        opp.get("review_count", 0),
            "Opp Score":      opp.get("opportunity_score", 0),
            "Urgency":        str(opp.get("urgency_band", "—")).upper(),
            "Buyer Type":     opp.get("buyer_type", "—"),
        })

    df = pd.DataFrame(rows)

    def _colour_urgency(val):
        val_lower = str(val).lower()
        if val_lower == "high":
            return "background-color:#fdecea;color:#c0392b;font-weight:700"
        elif val_lower == "medium":
            return "background-color:#fef9e7;color:#d35400;font-weight:700"
        elif val_lower == "low":
            return "background-color:#eafaf1;color:#1e8449;font-weight:700"
        return ""

    styled = (
        df.style
        .map(_colour_urgency, subset=["Urgency"])
        .map(_colour_score, subset=["Opp Score"])
        .format({"Opp Score": "{:.0f}", "Reviews": "{:,.0f}"})
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export Opportunities CSV",
        data=csv_data,
        file_name=f"dental_opportunities_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_dental_opps",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 3 — REPUTATION PROBLEMS
# ─────────────────────────────────────────────────────────────

def _render_reputation_problems():
    """Render Section 3 — practices with low ratings / reputation issues."""
    st.markdown(
        '<div class="section-header">⚠️ Reputation Problems</div>',
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
            key="rep_threshold",
        )
    with col_view:
        view_mode = st.radio(
            "View as",
            options=["Table", "Cards"],
            horizontal=True,
            key="rep_view_mode",
        )

    practices = _load_poor_reputation(threshold=threshold)

    if not practices:
        st.markdown(
            f'<div class="info-box">ℹ️ No practices with health score '
            f"below {threshold} found. "
            "Run dental review analysis to populate this section.</div>",
            unsafe_allow_html=True,
        )
        return

    st.caption(
        f"⚠️ {len(practices)} practice(s) with review health score "
        f"below {threshold} — sorted worst first"
    )

    if view_mode == "Cards":
        _render_reputation_cards(practices)
    else:
        _render_reputation_table(practices)


def _render_reputation_cards(practices: list):
    """Render reputation problem cards."""
    for p in practices[:15]:  # cap for performance
        name  = p.get("business_name", "Unknown")
        score = p.get("review_health_score", 0)
        rating = p.get("rating", 0) or 0
        count  = p.get("review_count", 0)
        problems = p.get("reputation_problems", [])

        problem_text = ""
        if problems:
            first = problems[0] if isinstance(problems[0], dict) else {}
            problem_text = first.get(
                "description",
                str(problems[0]) if problems else "",
            )

        st.markdown(
            f"""
            <div class="rep-card">
                <h4>⚠️ {name} — Health Score: {score}/100</h4>
                <p>
                    Rating: {float(rating):.1f}/5.0 &nbsp;|&nbsp;
                    Reviews: {count} &nbsp;|&nbsp;
                    Problems: {len(problems)}
                </p>
                {f'<p style="margin-top:0.4rem;font-style:italic">{problem_text}</p>' if problem_text else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_reputation_table(practices: list):
    """Render reputation problems as a DataFrame."""
    rows = []
    for p in practices:
        problems = p.get("reputation_problems", [])
        problem_types = []
        for prob in problems:
            if isinstance(prob, dict):
                problem_types.append(prob.get("problem_type", ""))
            else:
                problem_types.append(str(prob))

        rating = p.get("rating", 0) or 0
        rows.append({
            "Practice":      p.get("business_name", "Unknown"),
            "Health Score":  p.get("review_health_score", 0),
            "Rating":        f"{float(rating):.1f}" if rating else "—",
            "Reviews":       p.get("review_count", 0),
            "Problems":      len(problems),
            "Issues":        ", ".join(problem_types) if problem_types else "—",
            "Analysed":      _safe_date(p.get("analyzed_at")),
        })

    df = pd.DataFrame(rows)

    styled = df.style.map(
        _colour_score, subset=["Health Score"]
    ).format({
        "Health Score": "{:.0f}",
        "Reviews": "{:,.0f}",
    })

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export Reputation Report CSV",
        data=csv_data,
        file_name=f"dental_reputation_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key="dl_reputation",
    )


# ─────────────────────────────────────────────────────────────
# SECTION 4 — GROWING PRACTICES
# ─────────────────────────────────────────────────────────────

def _render_growing_practices():
    """Render Section 4 — practices with expansion signals."""
    st.markdown(
        '<div class="section-header">🚀 Growing Practices</div>',
        unsafe_allow_html=True,
    )

    col_days, col_view = st.columns([2, 2])
    with col_days:
        days_back = st.selectbox(
            "Signals detected in last",
            options=[7, 14, 30, 60, 90],
            index=2,
            format_func=lambda x: f"{x} days",
            key="growth_days",
        )
    with col_view:
        show_expanding = st.checkbox(
            "Show expanding groups only",
            value=False,
            key="show_expanding",
        )

    if show_expanding:
        practices = _load_expanding_groups()
        caption_text = "practices showing multi-signal expansion"
    else:
        practices = _load_growing_practices(days=days_back)
        caption_text = f"growing practices in last {days_back} days"

    if not practices:
        st.markdown(
            f'<div class="info-box">ℹ️ No {caption_text} detected yet. '
            "Growth signals are detected from Companies House, "
            "CQC, and review data.</div>",
            unsafe_allow_html=True,
        )
        return

    st.caption(f"🚀 {len(practices)} {caption_text}")

    tab_cards, tab_table = st.tabs(["Cards View", "Table View"])

    with tab_cards:
        for p in practices[:12]:
            name = p.get("business_name", "Unknown")
            sig_count = p.get("signal_count", p.get("distinct_types", 0))
            signals = p.get("signals", p.get("signal_types", []))
            expansion = p.get("expansion_type", "")
            latest = p.get("latest_signal", "")

            # Format signal list
            if signals and isinstance(signals[0], dict):
                sig_labels = [
                    s.get("signal_type", "").replace("_", " ").title()
                    for s in signals[:4]
                ]
            elif signals and isinstance(signals[0], str):
                sig_labels = [
                    s.replace("_", " ").title()
                    for s in signals[:4]
                ]
            else:
                sig_labels = []

            sig_text = " · ".join(sig_labels) if sig_labels else "—"

            expansion_badge = (
                f'<span style="background:#d5f5e3;color:#145a32;'
                f'border-radius:4px;padding:2px 8px;font-size:0.75rem;'
                f'font-weight:700">{expansion.upper()}</span>'
                if expansion
                else ""
            )

            st.markdown(
                f"""
                <div class="growth-card">
                    <h4>🚀 {name} {expansion_badge}</h4>
                    <p>
                        Signals detected: {sig_count}
                        {f'&nbsp;|&nbsp;Latest: {_safe_date(latest)}' if latest else ''}
                    </p>
                    <p style="margin-top:0.3rem;color:#145a32">
                        {sig_text}
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tab_table:
        rows = []
        for p in practices:
            signals = p.get("signals", p.get("signal_types", []))
            if signals and isinstance(signals[0], dict):
                sig_types = [
                    s.get("signal_type", "") for s in signals
                ]
            elif signals and isinstance(signals[0], str):
                sig_types = signals
            else:
                sig_types = []

            rows.append({
                "Practice":       p.get("business_name", "Unknown"),
                "Signal Count":   p.get("signal_count", p.get("distinct_types", 0)),
                "Signal Types":   ", ".join(sig_types) if sig_types else "—",
                "Expansion Type": p.get("expansion_type", "—"),
                "Latest Signal":  _safe_date(p.get("latest_signal", "")),
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Export Growing Practices CSV",
            data=csv_data,
            file_name=f"dental_growing_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="dl_growing",
        )


# ─────────────────────────────────────────────────────────────
# SECTION 5 — DENTAL GROUPS
# ─────────────────────────────────────────────────────────────

def _render_dental_groups():
    """Render Section 5 — dental chains and corporate groups."""
    st.markdown(
        '<div class="section-header">🏢 Dental Groups & Chains</div>',
        unsafe_allow_html=True,
    )

    col_min, _ = st.columns([2, 4])
    with col_min:
        min_sites = st.selectbox(
            "Minimum sites to qualify",
            options=[2, 3, 5, 10],
            index=0,
            key="dental_group_min",
        )

    all_groups   = _load_dental_groups()
    enterprise   = _load_enterprise_groups(min_sites=min_sites)

    if not all_groups:
        st.markdown(
            '<div class="info-box">ℹ️ No dental groups identified yet. '
            "Groups are detected automatically as practice data grows. "
            "Known corporate groups (Bupa, Portman, etc.) are matched "
            "via Companies House director cross-referencing.</div>",
            unsafe_allow_html=True,
        )
        return

    # Summary metrics
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Groups Detected", len(all_groups))
    with col_b:
        st.metric(f"Enterprise ({min_sites}+ sites)", len(enterprise))
    with col_c:
        total_sites = sum(
            g.get("site_count", g.get("practices_count", 0))
            for g in all_groups
        )
        st.metric("Total Sites in Groups", total_sites)

    st.divider()

    tab_all, tab_enterprise = st.tabs([
        f"All Groups ({len(all_groups)})",
        f"Enterprise Groups ({len(enterprise)})",
    ])

    with tab_all:
        _render_dental_group_cards(all_groups)

    with tab_enterprise:
        if enterprise:
            _render_dental_group_cards(enterprise)
        else:
            st.info(
                f"No dental groups with {min_sites}+ sites identified yet."
            )


def _render_dental_group_cards(groups: list):
    """Render dental group cards."""
    if not groups:
        st.info("No groups to display.")
        return

    for group in groups[:20]:
        name = group.get(
            "group_name",
            group.get("corporate_name", "Unknown Group"),
        )
        site_count = group.get(
            "site_count",
            group.get("practices_count", 0),
        )
        group_type = group.get("group_type", "detected")
        director   = group.get("director_name", "")
        practices  = group.get("practices", group.get("locations", []))

        practice_preview = ""
        if practices:
            names = [
                str(p.get("business_name", p))
                if isinstance(p, dict)
                else str(p)
                for p in practices[:3]
            ]
            practice_preview = ", ".join(names)
            if len(practices) > 3:
                practice_preview += f" +{len(practices) - 3} more"

        st.markdown(
            f"""
            <div class="group-card">
                <h4>🦷 {name}</h4>
                <p>
                    <strong>Sites:</strong> {site_count}
                    &nbsp;|&nbsp;
                    <strong>Type:</strong> {group_type}
                    {f'&nbsp;|&nbsp;<strong>Director:</strong> {director}' if director else ''}
                </p>
                {f'<p style="margin-top:0.3rem;color:#777">{practice_preview}</p>' if practice_preview else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────
# SECTION 6 — BUYER TYPE FILTER
# ─────────────────────────────────────────────────────────────

BUYER_TYPES = {
    "all":              "🔍 All Practices",
    "nhs":              "🏥 NHS Practices",
    "private":          "💰 Private Practices",
    "mixed":            "⚖️ Mixed NHS/Private",
    "cosmetic":         "✨ Cosmetic Focus",
    "orthodontic":      "🦷 Orthodontic Focus",
    "corporate":        "🏢 Corporate / Chain",
    "independent":      "👤 Independent Owner",
    "newly_registered": "🆕 Newly Registered",
}


def _render_buyer_type_filter():
    """Render Section 6 — buyer type filter dropdown."""
    st.markdown(
        '<div class="section-header">🎯 Buyer Type Filter</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="info-box">Filter dental practices by buyer profile '
        "to target the right decision-maker with the right offer.</div>",
        unsafe_allow_html=True,
    )

    buyer_display = list(BUYER_TYPES.values())
    buyer_keys    = list(BUYER_TYPES.keys())

    col_type, col_limit = st.columns([3, 1])
    with col_type:
        selected_display = st.selectbox(
            "Filter by buyer type:",
            options=buyer_display,
            key="dental_buyer_type",
        )
    with col_limit:
        show_n = st.selectbox(
            "Show top",
            options=[10, 25, 50],
            index=0,
            key="dental_buyer_limit",
        )

    selected_idx  = buyer_display.index(selected_display)
    selected_type = buyer_keys[selected_idx]

    with st.spinner(f"Loading {selected_display} opportunities..."):
        matches = _load_buyer_type_opportunities(selected_type)

    if not matches:
        st.markdown(
            f'<div class="info-box">ℹ️ No matches found for '
            f"<strong>{selected_display}</strong>. "
            "Run a dental search to populate opportunities.</div>",
            unsafe_allow_html=True,
        )
        return

    matches = matches[:show_n]

    st.success(
        f"✅ {len(matches)} practices matched for {selected_display}"
    )

    rows = []
    for m in matches:
        rating = m.get("rating", 0) or 0
        rows.append({
            "Practice":    m.get("business_name", "Unknown"),
            "Location":    m.get("address", "—"),
            "Rating":      f"{float(rating):.1f}" if rating else "—",
            "Reviews":     m.get("review_count", 0),
            "Opp Score":   m.get("opportunity_score", 0),
            "Urgency":     str(m.get("urgency_band", "—")).upper(),
            "Buyer Type":  m.get("buyer_type", selected_type),
        })

    df = pd.DataFrame(rows)

    def _colour_urgency_cell(val):
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
        .map(_colour_urgency_cell, subset=["Urgency"])
        .map(_colour_score, subset=["Opp Score"])
        .format({"Opp Score": "{:.0f}", "Reviews": "{:,.0f}"})
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"⬇️ Export {selected_display} CSV",
        data=csv_data,
        file_name=(
            f"dental_{selected_type}_"
            f"{datetime.now().strftime('%Y%m%d')}.csv"
        ),
        mime="text/csv",
        key="dl_buyer_type",
    )


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────

def _render_sidebar():
    """Render sidebar controls and module status."""
    with st.sidebar:
        st.markdown("### 🦷 Dental Intelligence")
        st.caption("KNCS LeadGen Pro — Phase 1")
        st.divider()

        if st.button(
            "🔄 Refresh All Data",
            use_container_width=True,
            key="dental_refresh_all",
        ):
            st.cache_data.clear()
            st.success("Cache cleared — reloading...")
            st.rerun()

        st.divider()

        # Batch analysis triggers
        st.markdown("**Run Analysis**")

        if st.button(
            "📊 Analyse Reviews (Batch)",
            use_container_width=True,
            key="dental_run_review_batch",
        ):
            ra = _safe_import_review_analyzer()
            if ra and hasattr(ra, "batch_analyze_all_dental_leads"):
                with st.spinner("Analysing reviews..."):
                    try:
                        result = ra.batch_analyze_all_dental_leads(
                            batch_size=50
                        )
                        st.success(
                            f"✅ Processed {result.get('processed', 0)} "
                            f"| Succeeded {result.get('succeeded', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Batch review analysis failed: %s", exc)
                        st.error("Review analysis failed. Check logs.")
            else:
                st.warning("Review analyser not available.")

        if st.button(
            "🚀 Scan Growth Signals (Batch)",
            use_container_width=True,
            key="dental_run_growth_batch",
        ):
            gs = _safe_import_growth_signals()
            if gs and hasattr(gs, "batch_scan_dental_leads"):
                with st.spinner("Scanning for growth signals..."):
                    try:
                        result = gs.batch_scan_dental_leads(batch_size=50)
                        st.success(
                            f"✅ Processed {result.get('processed', 0)} "
                            f"| Signals {result.get('signals_found', 0)} "
                            f"| Failed {result.get('failed', 0)}"
                        )
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Batch growth scan failed: %s", exc)
                        st.error("Growth scan failed. Check logs.")
            else:
                st.warning("Growth signal scanner not available.")

        if st.button(
            "🏢 Detect Dental Groups",
            use_container_width=True,
            key="dental_run_groups",
        ):
            gd = _safe_import_group_detector()
            if gd and hasattr(gd, "link_practices_to_groups"):
                with st.spinner("Detecting groups..."):
                    try:
                        linked = gd.link_practices_to_groups()
                        st.success(f"✅ Linked {linked} practices to groups")
                        st.cache_data.clear()
                    except Exception as exc:
                        log.error("Group detection failed: %s", exc)
                        st.error("Group detection failed. Check logs.")
            else:
                st.warning("Group detector not available.")

        st.divider()

        # Module status
        st.markdown("**Module Status**")
        modules = {
            "Dental Intelligence":  _safe_import_dental_intelligence(),
            "Group Detector":       _safe_import_group_detector(),
            "Review Analyzer":      _safe_import_review_analyzer(),
            "Growth Signals":       _safe_import_growth_signals(),
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
    Main entry point for the Dental Intelligence dashboard page.

    Called by app.py navigation router.
    All 6 sections render independently — failure in one
    does not block the others.
    """
    _inject_css()
    _render_sidebar()

    # Page header
    st.markdown(
        """
        <div class="dental-header">
            <h1>🦷 Dental Practice Intelligence</h1>
            <p>
                Review analysis · Reputation scoring · Growth signals ·
                Group detection · Buyer targeting — Phase 1 vertical module
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Section 1: Practice Summary ───────────────────────────
    try:
        _render_practice_summary()
    except Exception as exc:
        log.error("Section 1 failed: %s", exc)
        st.error("Practice summary failed to load.")

    st.divider()

    # ── Section 2: Top Opportunities ─────────────────────────
    try:
        _render_top_opportunities()
    except Exception as exc:
        log.error("Section 2 failed: %s", exc)
        st.error("Top opportunities failed to load.")

    st.divider()

    # ── Section 3: Reputation Problems ───────────────────────
    try:
        _render_reputation_problems()
    except Exception as exc:
        log.error("Section 3 failed: %s", exc)
        st.error("Reputation problems section failed to load.")

    st.divider()

    # ── Section 4: Growing Practices ─────────────────────────
    try:
        _render_growing_practices()
    except Exception as exc:
        log.error("Section 4 failed: %s", exc)
        st.error("Growing practices section failed to load.")

    st.divider()

    # ── Section 5: Dental Groups ─────────────────────────────
    try:
        _render_dental_groups()
    except Exception as exc:
        log.error("Section 5 failed: %s", exc)
        st.error("Dental groups section failed to load.")

    st.divider()

    # ── Section 6: Buyer Type Filter ─────────────────────────
    try:
        _render_buyer_type_filter()
    except Exception as exc:
        log.error("Section 6 failed: %s", exc)
        st.error("Buyer type filter failed to load.")


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN GUARD
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    show()