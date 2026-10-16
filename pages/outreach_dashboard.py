"""
KNCS Outreach Dashboard
Website audit + cold email sending in one place
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional
import html as _html_module
import re

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────
# Safe imports
# ─────────────────────────────────────────────────────────
try:
    from engine.website_auditor import (
        WebsiteAuditor,
        audit_lead_website,
        INDUSTRY_PROFILES,
        get_profile,
        _detect_industry,
    )
    _AUDITOR_AVAILABLE = True
except Exception:
    _AUDITOR_AVAILABLE  = False
    WebsiteAuditor      = None
    audit_lead_website  = None
    INDUSTRY_PROFILES   = {}
    def get_profile(k): return {}
    def _detect_industry(l): return "generic"

try:
    from engine.email_outreach import ColdEmailSender, EmailTemplateEngine
    _EMAIL_AVAILABLE    = True
except Exception:
    _EMAIL_AVAILABLE    = False
    ColdEmailSender     = None
    EmailTemplateEngine = None

try:
    from database import get_all_leads
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    def get_all_leads() -> list: return []

# ─────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────
_BG_SURFACE = "#0b1628"
_BG_RAISED  = "#101f38"
_BRAND_BLUE = "#5b8cff"
_BRAND_PUR  = "#7c5cff"
_GREEN      = "#10b981"
_GOLD       = "#f59e0b"
_RED        = "#ef4444"
_TEXT_PRI   = "#f1f5f9"
_TEXT_SEC   = "#94a3b8"
_TEXT_MUT   = "#475569"
_BORDER     = "rgba(255,255,255,0.05)"

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def _esc(value) -> str:
    return _html_module.escape(
        str(value if value is not None else "")
    )


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _safe_str(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002500-\U00002BFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "\u2640-\u2642"
        "\u2600-\u2B55"
        "\u200d\u23cf\u23e9\u231a\ufe0f\u3030"
        "]+",
        flags=re.UNICODE,
    )
    cleaned = emoji_pattern.sub("", text)
    cleaned = re.sub(r"  +", " ", cleaned).strip()
    return cleaned if cleaned else default


def _priority_color(priority: str) -> str:
    return {
        "URGENT": _RED,
        "HIGH":   _GOLD,
        "MEDIUM": _BRAND_BLUE,
        "LOW":    _GREEN,
    }.get(str(priority).upper(), _TEXT_MUT)


def _score_color(score: int) -> str:
    if score >= 8: return _GREEN
    if score >= 5: return _GOLD
    return _RED


def _grade_color(grade: str) -> str:
    return {
        "A+": _GREEN, "A": _GREEN, "B": _GREEN,
        "C": _GOLD,   "D": _GOLD,
        "E": _RED,    "F": _RED,
    }.get(str(grade).upper()[:2], _TEXT_MUT)


# ─────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────
def _render_css() -> None:
    st.markdown("""
<style>
.oa-hero {
    background: linear-gradient(145deg,#0b1628 0%,#0e1e35 50%,#0b1628 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 22px; padding: 28px 32px;
    margin-bottom: 24px; position: relative; overflow: hidden;
}
.oa-hero-orb {
    position: absolute; top: -60px; right: -60px;
    width: 260px; height: 260px;
    background: radial-gradient(circle,rgba(91,140,255,0.10) 0%,transparent 70%);
    border-radius: 50%; pointer-events: none;
}
.oa-hero-eyebrow {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 2px; color: #5b8cff; margin-bottom: 8px;
}
.oa-hero-title {
    font-size: 28px; font-weight: 900; color: #f1f5f9;
    letter-spacing: -0.8px; margin: 0 0 6px 0;
}
.oa-hero-sub { font-size: 13px; color: #64748b; font-weight: 500; }

.oa-metric {
    background: linear-gradient(145deg,#0b1628,#101f38);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 16px 18px; text-align: center;
}
.oa-metric-icon  { font-size: 22px; margin-bottom: 6px; }
.oa-metric-label {
    font-size: 9.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1.2px; color: #475569; margin-bottom: 6px;
}
.oa-metric-value {
    font-size: 28px; font-weight: 900; color: #f1f5f9;
    letter-spacing: -1px; line-height: 1;
}
.oa-metric-sub { font-size: 11px; color: #475569; margin-top: 4px; }

.oa-sec-title {
    font-size: 18px; font-weight: 800; color: #f1f5f9;
    letter-spacing: -0.4px; margin: 0 0 4px 0;
}
.oa-sec-sub { font-size: 12px; color: #475569; margin: 0 0 18px 0; }

.oa-result-card {
    background: linear-gradient(145deg,#0b1628,#101f38);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 20px; padding: 24px 28px; margin-top: 20px;
    animation: result-in 0.22s ease;
}
@keyframes result-in {
    from { opacity:0; transform:translateY(-8px); }
    to   { opacity:1; transform:translateY(0); }
}
.oa-result-top {
    display: flex; align-items: flex-start;
    gap: 20px; margin-bottom: 20px; flex-wrap: wrap;
}
.oa-score-circle {
    width: 90px; height: 90px; border-radius: 50%;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    flex-shrink: 0; border: 3px solid;
}
.oa-score-num  { font-size: 30px; font-weight: 900; line-height: 1; }
.oa-score-lbl  {
    font-size: 9px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; margin-top: 2px; opacity: 0.7;
}
.oa-result-meta { flex: 1; min-width: 180px; }
.oa-result-biz  {
    font-size: 18px; font-weight: 800; color: #f1f5f9; margin-bottom: 6px;
}
.oa-result-url  {
    font-size: 12px; color: #5b8cff;
    margin-bottom: 10px; word-break: break-all;
}
.oa-badge {
    display: inline-block; border-radius: 999px;
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.6px;
    padding: 3px 10px; margin-right: 6px; border: 1px solid;
}
.oa-result-section {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 14px 16px; margin-bottom: 12px;
}
.oa-result-section-title {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 10px;
}
.oa-result-item {
    font-size: 13px; color: #cbd5e1; padding: 4px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04); line-height: 1.5;
}
.oa-result-item:last-child { border-bottom: none; }

.oa-hook-box {
    background: rgba(91,140,255,0.06);
    border: 1px solid rgba(91,140,255,0.18);
    border-radius: 12px; padding: 14px 16px;
    font-size: 13px; color: #cbd5e1;
    line-height: 1.7; font-style: italic; margin-bottom: 12px;
}
.oa-hook-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #5b8cff;
    margin-bottom: 6px; font-style: normal;
}
.oa-rec-box {
    background: rgba(16,185,129,0.06);
    border: 1px solid rgba(16,185,129,0.18);
    border-radius: 12px; padding: 14px 16px;
    font-size: 13px; color: #cbd5e1;
    line-height: 1.7; margin-bottom: 12px;
}
.oa-rec-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 1px; color: #10b981; margin-bottom: 6px;
}
.oa-industry-tag {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: #7c5cff;
    margin-bottom: 10px;
}

.oa-info {
    background: rgba(91,140,255,0.06);
    border: 1px solid rgba(91,140,255,0.15);
    border-radius: 14px; padding: 16px 18px;
    font-size: 13px; color: #94a3b8;
    line-height: 1.7; margin-bottom: 16px;
}
.oa-warn {
    background: rgba(245,158,11,0.07);
    border: 1px solid rgba(245,158,11,0.20);
    border-radius: 14px; padding: 16px 18px;
    font-size: 13px; color: #fcd34d;
    line-height: 1.7; margin-bottom: 16px;
}
.oa-error {
    background: rgba(239,68,68,0.07);
    border: 1px solid rgba(239,68,68,0.20);
    border-radius: 14px; padding: 16px 18px;
    font-size: 13px; color: #fca5a5;
    line-height: 1.7; margin-bottom: 16px;
}
.oa-success {
    background: rgba(16,185,129,0.07);
    border: 1px solid rgba(16,185,129,0.20);
    border-radius: 14px; padding: 16px 18px;
    font-size: 13px; color: #6ee7b7;
    line-height: 1.7; margin-bottom: 16px;
}

.oa-email-preview {
    background: linear-gradient(145deg,#0b1628,#101f38);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px; padding: 20px 22px; margin-bottom: 16px;
}
.oa-email-field {
    display: flex; gap: 10px; padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px;
}
.oa-email-field:last-child { border-bottom: none; }
.oa-email-field-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: #475569;
    min-width: 70px; padding-top: 1px;
}
.oa-email-field-value { color: #cbd5e1; flex: 1; }

.oa-settings-card {
    background: linear-gradient(145deg,#0b1628,#101f38);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 18px; padding: 24px 28px; margin-bottom: 16px;
}
.oa-divider {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.05);
    margin: 20px 0;
}
.oa-progress-label {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    color: #5b8cff; margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────
# Hero
# ─────────────────────────────────────────────────────────
def _render_hero(
    audited_count: int,
    urgent_count:  int,
    high_count:    int,
    sent_count:    int,
) -> None:
    st.markdown(
        '<div class="oa-hero">'
        '<div class="oa-hero-orb"></div>'
        '<div style="position:relative;z-index:1;">'
        '<div class="oa-hero-eyebrow">Email Outreach</div>'
        '<div class="oa-hero-title">'
        '&#128231; Cold Email Outreach'
        '</div>'
        '<div class="oa-hero-sub">'
        'Audit websites &#8594; prioritise leads '
        '&#8594; send personalised cold emails'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    metrics = [
        ("&#128202;", "Leads Audited", str(audited_count), "",             _BRAND_BLUE),
        ("&#128308;", "Urgent",        str(urgent_count),  "No website",   _RED),
        ("&#128992;", "High Priority", str(high_count),    "Score 1-4",    _GOLD),
        ("&#128231;", "Emails Sent",   str(sent_count),    "This session", _GREEN),
        ("&#128683;", "Daily Limit",   "40",               "Gmail safe",   _TEXT_MUT),
    ]
    for col, (icon, label, value, sub, color) in zip(
        [c1, c2, c3, c4, c5], metrics
    ):
        with col:
            st.markdown(
                '<div class="oa-metric">'
                '<div class="oa-metric-icon">' + icon + '</div>'
                '<div class="oa-metric-label">' + label + '</div>'
                '<div class="oa-metric-value" style="color:'
                + color + ';">' + value + '</div>'
                '<div class="oa-metric-sub">' + sub + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────
# Audit result card
# ─────────────────────────────────────────────────────────
def _render_audit_result(
    result:        dict,
    business_name: str,
    url:           str,
) -> None:
    score    = _safe_int(result.get("score", 0))
    grade    = _safe_str(result.get("grade",          "N/A"))
    priority = _safe_str(result.get("priority",       "N/A"))
    industry = _safe_str(result.get("industry",       "Business"))
    rec      = _safe_str(result.get("recommendation", "No recommendation."))
    hook     = _safe_str(result.get("email_hook",     "No hook generated."))

    raw_issues    = result.get("issues",    [])
    raw_strengths = result.get("strengths", [])
    issues    = [
        _safe_str(i) for i in
        (raw_issues if isinstance(raw_issues, list) else [])
        if _safe_str(i)
    ]
    strengths = [
        _safe_str(s) for s in
        (raw_strengths if isinstance(raw_strengths, list) else [])
        if _safe_str(s)
    ]

    sc = _score_color(score)
    gc = _grade_color(grade)
    pc = _priority_color(priority)

    circle_html = (
        '<div class="oa-score-circle" style="'
        'border-color:' + sc + ';'
        'background:' + sc + '18;">'
        '<div class="oa-score-num" style="color:' + sc + ';">'
        + str(score) + '</div>'
        '<div class="oa-score-lbl" style="color:' + sc + ';">/ 10</div>'
        '</div>'
    )

    grade_badge = (
        '<span class="oa-badge" style="color:' + gc + ';'
        'border-color:' + gc + '44;background:' + gc + '18;">'
        'Grade ' + _esc(grade) + '</span>'
    )
    priority_badge = (
        '<span class="oa-badge" style="color:' + pc + ';'
        'border-color:' + pc + '44;background:' + pc + '18;">'
        + _esc(priority) + '</span>'
    )

    issues_html = ""
    if issues:
        items = "".join(
            '<div class="oa-result-item">'
            '&#10060; ' + _esc(i) + '</div>'
            for i in issues
        )
        issues_html = (
            '<div class="oa-result-section">'
            '<div class="oa-result-section-title" style="color:'
            + _RED + ';">&#9888; Issues Found</div>'
            + items +
            '</div>'
        )

    strengths_html = ""
    if strengths:
        items = "".join(
            '<div class="oa-result-item">'
            '&#9989; ' + _esc(s) + '</div>'
            for s in strengths
        )
        strengths_html = (
            '<div class="oa-result-section">'
            '<div class="oa-result-section-title" style="color:'
            + _GREEN + ';">&#9989; Strengths</div>'
            + items +
            '</div>'
        )

    st.markdown(
        '<div class="oa-result-card">'
        '<div class="oa-result-top">'
        + circle_html +
        '<div class="oa-result-meta">'
        '<div class="oa-industry-tag">&#127981; '
        + _esc(industry) + '</div>'
        '<div class="oa-result-biz">' + _esc(business_name) + '</div>'
        '<div class="oa-result-url">'
        '<a href="' + _esc(url) + '" target="_blank" '
        'style="color:#5b8cff;">' + _esc(url) + '</a>'
        '</div>'
        + grade_badge + priority_badge +
        '</div>'
        '</div>'
        '<div class="oa-rec-box">'
        '<div class="oa-rec-label">&#128161; Recommendation</div>'
        + _esc(rec) +
        '</div>'
        '<div class="oa-hook-box">'
        '<div class="oa-hook-label">&#128172; Email Hook</div>'
        '&ldquo;' + _esc(hook) + '&rdquo;'
        '</div>'
        + issues_html
        + strengths_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────
# TAB 1 — Website Audit
# ─────────────────────────────────────────────────────────
def _tab_audit() -> None:

    st.markdown(
        '<div class="oa-sec-title">Website Audit</div>'
        '<div class="oa-sec-sub">'
        'Score any business website 1&#8211;10. '
        'Detects industry automatically and applies relevant checks.'
        '</div>',
        unsafe_allow_html=True,
    )

    # BUG-EO-02 FIXED: oa-error -> oa-info (capability warning, not crash)
    # return is intentionally kept — removing it causes TypeError when
    # _AUDITOR_AVAILABLE is False and WebsiteAuditor is None.
    if not _AUDITOR_AVAILABLE:
        st.markdown(
            '<div class="oa-info">'
            '&#9888; Website auditor engine not available. '
            'Check engine/website_auditor.py is present.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    industry_options = {
        "Auto-detect":            "",
        "Generic Business":       "generic",
        "Broker / Financial":     "broker",
        "Care Home":              "care_home",
        "Retail / E-commerce":    "retail",
        "Hospitality":            "hospitality",
        "Professional Services":  "professional_services",
    }

    selected_industry_label = st.selectbox(
        "Industry (auto-detected from lead data — override here)",
        options=list(industry_options.keys()),
        index=0,
        key="industry_override",
    )
    selected_industry_key = industry_options[selected_industry_label]

    st.markdown('<hr class="oa-divider">', unsafe_allow_html=True)

    st.markdown(
        '<div class="oa-sec-title" style="font-size:15px;">'
        'Audit All Leads</div>',
        unsafe_allow_html=True,
    )

    all_c1, all_c2 = st.columns([3, 1])

    with all_c1:
        st.markdown(
            '<div class="oa-info">'
            'Load your leads from the database and score every website '
            'automatically. Industry is detected per lead. Results are '
            'sorted by priority so you know who to contact first.'
            '</div>',
            unsafe_allow_html=True,
        )

    with all_c2:
        if st.button(
            "Audit All Websites",
            type="primary",
            use_container_width=True,
            key="audit_all_btn",
        ):
            if not _DB_AVAILABLE:
                st.markdown(
                    '<div class="oa-error">'
                    '&#9888; Database not available.'
                    '</div>',
                    unsafe_allow_html=True,
                )
            else:
                try:
                    leads = get_all_leads()
                    if not leads:
                        st.markdown(
                            '<div class="oa-warn">'
                            '&#9888; No leads in database. '
                            'Generate leads first.'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        results  = []
                        st.markdown(
                            '<div class="oa-progress-label">'
                            'Auditing Websites</div>',
                            unsafe_allow_html=True,
                        )
                        progress = st.progress(0.0)
                        status   = st.empty()

                        for i, lead in enumerate(leads):
                            progress.progress(
                                float(i + 1)
                                / float(max(len(leads), 1))
                            )
                            bname = _safe_str(
                                lead.get("business_name"), "Unknown"
                            )
                            status.markdown(
                                "<div style='font-size:12px;color:"
                                + _TEXT_SEC + ";padding:4px 0;'>"
                                "Auditing <b>" + str(i + 1)
                                + "</b>/" + str(len(leads))
                                + " &#8212; " + _esc(bname)
                                + "</div>",
                                unsafe_allow_html=True,
                            )
                            try:
                                if selected_industry_key:
                                    auditor = WebsiteAuditor(
                                        industry_key=selected_industry_key
                                    )
                                    url  = _safe_str(lead.get("website", ""))
                                    name = _safe_str(lead.get("business_name", ""))
                                    audit_result = auditor.audit_website(
                                        url, name,
                                        industry_key=selected_industry_key,
                                    )
                                    lead.update({
                                        "website_score":
                                            audit_result["score"],
                                        "website_grade":
                                            audit_result["grade"],
                                        "website_priority":
                                            audit_result["priority"],
                                        "website_issues":
                                            audit_result["issues"],
                                        "website_recommendation":
                                            audit_result["recommendation"],
                                        "website_email_hook":
                                            audit_result["email_hook"],
                                        "website_should_email":
                                            audit_result["should_email"],
                                        "website_industry":
                                            audit_result.get(
                                                "industry", "Business"
                                            ),
                                        "website_audit_full":
                                            audit_result,
                                    })
                                    audited_lead = lead
                                else:
                                    audited_lead = audit_lead_website(lead)
                            except Exception:
                                audited_lead = lead
                            results.append(audited_lead)
                            time.sleep(1)

                        st.session_state.audited_leads = results
                        progress.progress(1.0)
                        status.markdown(
                            "<div style='color:" + _GREEN + ";"
                            "font-size:13px;font-weight:700;'>"
                            "&#9989; Audit complete &#8212; "
                            + str(len(results)) + " leads scored"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                        st.rerun()

                except Exception as e:
                    st.markdown(
                        '<div class="oa-error">'
                        '&#9888; Error loading leads: '
                        + _esc(str(e)) +
                        '</div>',
                        unsafe_allow_html=True,
                    )

    st.markdown('<hr class="oa-divider">', unsafe_allow_html=True)

    st.markdown(
        '<div class="oa-sec-title" style="font-size:15px;">'
        'Audit a Single Website</div>'
        '<div class="oa-sec-sub">'
        'Test any URL instantly. Industry auto-detected or use '
        'the selector above to override.'
        '</div>',
        unsafe_allow_html=True,
    )

    in_c1, in_c2 = st.columns([2, 2])
    with in_c1:
        test_url = st.text_input(
            "Website URL",
            placeholder="https://yieldcollective.co",
            key="single_audit_url",
        )
    with in_c2:
        test_name = st.text_input(
            "Business Name",
            placeholder="Yield Collective",
            key="single_audit_name",
        )

    if not test_name:
        st.markdown(
            "<div style='font-size:11px;color:" + _TEXT_MUT
            + ";margin-bottom:8px;'>"
            "&#128161; Enter the full business name for accurate "
            "industry detection and a personalised email hook."
            "</div>",
            unsafe_allow_html=True,
        )

    audit_clicked = st.button(
        "Audit This Site",
        use_container_width=True,
        key="single_audit_btn",
        type="primary",
    )

    if audit_clicked:
        if not test_url:
            st.markdown(
                '<div class="oa-warn">'
                '&#9888; Please enter a website URL first.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            name = _safe_str(test_name) or "Unknown Business"
            if selected_industry_key:
                industry_key = selected_industry_key
            else:
                industry_key = _detect_industry(
                    {"business_name": name, "industry": ""}
                )

            with st.spinner(
                "Auditing " + test_url
                + " as " + get_profile(industry_key).get(
                    "label", "Business"
                ) + "..."
            ):
                try:
                    auditor = WebsiteAuditor(industry_key=industry_key)
                    result  = auditor.audit_website(
                        test_url, name,
                        industry_key=industry_key,
                    )
                    st.session_state["single_audit_result"]      = result
                    st.session_state["single_audit_result_url"]  = test_url
                    st.session_state["single_audit_result_name"] = name
                except Exception as e:
                    st.markdown(
                        '<div class="oa-error">'
                        '&#9888; Audit failed: '
                        + _esc(str(e)) +
                        '</div>',
                        unsafe_allow_html=True,
                    )

    if st.session_state.get("single_audit_result"):
        _render_audit_result(
            result        = st.session_state["single_audit_result"],
            business_name = st.session_state.get(
                "single_audit_result_name", "Unknown"
            ),
            url           = st.session_state.get(
                "single_audit_result_url", ""
            ),
        )

        if st.button("Clear Result", key="clear_single_result"):
            st.session_state.pop("single_audit_result",      None)
            st.session_state.pop("single_audit_result_url",  None)
            st.session_state.pop("single_audit_result_name", None)
            st.rerun()

    st.markdown('<hr class="oa-divider">', unsafe_allow_html=True)

    if st.session_state.audited_leads:
        st.markdown(
            '<div class="oa-sec-title" style="font-size:15px;">'
            'Audited Leads &#8212; Prioritised for Outreach</div>'
            '<div class="oa-sec-sub">'
            'Sorted by priority. Filter to find your best opportunities.'
            '</div>',
            unsafe_allow_html=True,
        )

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            show_priority = st.multiselect(
                "Priority",
                ["URGENT", "HIGH", "MEDIUM", "LOW"],
                default=["URGENT", "HIGH"],
                key="audit_priority_filter",
            )
        with fc2:
            show_only_email = st.checkbox(
                "Only leads WITH email",
                key="audit_email_filter",
            )
        with fc3:
            min_score = st.slider(
                "Min Score",
                min_value=0,
                max_value=10,
                value=0,
                key="audit_score_filter",
            )

        filtered = [
            l for l in st.session_state.audited_leads
            if _safe_str(l.get("website_priority"), "LOW")
            in show_priority
            and _safe_int(l.get("website_score", 0)) >= min_score
        ]
        if show_only_email:
            filtered = [l for l in filtered if l.get("email")]

        if not filtered:
            st.markdown(
                '<div class="oa-info">'
                '&#128269; No leads match the selected filters.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            rows = []
            for l in filtered:
                rows.append({
                    "Business":       _safe_str(l.get("business_name")),
                    "Industry":       _safe_str(l.get("website_industry")),
                    "Score":          _safe_int(l.get("website_score")),
                    "Grade":          _safe_str(l.get("website_grade")),
                    "Priority":       _safe_str(l.get("website_priority")),
                    "Issues":         _safe_int(l.get("issues_count")),
                    "Email":          _safe_str(l.get("email"), "None"),
                    "Decision Maker": _safe_str(l.get("decision_maker_name")),
                    "Recommendation": _safe_str(l.get("website_recommendation")),
                })

            df = pd.DataFrame(rows)

            def _colour_priority(val: str) -> str:
                colours = {
                    "URGENT": "background-color:#3b0f0f;color:#fca5a5",
                    "HIGH":   "background-color:#3b2800;color:#fcd34d",
                    "MEDIUM": "background-color:#0f1e3b;color:#93c5fd",
                    "LOW":    "background-color:#0f2e1a;color:#6ee7b7",
                }
                return colours.get(str(val).upper(), "")

            styled = df.style.map(
                _colour_priority, subset=["Priority"]
            )
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                height=min(600, 60 + len(rows) * 38),
            )

            st.markdown(
                "<div style='font-size:11px;color:" + _TEXT_MUT
                + ";margin-bottom:8px;'>"
                "Showing <b style='color:" + _TEXT_SEC + ";'>"
                + str(len(filtered)) + "</b> of "
                + str(len(st.session_state.audited_leads))
                + " audited leads.</div>",
                unsafe_allow_html=True,
            )

            csv = df.to_csv(index=False)
            st.download_button(
                "Download Audit Report CSV",
                data=csv,
                file_name="website_audit_"
                + datetime.now().strftime("%Y%m%d_%H%M")
                + ".csv",
                mime="text/csv",
                key="dl_audit_report",
            )
    else:
        st.markdown(
            '<div class="oa-info">'
            '&#128202; No leads audited yet. '
            'Click <b>Audit All Websites</b> above or test a single URL.'
            '</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────
# TAB 2 — Send Emails
# ─────────────────────────────────────────────────────────
def _tab_send_emails() -> None:

    st.markdown(
        '<div class="oa-sec-title">Send Cold Emails</div>'
        '<div class="oa-sec-sub">'
        'Review the email preview, set your batch size, '
        'then send to your prioritised leads.'
        '</div>',
        unsafe_allow_html=True,
    )

    if not _EMAIL_AVAILABLE:
        st.markdown(
            '<div class="oa-error">'
            '&#9888; Email outreach engine not available. '
            'Check engine/email_outreach.py is present.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    if not st.session_state.audited_leads:
        st.markdown(
            '<div class="oa-warn">'
            '&#9888; Run the Website Audit tab first to '
            'prioritise your leads before sending emails.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    ready_leads = [
        l for l in st.session_state.audited_leads
        if l.get("email") and l.get("website_should_email", True)
    ]

    if not ready_leads:
        st.markdown(
            '<div class="oa-info">'
            '&#128231; No leads with email addresses found. '
            'Enrich more leads with email addresses first.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        "<div style='font-size:13px;color:" + _TEXT_SEC
        + ";margin-bottom:16px;'>"
        "<b style='color:" + _TEXT_PRI + ";'>"
        + str(len(ready_leads)) + "</b>"
        " leads ready to email."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="oa-sec-title" style="font-size:15px;">'
        'Email Preview</div>',
        unsafe_allow_html=True,
    )

    preview_lead = ready_leads[0]

    try:
        template_engine = EmailTemplateEngine()
        email_data      = template_engine.generate_email(
            preview_lead, sequence=1
        )
    except Exception as e:
        st.markdown(
            '<div class="oa-error">'
            '&#9888; Could not generate email preview: '
            + _esc(str(e)) +
            '</div>',
            unsafe_allow_html=True,
        )
        email_data = {}

    if email_data:
        prev_c1, prev_c2 = st.columns([1, 1])
        with prev_c1:
            st.markdown(
                '<div class="oa-email-preview">'
                '<div class="oa-email-field">'
                '<div class="oa-email-field-label">To</div>'
                '<div class="oa-email-field-value">'
                + _esc(_safe_str(preview_lead.get("email"))) +
                '</div></div>'
                '<div class="oa-email-field">'
                '<div class="oa-email-field-label">Subject</div>'
                '<div class="oa-email-field-value">'
                + _esc(_safe_str(email_data.get("subject"))) +
                '</div></div>'
                '<div class="oa-email-field">'
                '<div class="oa-email-field-label">Template</div>'
                '<div class="oa-email-field-value">'
                + _esc(_safe_str(email_data.get("template_type"))) +
                '</div></div>'
                '<div class="oa-email-field">'
                '<div class="oa-email-field-label">Score</div>'
                '<div class="oa-email-field-value">'
                + str(_safe_int(preview_lead.get("website_score")))
                + "/10</div></div>"
                '<div class="oa-email-field">'
                '<div class="oa-email-field-label">Priority</div>'
                '<div class="oa-email-field-value">'
                + _esc(_safe_str(preview_lead.get("website_priority"))) +
                '</div></div>'
                '</div>',
                unsafe_allow_html=True,
            )
        with prev_c2:
            st.text_area(
                "Email Body",
                value=_safe_str(email_data.get("text")),
                height=240,
                key="email_preview_body",
            )

    st.markdown('<hr class="oa-divider">', unsafe_allow_html=True)
    st.markdown(
        '<div class="oa-sec-title" style="font-size:15px;">'
        'Send Controls</div>',
        unsafe_allow_html=True,
    )

    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        max_to_send = st.number_input(
            "Max emails to send",
            min_value=1, max_value=40, value=10,
            key="max_emails_input",
        )
    with sc2:
        delay_seconds = st.number_input(
            "Delay between emails (seconds)",
            min_value=30, max_value=300, value=60,
            help="Minimum 30s recommended",
            key="email_delay_input",
        )
    with sc3:
        st.markdown(
            "<div style='height:26px'></div>",
            unsafe_allow_html=True,
        )
        send_button = st.button(
            "Send "
            + str(min(int(max_to_send), len(ready_leads)))
            + " Emails",
            type="primary",
            use_container_width=True,
            key="send_emails_btn",
        )

    if send_button:
        try:
            sender = ColdEmailSender()
        except Exception as e:
            st.markdown(
                '<div class="oa-error">'
                '&#9888; Could not initialise sender: '
                + _esc(str(e)) +
                '</div>',
                unsafe_allow_html=True,
            )
            return

        if not getattr(sender, "gmail_address", None):
            st.markdown(
                '<div class="oa-error">'
                '&#9888; Gmail credentials not configured. '
                'Go to the Settings tab.'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        leads_to_send   = ready_leads[: int(max_to_send)]
        template_engine = EmailTemplateEngine()

        st.markdown(
            '<div class="oa-progress-label">Sending Emails</div>',
            unsafe_allow_html=True,
        )
        progress    = st.progress(0.0)
        status      = st.empty()
        results_log = []

        for i, lead in enumerate(leads_to_send):
            progress.progress(
                float(i + 1) / float(max(len(leads_to_send), 1))
            )
            email_addr = _safe_str(lead.get("email"))
            status.markdown(
                "<div style='font-size:12px;color:" + _TEXT_SEC
                + ";padding:4px 0;'>"
                "Sending <b>" + str(i + 1) + "</b>/"
                + str(len(leads_to_send))
                + " &#8212; " + _esc(email_addr)
                + "</div>",
                unsafe_allow_html=True,
            )

            try:
                ed     = template_engine.generate_email(lead, sequence=1)
                # ── v1.2: pass lead= so Nigeria/UK guard activates ──
                result = sender.send_email(
                    to_email  = email_addr,
                    subject   = _safe_str(ed.get("subject")),
                    html_body = _safe_str(ed.get("html")),
                    text_body = _safe_str(ed.get("text")),
                    lead      = lead,          # ← COMPLIANCE GUARD
                )
            except Exception as e:
                result = {"sent": False, "error": str(e)}
                ed     = {}

            log_entry = {
                "Business":  _safe_str(lead.get("business_name")),
                "Email":     email_addr,
                "Subject":   _safe_str(ed.get("subject")),
                "Template":  _safe_str(ed.get("template_type")),
                "Sent":      result.get("sent",  False),
                "Error":     _safe_str(result.get("error")),
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            results_log.append(log_entry)
            st.session_state.email_log.append(log_entry)

            if i < len(leads_to_send) - 1:
                time.sleep(int(delay_seconds))

        progress.progress(1.0)
        sent_count = sum(1 for r in results_log if r.get("Sent"))
        status.markdown(
            "<div style='color:" + _GREEN + ";font-size:13px;"
            "font-weight:700;padding:6px 0;'>"
            "&#9989; Done &#8212; "
            + str(sent_count) + "/" + str(len(leads_to_send))
            + " emails sent</div>",
            unsafe_allow_html=True,
        )
        st.rerun()


# ─────────────────────────────────────────────────────────
# TAB 3 — Email Log
# ─────────────────────────────────────────────────────────
def _tab_email_log() -> None:

    st.markdown(
        '<div class="oa-sec-title">Email Send Log</div>'
        '<div class="oa-sec-sub">'
        'Full record of every email sent this session.'
        '</div>',
        unsafe_allow_html=True,
    )

    if not st.session_state.email_log:
        st.markdown(
            '<div class="oa-info">'
            '&#128231; No emails sent yet. '
            'Go to Send Emails to get started.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    sent   = sum(1 for l in st.session_state.email_log if l.get("Sent"))
    total  = max(len(st.session_state.email_log), 1)
    failed = total - sent
    rate   = int(sent / total * 100)

    m1, m2, m3, m4 = st.columns(4)
    for col, (icon, label, value, color) in zip(
        [m1, m2, m3, m4],
        [
            ("&#128231;", "Total Attempted", str(total),      _BRAND_BLUE),
            ("&#9989;",   "Sent",            str(sent),       _GREEN),
            ("&#10060;",  "Failed",          str(failed),     _RED),
            ("&#128200;", "Success Rate",    str(rate) + "%", _GOLD),
        ],
    ):
        with col:
            st.markdown(
                '<div class="oa-metric">'
                '<div class="oa-metric-icon">' + icon + '</div>'
                '<div class="oa-metric-label">' + label + '</div>'
                '<div class="oa-metric-value" style="color:'
                + color + ';">' + value + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    log_df = pd.DataFrame(st.session_state.email_log)
    st.dataframe(
        log_df,
        use_container_width=True,
        hide_index=True,
        height=min(500, 60 + len(log_df) * 38),
    )

    cl1, cl2 = st.columns(2)
    with cl1:
        st.download_button(
            "Download Log CSV",
            data=log_df.to_csv(index=False),
            file_name="email_log_"
            + datetime.now().strftime("%Y%m%d_%H%M")
            + ".csv",
            mime="text/csv",
            key="dl_email_log",
        )
    with cl2:
        if st.button("Clear Log", key="clear_email_log"):
            st.session_state.email_log = []
            st.rerun()


# ─────────────────────────────────────────────────────────
# TAB 4 — Settings
# ─────────────────────────────────────────────────────────
def _tab_settings() -> None:

    st.markdown(
        '<div class="oa-sec-title">Email Settings</div>'
        '<div class="oa-sec-sub">'
        'Configure your Gmail credentials to enable sending.'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="oa-info">'
        '<b>Gmail App Password required</b> &#8212; '
        'do not use your regular Gmail password.<br>'
        'Go to: <b>Google Account &#8594; Security &#8594; '
        '2-Step Verification &#8594; App Passwords &#8594; Generate</b>'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.form("gmail_settings_form"):
        gmail = st.text_input(
            "Gmail Address", placeholder="you@gmail.com"
        )
        app_password = st.text_input(
            "Gmail App Password",
            type="password",
            placeholder="xxxx xxxx xxxx xxxx",
        )
        from_name = st.text_input(
            "From Name",
            value="KNCS Digital",
            placeholder="Your Name or Company",
        )

        if st.form_submit_button(
            "Save Settings",
            use_container_width=True,
            type="primary",
        ):
            env_path = ".env"
            try:
                try:
                    with open(env_path, "r") as f:
                        env_content = f.read()
                except FileNotFoundError:
                    env_content = ""

                lines     = env_content.split("\n")
                new_lines = []
                keys_set  = set()

                for line in lines:
                    if line.startswith("GMAIL_ADDRESS="):
                        new_lines.append("GMAIL_ADDRESS=" + gmail)
                        keys_set.add("GMAIL_ADDRESS")
                    elif line.startswith("GMAIL_APP_PASSWORD="):
                        new_lines.append(
                            "GMAIL_APP_PASSWORD=" + app_password
                        )
                        keys_set.add("GMAIL_APP_PASSWORD")
                    elif line.startswith("FROM_NAME="):
                        new_lines.append("FROM_NAME=" + from_name)
                        keys_set.add("FROM_NAME")
                    else:
                        new_lines.append(line)

                if "GMAIL_ADDRESS" not in keys_set:
                    new_lines.append("GMAIL_ADDRESS=" + gmail)
                if "GMAIL_APP_PASSWORD" not in keys_set:
                    new_lines.append(
                        "GMAIL_APP_PASSWORD=" + app_password
                    )
                if "FROM_NAME" not in keys_set:
                    new_lines.append("FROM_NAME=" + from_name)

                with open(env_path, "w") as f:
                    f.write("\n".join(new_lines))

                st.markdown(
                    '<div class="oa-success">'
                    '&#9989; Settings saved to .env'
                    '</div>',
                    unsafe_allow_html=True,
                )

            except Exception as e:
                st.markdown(
                    '<div class="oa-error">'
                    '&#9888; Could not save: '
                    + _esc(str(e)) +
                    '</div>',
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────
# Main render
# ─────────────────────────────────────────────────────────
def render_outreach_dashboard() -> None:

    _render_css()

    if "audited_leads" not in st.session_state:
        st.session_state.audited_leads = []
    if "email_log" not in st.session_state:
        st.session_state.email_log = []

    audited      = st.session_state.audited_leads
    urgent_count = len(
        [l for l in audited if l.get("website_priority") == "URGENT"]
    )
    high_count   = len(
        [l for l in audited if l.get("website_priority") == "HIGH"]
    )
    sent_count = len(st.session_state.email_log)

    _render_hero(
        audited_count = len(audited),
        urgent_count  = urgent_count,
        high_count    = high_count,
        sent_count    = sent_count,
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        "🔍 Website Audit",
        "📧 Send Emails",
        "📊 Email Log",
        "⚙️ Settings",
    ])

    with tab1: _tab_audit()
    with tab2: _tab_send_emails()
    with tab3: _tab_email_log()
    with tab4: _tab_settings()


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
def render() -> None:
    render_outreach_dashboard()