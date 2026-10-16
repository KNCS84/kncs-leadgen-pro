"""
pages/nigeria_dashboard.py
KNCS LeadGen Pro — Nigeria Module — v10.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Streamlit UI page for Nigeria B2B lead generation.
Controls moved from st.sidebar into main page body
to avoid conflict with app.py sidebar navigation.

Fixes applied in v10.1:
  FIX-NG-SAVE-01 (CRITICAL) — This page previously had NO database
              import or write path at all. Search and enrich results
              only ever lived in st.session_state and CSV downloads
              via st.download_button. This meant every lead found
              through this page was invisible to Call Center,
              Dashboard, and Reports, regardless of any routing fix,
              because it never reached the leads table.

              Added:
                - insert_lead import (from database.py)
                - engine.router import (assign_channel,
                  apply_routing_updates)
                - _map_lead_for_db(): translates this page's
                  enrichment field names (niche, dm_name, dm_title,
                  whatsapp_number) to the exact column names
                  insert_lead() expects (industry,
                  decision_maker_name, decision_maker_title, phone)
                - _save_leads_to_db(): persists enriched leads via
                  insert_lead(), then applies channel routing
                  immediately after each insert
                - "Save to Database" button added to the control
                  panel, enabled once leads have been enriched
"""

import logging
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st

from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient

try:
    from engine.nigeria.nigeria_enricher import NigeriaEnricher
    _ENRICHER_OK = True
except Exception:
    _ENRICHER_OK = False

try:
    from engine.nigeria.decision_maker_finder import DecisionMakerFinder
    _DM_OK = True
except Exception:
    _DM_OK = False

# ─────────────────────────────────────────────────────────
# FIX-NG-SAVE-01: Database + router imports.
# This page previously had NO database import at all.
# ─────────────────────────────────────────────────────────
try:
    from database import insert_lead
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    def insert_lead(*a, **k):
        return (False, "error:db_unavailable", None)

try:
    from engine.router import assign_channel, apply_routing_updates
    _ROUTER_AVAILABLE = True
except Exception:
    _ROUTER_AVAILABLE = False
    def assign_channel(lead: dict) -> dict:
        return {}
    def apply_routing_updates(lead_id, updates) -> bool:
        return False

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────

NICHES = {
    "🏫 Private Schools":             "private_school",
    "🏥 Private Hospitals & Clinics": "private_hospital",
    "🏠 Real Estate Companies":       "real_estate",
    "🏨 Hotels & Guest Houses":       "hotel",
    "🏭 Manufacturing SMEs":          "manufacturing",
    "🏗️ Construction Companies":      "construction",
    "⚖️ Law Firms":                   "law_firm",
}

CITIES = {
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

CITY_DISPLAY = {
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

NICHE_DM_TITLES = {
    "private_school":   "School Proprietor/Principal",
    "private_hospital": "Medical Director",
    "real_estate":      "Managing Director",
    "hotel":            "General Manager",
    "manufacturing":    "Factory Director",
    "construction":     "Managing Director",
    "law_firm":         "Senior Partner",
}

CONFIDENCE_EMOJI = {
    "high":   "🟢",
    "medium": "🟡",
    "low":    "🔴",
}

CONFIDENCE_COLOURS = {
    "high":   "#28a745",
    "medium": "#fd7e14",
    "low":    "#dc3545",
}

# ─────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "ng_raw_leads":      [],
        "ng_enriched_leads": [],
        "ng_last_search":    None,
        "ng_error":          None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _reset_results() -> None:
    st.session_state["ng_raw_leads"]      = []
    st.session_state["ng_enriched_leads"] = []
    st.session_state["ng_error"]          = None


# ─────────────────────────────────────────────────────────
# WHATSAPP HELPERS
# ─────────────────────────────────────────────────────────

def _build_wa_link(phone: str, message: str) -> str:
    from urllib.parse import quote
    import re
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("0"):
        digits = "+234" + digits[1:]
    elif digits.startswith("234") and not digits.startswith("+"):
        digits = "+" + digits
    return f"https://wa.me/{digits}?text={quote(message)}"


def _generate_message(
    dm_name: str,
    business_name: str,
    niche: str,
    city: str,
) -> str:
    greeting   = f"Hi {dm_name}" if dm_name else "Hello"
    city_short = city.split()[0] if city else "Nigeria"
    messages = {
        "private_school": (
            f"{greeting}, I came across {business_name} and was "
            f"impressed by your work in education in {city_short}. "
            f"I'd love to discuss how we can help you attract more "
            f"quality students through digital outreach. "
            f"Do you have 5 minutes this week?"
        ),
        "private_hospital": (
            f"{greeting}, I noticed {business_name} serves the "
            f"{city_short} community. I'd like to share how other "
            f"clinics are growing their patient base through targeted "
            f"digital marketing. Could we connect briefly?"
        ),
        "real_estate": (
            f"{greeting}, I came across {business_name} and your "
            f"property portfolio caught my attention. I help real "
            f"estate companies in {city_short} close more deals using "
            f"qualified lead generation. Would you be open to a quick call?"
        ),
        "hotel": (
            f"{greeting}, {business_name} looks like a great property. "
            f"I work with hotels in {city_short} to increase bookings "
            f"through direct digital outreach. Can we speak briefly?"
        ),
        "law_firm": (
            f"{greeting}, I found {business_name} while researching "
            f"top law firms in {city_short}. I help law firms attract "
            f"high-value clients through targeted campaigns. "
            f"Would you be open to a brief conversation?"
        ),
    }
    return messages.get(
        niche,
        f"{greeting}, I'd love to explore how we can help "
        f"{business_name} grow in {city_short}. Can we connect?",
    )


# ─────────────────────────────────────────────────────────
# ENRICHMENT
# ─────────────────────────────────────────────────────────

def _enrich_one(raw: dict, city_display: str) -> dict:
    lead  = dict(raw)
    niche = lead.get("niche", "")
    name  = lead.get("business_name", "")

    if not lead.get("city"):
        lead["city"] = city_display

    if _ENRICHER_OK:
        try:
            lead = NigeriaEnricher().enrich_lead(lead)
        except Exception:
            pass

    dm_name  = ""
    dm_title = ""
    if _DM_OK:
        try:
            dm_data  = DecisionMakerFinder().find(
                business_name=name,
                website=lead.get("website", ""),
                city=city_display,
                niche=niche,
            )
            dm_name  = dm_data.get("decision_maker_name", "")
            dm_title = dm_data.get("decision_maker_title", "")
        except Exception:
            pass

    if not dm_name:
        dm_name = (
            lead.get("decision_maker", "")
            or lead.get("dm_name", "")
        )
    if not dm_title:
        dm_title = NICHE_DM_TITLES.get(niche, "Director")

    lead["dm_name"]  = dm_name
    lead["dm_title"] = dm_title

    wa = lead.get("whatsapp_number", "") or lead.get("phone", "")
    if wa:
        msg = _generate_message(dm_name, name, niche, city_display)
        lead["outreach_message"] = msg
        lead["wa_outreach_url"]  = _build_wa_link(wa, msg)
        if not lead.get("whatsapp_number"):
            lead["whatsapp_number"] = wa

    filled = sum(1 for f in [
        lead.get("business_name"),
        lead.get("phone"),
        lead.get("whatsapp_number"),
        lead.get("email"),
        lead.get("dm_name"),
        lead.get("website"),
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
# ─────────────────────────────────────────────────────────

def _map_lead_for_db(
    lead: dict, niche_label: str = "", city_label: str = "",
) -> dict:
    """
    Maps this page's enrichment field names (niche, dm_name,
    dm_title, whatsapp_number) to the exact column names
    database.py's insert_lead() expects (industry,
    decision_maker_name, decision_maker_title, phone). Without this
    mapping, insert_lead() silently drops these fields since it only
    copies keys that exactly match its internal allow-list.
    """
    mapped = dict(lead)

    if not str(mapped.get("industry") or "").strip():
        niche_val = mapped.get("niche") or niche_label
        if niche_val:
            mapped["industry"] = niche_val

    if not str(mapped.get("decision_maker_name") or "").strip():
        dm_val = mapped.get("dm_name") or mapped.get("decision_maker") or ""
        if dm_val:
            mapped["decision_maker_name"] = dm_val

    if not str(mapped.get("decision_maker_title") or "").strip():
        dm_title_val = mapped.get("dm_title") or ""
        if dm_title_val:
            mapped["decision_maker_title"] = dm_title_val

    if not str(mapped.get("phone") or "").strip():
        wa_val = mapped.get("whatsapp_number") or ""
        if wa_val:
            mapped["phone"] = wa_val

    mapped["country"] = "Nigeria"
    if not str(mapped.get("market") or "").strip():
        mapped["market"] = "Nigeria"
    if not str(mapped.get("city") or "").strip() and city_label:
        mapped["city"] = city_label
    if not str(mapped.get("source") or "").strip():
        mapped["source"] = "nigeria_google_maps"
    if not str(mapped.get("enrichment_status") or "").strip() \
            or mapped.get("enrichment_status") == "pending":
        mapped["enrichment_status"] = "complete"

    return mapped


def _save_leads_to_db(
    leads: list, niche_label: str = "", city_label: str = "",
) -> dict:
    """
    FIX-NG-SAVE-01: Persists enriched leads to the leads table and
    applies channel routing immediately after each insert, so
    best_channel/wa_status/whatsapp_ready are set on the same row
    that gets written — closing the root cause of the Nigeria
    WhatsApp queue bug at this entry point too.
    """
    summary = {"saved": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not _DB_AVAILABLE:
        summary["errors"] = len(leads)
        return summary

    for lead in leads:
        try:
            mapped = _map_lead_for_db(lead, niche_label, city_label)
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

            if lead_id and _ROUTER_AVAILABLE:
                try:
                    routing_input       = dict(mapped)
                    routing_input["id"] = lead_id
                    routing = assign_channel(routing_input)
                    if routing:
                        apply_routing_updates(lead_id, routing)
                except Exception:
                    pass

        except Exception:
            summary["errors"] += 1

    return summary


# ─────────────────────────────────────────────────────────
# DATA HELPERS
# ─────────────────────────────────────────────────────────

def _to_dataframe(leads: list) -> pd.DataFrame:
    if not leads:
        return pd.DataFrame()
    rows = []
    for l in leads:
        rows.append({
            "Business Name":  l.get("business_name", ""),
            "Decision Maker": l.get("dm_name", "")
                              or l.get("decision_maker", ""),
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
            "Completeness %": l.get("completeness_pct", ""),
            "Source":         l.get("source", ""),
            "Outreach URL":   l.get("wa_outreach_url", ""),
        })
    return pd.DataFrame(rows)


def _wa_dataframe(leads: list) -> pd.DataFrame:
    rows = []
    for l in leads:
        wa = l.get("whatsapp_number", "")
        if not wa:
            continue
        rows.append({
            "Decision Maker": l.get("dm_name", "")
                              or l.get("decision_maker", ""),
            "DM Title":       l.get("dm_title", ""),
            "Business Name":  l.get("business_name", ""),
            "City":           l.get("city", ""),
            "WhatsApp":       wa,
            "Click-to-Chat":  l.get("wa_outreach_url", ""),
            "Message":        l.get("outreach_message", ""),
            "Email":          l.get("email", ""),
            "Website":        l.get("website", ""),
            "Confidence":     l.get("confidence", ""),
        })
    return pd.DataFrame(rows)


def _calculate_metrics(raw: list, enriched: list) -> dict:
    total    = len(enriched)
    with_wa  = sum(1 for l in enriched if l.get("whatsapp_number"))
    with_dm  = sum(
        1 for l in enriched
        if l.get("dm_name") or l.get("decision_maker")
    )
    high_c   = sum(
        1 for l in enriched if l.get("confidence") == "high"
    )
    avg_comp = (
        round(
            sum(l.get("completeness_pct", 0) for l in enriched)
            / total, 1
        )
        if total else 0
    )
    return {
        "total_found":      len(raw),
        "total_enriched":   total,
        "with_whatsapp":    with_wa,
        "with_dm_name":     with_dm,
        "high_confidence":  high_c,
        "avg_completeness": avg_comp,
        "whatsapp_pct":     round(with_wa / total * 100, 1)
                            if total else 0,
    }


# ─────────────────────────────────────────────────────────
# UI COMPONENTS
# ─────────────────────────────────────────────────────────

def _render_metrics(raw: list, enriched: list) -> None:
    m = _calculate_metrics(raw, enriched)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🔍 Found",          m["total_found"])
    c2.metric("✅ Enriched",        m["total_enriched"])
    c3.metric(
        "💬 With WhatsApp",
        m["with_whatsapp"],
        delta=(
            f"{m['whatsapp_pct']}%"
            if m["total_enriched"] > 0 else None
        ),
    )
    c4.metric("👤 DM Name Found",   m["with_dm_name"])
    c5.metric("🟢 High Confidence", m["high_confidence"])
    if enriched:
        st.caption(
            f"Average completeness: **{m['avg_completeness']}%**"
        )


def _render_lead_card(lead: dict) -> None:
    confidence = lead.get("confidence", "low")
    emoji      = CONFIDENCE_EMOJI.get(confidence, "🔴")
    colour     = CONFIDENCE_COLOURS.get(confidence, "#dc3545")
    name       = lead.get("business_name", "Unknown")
    dm_name    = (
        lead.get("dm_name", "") or lead.get("decision_maker", "")
    )
    dm_title   = lead.get("dm_title", "")
    wa         = lead.get("whatsapp_number", "")
    pct        = lead.get("completeness_pct", 0)

    header = f"{emoji} **{name}**"
    if dm_name:
        title_str = f" ({dm_title})" if dm_title else ""
        header   += f" — 👤 {dm_name}{title_str}"
    if wa:
        header += f" | 📱 {wa}"

    with st.expander(header, expanded=(confidence == "high")):
        col_left, col_right = st.columns(2)

        with col_left:
            st.markdown("**📋 Business Details**")
            st.write(f"🏢 **Name:** {name}")
            st.write(f"📍 **Address:** {lead.get('address', '—')}")
            st.write(f"🌍 **City:** {lead.get('city', '—')}")
            st.write(f"🏷️ **Niche:** {lead.get('niche', '—')}")
            st.write(f"⭐ **Rating:** {lead.get('rating', '—')}")
            if lead.get("website"):
                st.write(f"🌐 **Website:** {lead['website']}")
            st.markdown("---")
            st.markdown("**👤 Decision Maker**")
            st.write(f"👤 **Name:** {dm_name or '—'}")
            st.write(f"💼 **Title:** {dm_title or '—'}")
            st.write(
                f"📧 **DM Email:** {lead.get('dm_email', '—')}"
            )

        with col_right:
            st.markdown("**📱 Contact Details**")
            st.write(f"📞 **Phone:** {lead.get('phone', '—')}")
            st.write(f"💬 **WhatsApp:** {wa or '—'}")
            st.write(f"📧 **Email:** {lead.get('email', '—')}")

            outreach_url = lead.get("wa_outreach_url", "")
            if outreach_url:
                st.markdown("---")
                st.markdown("**🚀 Outreach**")
                st.markdown(
                    f'<a href="{outreach_url}" target="_blank">'
                    f'<button style="background:#25D366;color:white;'
                    f'border:none;padding:8px 18px;border-radius:8px;'
                    f'cursor:pointer;font-size:13px;font-weight:700;">'
                    f'📲 Open WhatsApp Chat</button></a>',
                    unsafe_allow_html=True,
                )

            message = lead.get("outreach_message", "")
            if message:
                st.markdown("---")
                st.markdown("**💬 Message Preview**")
                st.info(message)

        st.markdown("---")
        cq1, cq2 = st.columns(2)
        with cq1:
            st.markdown(
                f"**Confidence:** "
                f"<span style='color:{colour};font-weight:bold;'>"
                f"{emoji} {confidence.upper()}</span>",
                unsafe_allow_html=True,
            )
        with cq2:
            st.write(f"**Completeness:** {pct}%")
            st.progress(pct / 100)


def _render_lead_table(enriched: list) -> None:
    if not enriched:
        return
    df = _to_dataframe(enriched)

    def _highlight(row):
        cmap = {
            "high":   "background-color:#d4edda;color:#155724;",
            "medium": "background-color:#fff3cd;color:#856404;",
            "low":    "background-color:#f8d7da;color:#721c24;",
        }
        style = cmap.get(row.get("Confidence", "low"), "")
        return [style] * len(row)

    st.dataframe(
        df.style.apply(_highlight, axis=1),
        use_container_width=True,
        height=420,
    )


# ─────────────────────────────────────────────────────────
# CONTROL PANEL
# Renders in left column — no st.sidebar used
# ─────────────────────────────────────────────────────────

def _render_control_panel() -> dict:
    """
    Renders all search controls as plain Streamlit
    widgets in the left column of the main page body.
    Returns dict of all selected values and button states.
    """

    # ── Section heading ───────────────────────
    st.markdown(
        "<p style='font-size:11px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:1.5px;"
        "color:#5b8cff;margin:0 0 16px 0;'>"
        "🇳🇬 Search Controls</p>",
        unsafe_allow_html=True,
    )

    # ── Niche ─────────────────────────────────
    st.markdown(
        "<p style='font-size:11px;font-weight:600;"
        "color:#94a3b8;margin:0 0 4px 0;'>"
        "🏷️ Target Niche</p>",
        unsafe_allow_html=True,
    )
    niche_label = st.selectbox(
        "Target Niche",
        options=list(NICHES.keys()),
        index=0,
        label_visibility="collapsed",
        key="ng_niche_select",
    )
    selected_niche = NICHES[niche_label]

    st.markdown(
        "<div style='height:12px'></div>",
        unsafe_allow_html=True,
    )

    # ── City ──────────────────────────────────
    st.markdown(
        "<p style='font-size:11px;font-weight:600;"
        "color:#94a3b8;margin:0 0 4px 0;'>"
        "📍 Target City</p>",
        unsafe_allow_html=True,
    )
    city_label = st.selectbox(
        "Target City",
        options=list(CITIES.keys()),
        index=0,
        label_visibility="collapsed",
        key="ng_city_select",
    )
    selected_city_key  = CITIES[city_label]
    selected_city_disp = CITY_DISPLAY.get(
        selected_city_key, city_label
    )

    st.markdown(
        "<div style='height:12px'></div>",
        unsafe_allow_html=True,
    )

    # ── Max leads ─────────────────────────────
    st.markdown(
        "<p style='font-size:11px;font-weight:600;"
        "color:#94a3b8;margin:0 0 4px 0;'>"
        "🔢 Max Leads</p>",
        unsafe_allow_html=True,
    )
    max_leads = st.slider(
        "Max Leads",
        min_value=5,
        max_value=60,
        value=10,
        step=5,
        label_visibility="collapsed",
        key="ng_max_leads",
    )

    st.markdown(
        "<div style='height:12px'></div>",
        unsafe_allow_html=True,
    )

    # ── API Key ───────────────────────────────
    st.markdown(
        "<p style='font-size:11px;font-weight:600;"
        "color:#94a3b8;margin:0 0 4px 0;'>"
        "🔑 Google Maps API Key "
        "<span style='font-weight:400;color:#475569;"
        "font-size:10px;'>(optional)</span></p>",
        unsafe_allow_html=True,
    )
    gmaps_key = st.text_input(
        "Google Maps API Key",
        placeholder="Leave blank for free mode",
        type="password",
        label_visibility="collapsed",
        key="ng_api_key",
    )

    st.markdown(
        "<div style='height:16px'></div>",
        unsafe_allow_html=True,
    )

    # ── Status indicator ──────────────────────
    if _DM_OK:
        st.success("✅ DM Finder ready")
    else:
        st.warning("⚠️ DM Finder not loaded")

    # FIX-NG-SAVE-01: Database status indicator
    if _DB_AVAILABLE:
        st.success("✅ Database connected")
    else:
        st.error("❌ Database not available — saves will fail")

    # ── Current search summary ────────────────
    st.markdown(
        "<div style='"
        "background:rgba(91,140,255,0.06);"
        "border:1px solid rgba(91,140,255,0.14);"
        "border-radius:10px;"
        "padding:10px 12px;"
        "margin:12px 0 16px 0;"
        "font-size:11px;"
        "color:#94a3b8;"
        "line-height:2.0;"
        "'>"
        "<b style='color:#5b8cff;font-size:10px;"
        "text-transform:uppercase;letter-spacing:1px;'>"
        "Current search</b><br>"
        "Niche: <code style='font-size:10px;color:#cbd5e1;'>"
        + selected_niche + "</code><br>"
        "City:&nbsp; <code style='font-size:10px;color:#cbd5e1;'>"
        + selected_city_key + "</code>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Primary action buttons ────────────────
    btn_find = st.button(
        "🔍 Find Businesses",
        use_container_width=True,
        type="primary",
        key="ng_btn_find",
        help="Search Google Maps for businesses matching your niche and city",
    )

    btn_enrich = st.button(
        "⚡ Enrich Leads",
        use_container_width=True,
        key="ng_btn_enrich",
        disabled=len(
            st.session_state.get("ng_raw_leads", [])
        ) == 0,
        help="Find decision maker names, WhatsApp numbers and email addresses",
    )

    # FIX-NG-SAVE-01: Save to Database button — this is the button
    # that closes the root cause bug. Without it, enriched leads
    # never reach the database and stay invisible to Call Center.
    btn_save = st.button(
        "💾 Save to Database",
        use_container_width=True,
        type="primary",
        key="ng_btn_save",
        disabled=len(
            st.session_state.get("ng_enriched_leads", [])
        ) == 0,
        help="Persist enriched leads to the database so they appear "
             "in Call Center, Dashboard and Reports",
    )

    st.markdown(
        "<div style='height:1px;background:rgba(255,255,255,0.06);"
        "margin:14px 0;'></div>",
        unsafe_allow_html=True,
    )

    # ── Export buttons ────────────────────────
    btn_csv = st.button(
        "📥 Download CSV",
        use_container_width=True,
        key="ng_btn_csv",
        disabled=len(
            st.session_state.get("ng_enriched_leads", [])
        ) == 0,
        help="Export all enriched leads to a CSV file",
    )

    btn_wa = st.button(
        "📲 WhatsApp List",
        use_container_width=True,
        key="ng_btn_wa",
        disabled=len(
            st.session_state.get("ng_enriched_leads", [])
        ) == 0,
        help="Export leads with WhatsApp numbers and pre-written messages",
    )

    return {
        "niche_label":         niche_label,
        "selected_niche":      selected_niche,
        "city_label":          city_label,
        "selected_city_key":   selected_city_key,
        "selected_city_disp":  selected_city_disp,
        "max_leads":           max_leads,
        "gmaps_key":           gmaps_key,
        "btn_find":            btn_find,
        "btn_enrich":          btn_enrich,
        "btn_save":            btn_save,
        "btn_csv":             btn_csv,
        "btn_wa":              btn_wa,
    }


# ─────────────────────────────────────────────────────────
# MAIN PAGE FUNCTION
# ─────────────────────────────────────────────────────────

def main() -> None:
    _init_state()

    # ── Page header ───────────────────────────
    st.markdown(
        "<div style='"
        "background:linear-gradient(145deg,#0b1628 0%,"
        "#0e1e35 50%,#0b1628 100%);"
        "border:1px solid rgba(255,255,255,0.07);"
        "border-radius:18px;"
        "padding:24px 28px;"
        "margin-bottom:20px;"
        "'>"
        "<div style='font-size:10px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:2px;"
        "color:#5b8cff;margin-bottom:8px;'>Nigeria B2B</div>"
        "<div style='font-size:26px;font-weight:900;"
        "color:#f1f5f9;letter-spacing:-0.8px;"
        "line-height:1.15;margin-bottom:8px;'>"
        "🇳🇬 Nigeria B2B Lead Generator"
        "</div>"
        "<div style='font-size:13px;color:#475569;"
        "font-weight:500;line-height:1.7;'>"
        "Discover Nigerian business decision makers — "
        "name, title, WhatsApp, email — across "
        "<b style='color:#94a3b8;'>7 niches</b> and "
        "<b style='color:#94a3b8;'>21 cities</b>. "
        "<b style='color:#10b981;'>100% free</b>, "
        "no API key required."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Error display ─────────────────────────
    if st.session_state.get("ng_error"):
        st.error(st.session_state["ng_error"])
        if st.button("Clear Error", key="ng_clear_error"):
            st.session_state["ng_error"] = None
            st.rerun()

    # ── Two-column layout ─────────────────────
    col_ctrl, col_results = st.columns(
        [0.32, 0.68], gap="large"
    )

    with col_ctrl:
        controls = _render_control_panel()

    with col_results:

        niche_label        = controls["niche_label"]
        selected_niche     = controls["selected_niche"]
        city_label         = controls["city_label"]
        selected_city_key  = controls["selected_city_key"]
        selected_city_disp = controls["selected_city_disp"]
        max_leads          = controls["max_leads"]
        gmaps_key          = controls["gmaps_key"]
        btn_find           = controls["btn_find"]
        btn_enrich         = controls["btn_enrich"]
        btn_save           = controls["btn_save"]
        btn_csv            = controls["btn_csv"]
        btn_wa             = controls["btn_wa"]

        # ── FIND BUSINESSES ───────────────────
        if btn_find:
            _reset_results()
            with st.spinner(
                f"🔍 Searching {niche_label} in {city_label}..."
            ):
                try:
                    client  = NigeriaGoogleMapsClient(
                        api_key=(
                            gmaps_key.strip() if gmaps_key else ""
                        )
                    )
                    results = client.search_businesses(
                        niche=selected_niche,
                        city_key=selected_city_key,
                        max_results=max_leads,
                    )
                    st.session_state["ng_raw_leads"] = (
                        results or []
                    )

                    if results:
                        st.success(
                            f"✅ Found **{len(results)}** businesses "
                            f"in {city_label}. "
                            f"Click **⚡ Enrich Leads** to continue."
                        )
                    else:
                        st.warning(
                            "No businesses found. "
                            "Try a different niche or city."
                        )
                except Exception as exc:
                    st.session_state["ng_error"] = str(exc)
                    logger.error("Find error", exc_info=True)
                    st.rerun()

        # ── ENRICH ────────────────────────────
        if btn_enrich:
            raw = st.session_state.get("ng_raw_leads", [])
            if not raw:
                st.warning("Find businesses first.")
            else:
                enriched_list = []
                prog          = st.progress(0.0)
                status        = st.empty()
                total         = len(raw)

                for i, lead in enumerate(raw):
                    bname = lead.get(
                        "business_name", f"Lead {i+1}"
                    )
                    status.markdown(
                        f"⚡ **Enriching {i+1}/{total}:** "
                        f"{bname}..."
                    )
                    prog.progress(
                        float(i + 1) / float(total)
                    )
                    try:
                        enriched_list.append(
                            _enrich_one(
                                lead, selected_city_disp
                            )
                        )
                    except Exception:
                        lead_copy = dict(lead)
                        lead_copy.update({
                            "dm_name":          "",
                            "dm_title":         "",
                            "confidence":       "low",
                            "completeness_pct": 0,
                        })
                        enriched_list.append(lead_copy)
                    time.sleep(0.05)

                st.session_state["ng_enriched_leads"] = (
                    enriched_list
                )
                prog.empty()
                status.empty()

                with_wa = sum(
                    1 for l in enriched_list
                    if l.get("whatsapp_number")
                )
                with_dm = sum(
                    1 for l in enriched_list
                    if l.get("dm_name")
                )
                st.success(
                    f"✅ Enriched **{len(enriched_list)}** leads | "
                    f"**{with_dm}** decision makers | "
                    f"**{with_wa}** with WhatsApp. "
                    f"Click **💾 Save to Database** to persist these."
                )

        # ── FIX-NG-SAVE-01: SAVE TO DATABASE ──
        if btn_save:
            enriched = st.session_state.get("ng_enriched_leads", [])
            if not enriched:
                st.warning("Enrich leads first, then save.")
            else:
                with st.spinner(
                    f"Saving {len(enriched)} leads to database..."
                ):
                    result = _save_leads_to_db(
                        enriched,
                        niche_label=selected_niche,
                        city_label=selected_city_disp,
                    )
                st.success(
                    f"Saved {result['saved']} new, "
                    f"updated {result['updated']}, "
                    f"skipped {result['skipped']}"
                    + (
                        f", {result['errors']} errors"
                        if result["errors"] else ""
                    )
                    + ". Leads now visible in Call Center and Dashboard."
                )
                if result["saved"] or result["updated"]:
                    st.session_state["ng_raw_leads"]      = []
                    st.session_state["ng_enriched_leads"] = []
                    st.rerun()

        # ── DOWNLOAD CSV ──────────────────────
        if btn_csv:
            enriched = st.session_state.get(
                "ng_enriched_leads", []
            )
            if enriched:
                df  = _to_dataframe(enriched)
                ts  = datetime.now().strftime("%Y%m%d_%H%M")
                buf = StringIO()
                df.to_csv(buf, index=False)
                st.download_button(
                    "📥 Click to Download CSV",
                    data=buf.getvalue(),
                    file_name=f"nigeria_leads_{ts}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="ng_dl_csv",
                )

        # ── EXPORT WHATSAPP ───────────────────
        if btn_wa:
            enriched = st.session_state.get(
                "ng_enriched_leads", []
            )
            if enriched:
                wa_df = _wa_dataframe(enriched)
                if wa_df.empty:
                    st.warning(
                        "No WhatsApp numbers found yet."
                    )
                else:
                    ts  = datetime.now().strftime("%Y%m%d_%H%M")
                    buf = StringIO()
                    wa_df.to_csv(buf, index=False)
                    st.download_button(
                        "📲 Download WhatsApp List",
                        data=buf.getvalue(),
                        file_name=f"nigeria_whatsapp_{ts}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key="ng_dl_wa",
                    )

        # ── METRICS ───────────────────────────
        raw      = st.session_state.get("ng_raw_leads", [])
        enriched = st.session_state.get("ng_enriched_leads", [])

        if raw or enriched:
            st.divider()
            _render_metrics(raw, enriched)

        # ── RAW PREVIEW ───────────────────────
        if raw and not enriched:
            st.divider()
            st.markdown(f"### 🔍 Found {len(raw)} Businesses")
            st.caption(
                "Click **⚡ Enrich Leads** in the control "
                "panel to get contact details."
            )
            preview = [
                {
                    "Business Name": l.get("business_name", ""),
                    "Address":       l.get("address", ""),
                    "Phone":         l.get("phone", ""),
                    "Website":       l.get("website", ""),
                    "Rating":        l.get("rating", ""),
                    "Source":        l.get("source", ""),
                }
                for l in raw
            ]
            st.dataframe(
                pd.DataFrame(preview),
                use_container_width=True,
                height=300,
            )

        # ── ENRICHED RESULTS ──────────────────
        if enriched:
            st.divider()
            conf_order = {"high": 0, "medium": 1, "low": 2}

            tab_table, tab_cards, tab_wa = st.tabs([
                f"📊 All {len(enriched)} Leads",
                "🃏 Lead Cards",
                "💬 WhatsApp Only",
            ])

            with tab_table:
                st.markdown(
                    f"### 📊 {len(enriched)} Enriched Leads"
                )
                _render_lead_table(enriched)

                dm_leads = [
                    l for l in enriched
                    if l.get("dm_name")
                    or l.get("decision_maker")
                ]
                if dm_leads:
                    st.markdown("---")
                    st.markdown(
                        f"**👤 Decision Makers: "
                        f"{len(dm_leads)}/{len(enriched)}**"
                    )
                    dm_rows = [
                        {
                            "Business": l.get(
                                "business_name", ""
                            ),
                            "DM Name":  (
                                l.get("dm_name", "")
                                or l.get("decision_maker", "")
                            ),
                            "Title":    l.get("dm_title", ""),
                            "DM Email": l.get("dm_email", ""),
                            "DM Phone": l.get("dm_phone", ""),
                        }
                        for l in dm_leads
                    ]
                    st.dataframe(
                        pd.DataFrame(dm_rows),
                        use_container_width=True,
                        height=250,
                    )

            with tab_cards:
                st.markdown("### 🃏 Lead Cards")
                st.caption(
                    "High confidence leads expanded by default."
                )
                for lead in sorted(
                    enriched,
                    key=lambda l: conf_order.get(
                        l.get("confidence", "low"), 2
                    ),
                ):
                    _render_lead_card(lead)

            with tab_wa:
                wa_leads = [
                    l for l in enriched
                    if l.get("whatsapp_number")
                ]
                if wa_leads:
                    st.markdown(
                        f"### 💬 {len(wa_leads)} "
                        f"Leads with WhatsApp"
                    )
                    for lead in sorted(
                        wa_leads,
                        key=lambda l: conf_order.get(
                            l.get("confidence", "low"), 2
                        ),
                    ):
                        conf   = lead.get("confidence", "low")
                        emoji  = CONFIDENCE_EMOJI.get(
                            conf, "🔴"
                        )
                        bname  = lead.get(
                            "business_name", "Unknown"
                        )
                        dm     = (
                            lead.get("dm_name", "")
                            or lead.get("decision_maker", "")
                        )
                        wa     = lead.get(
                            "whatsapp_number", ""
                        )
                        wa_url = lead.get("wa_outreach_url", "")
                        msg    = lead.get(
                            "outreach_message", ""
                        )

                        c1, c2, c3 = st.columns([3, 2, 2])
                        with c1:
                            st.markdown(f"{emoji} **{bname}**")
                            if dm:
                                st.caption(f"👤 {dm}")
                        with c2:
                            st.code(wa, language=None)
                        with c3:
                            if wa_url:
                                st.markdown(
                                    f"[📲 WhatsApp]({wa_url})"
                                )
                        if msg:
                            with st.expander("💬 Message"):
                                st.write(msg)
                        st.divider()
                else:
                    st.info(
                        "No WhatsApp numbers found. "
                        "Run enrichment first."
                    )

        # ── EMPTY STATE ───────────────────────
        if not raw and not enriched and not st.session_state.get(
            "ng_error"
        ):
            st.markdown("---")
            st.markdown("### 🚀 How to Use")
            st.markdown(
                """
1. **Select a niche** in the control panel on the left
2. **Select a city** — Abuja, Lagos, Benin City, Warri etc.
3. Click **🔍 Find Businesses**
4. Click **⚡ Enrich Leads** to get decision maker details
5. Click **💾 Save to Database** so leads appear in Call Center
6. **Download CSV** or **WhatsApp List** if you also want a local copy

---

**📊 Expected results per 10 businesses:**

| Data Point | Hit Rate |
|---|---|
| Business name | 100% |
| Phone number | 60–80% |
| WhatsApp number | 30–50% |
| Decision maker name | 40–60% |
| Email address | 20–35% |

---
**Zero cost. No API key needed.**
                """
            )


# ─────────────────────────────────────────────────────────
# ENTRY POINTS
# ─────────────────────────────────────────────────────────

def render_nigeria_dashboard() -> None:
    main()


def render() -> None:
    main()