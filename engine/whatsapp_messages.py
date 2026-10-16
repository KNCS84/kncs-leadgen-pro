# engine/whatsapp_messages.py
# KNCS LeadGen Pro — v9.6
# Owner: Amechi
# Offer: Professional Website Design ONLY

from __future__ import annotations

import json
import re
from urllib.parse import quote
from typing import Any

# ─────────────────────────────────────────────
# Safe import — Search Intent
# ─────────────────────────────────────────────
try:
    from engine.search_intent import (
        get_search_intent_line_by_tone,
        get_search_phrase,
    )
    _INTENT_AVAILABLE = True
except Exception:
    _INTENT_AVAILABLE = False

    def get_search_intent_line_by_tone(
        industry: str, city: str, tone: str = "friendly"
    ) -> str:
        return ""

    def get_search_phrase(industry: str, city: str = "") -> str:
        return ""


# ─────────────────────────────────────────────
# BANNED PHRASES
# ─────────────────────────────────────────────
_BANNED = [
    "strong online presence",
    "get more customers automatically",
    "businesses like yours",
    "digital marketing",
    "take your business to the next level",
    "in today's digital world",
    "online visibility",
    "brand awareness",
]


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _clean_business_name_for_greeting(raw_name: Any) -> str:
    text = _clean(raw_name, "there")
    if not text or text.lower() == "there":
        return "there"
    # Strip pipe separators, double pipes, hyphen keyword stuffing from Google Maps
    # e.g. 'Smile Best Dental || Dental Clinic in Lagos || Dental Clinic in ikeja.' -> 'Smile Best Dental'
    parts = re.split(r"\s*(?:\|\||\||\s-\s|::)\s*", text)
    cleaned = parts[0].strip()
    return cleaned if cleaned else text


def _has_website(lead: dict) -> bool:
    w = _clean(lead.get("website")).lower()
    return (
        w not in ("", "none", "n/a", "not found", "no website")
        and len(w) > 4
    )


def _audit_label(score: int) -> str:
    if score <= 2:
        return "Critical"
    if score <= 4:
        return "Poor"
    if score <= 6:
        return "Below Average"
    if score <= 8:
        return "Average"
    return "Strong"


def _pain_list(lead: dict) -> list[str]:
    raw = lead.get("pain_points", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    return [str(p).strip() for p in (raw or []) if str(p).strip()]


WA_STATUS_VALUES = {
    "unknown",
    "estimated",
    "business_confirmed",
    "verified",
    "opted_in",
    "suppressed",
}

WA_ELIGIBLE_STATUSES = {
    "estimated",
    "business_confirmed",
    "verified",
    "opted_in",
}


def get_whatsapp_status(lead: dict) -> str:
    """
    Returns the best-known WhatsApp status for this lead.
    """
    status = _clean(
        lead.get("wa_status") or lead.get("verification_status")
    ).lower()

    if status in WA_STATUS_VALUES:
        return status

    if lead.get("wa_verified"):
        return "verified"
    if lead.get("wa_opt_in"):
        return "opted_in"
    if lead.get("phone_verified") and lead.get("wa_eligible"):
        return "business_confirmed"

    phone = _clean(lead.get("phone") or lead.get("phone_number"))
    if phone:
        return "estimated"

    return "unknown"


def wa_status_label(status: str) -> str:
    status = _clean(status).lower()
    return {
        "unknown":            "⚫ Unknown",
        "estimated":          "🟡 WA Estimated",
        "business_confirmed": "🟠 WA Business Confirmed",
        "verified":           "🟢 WA Verified",
        "opted_in":           "✅ WA Opted-In",
        "suppressed":         "🔴 Suppressed",
    }.get(status, "⚫ Unknown")


def can_send_whatsapp(lead: dict) -> bool:
    """
    Returns True only if the lead is WhatsApp-eligible.
    """
    return get_whatsapp_status(lead) in WA_ELIGIBLE_STATUSES


def _preferred_tone(country: str, tone: str) -> str:
    """
    Keeps existing tone behaviour, but allows country-aware fallback
    if caller passes an unknown tone or 'auto'.
    """
    tone = _clean(tone, "friendly").lower()
    if tone in {"friendly", "formal", "direct"}:
        return tone

    country = _clean(country).lower()
    if country in {"uk", "united kingdom", "ireland", "south africa"}:
        return "formal"
    if country in {"nigeria", "ghana", "kenya"}:
        return "friendly"
    return "direct"


def _contains_banned(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in _BANNED)


def _sanitise_message(text: str, lead: dict) -> str:
    """
    Removes banned phrasing if it slips through.
    """
    if not text:
        return text

    lower = text.lower()
    for phrase in _BANNED:
        if phrase in lower:
            text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return text.strip()


# ══════════════════════════════════════════════
# CORE MESSAGE BUILDER
# ══════════════════════════════════════════════

def build_whatsapp_message(
    lead: dict,
    tone: str = "friendly",
    sender_name: str = "Amechi",
    company_name: str = "King Networks Consulting Services (KNCS)",
    cta_link: str = "",
) -> str:
    """
    Builds a personalised WhatsApp message for a website design pitch.
    Includes search intent phrase per industry + city.

    Args:
        lead         : Lead dict from database
        tone         : 'friendly' | 'formal' | 'direct'
        sender_name  : Sender first name
        company_name : Company display name
        cta_link     : Optional booking/calendar link

    Returns:
        Formatted string ready to paste or send via wa.me
    """
    name        = _clean_business_name_for_greeting(lead.get("business_name"))
    industry    = (_clean(lead.get("industry"), "your industry"))
    city        = (_clean(lead.get("city"), "your area"))
    country     = _clean(lead.get("country"), "")
    rating      = _safe_float(lead.get("rating"))
    reviews     = _safe_int(lead.get("reviews"))
    audit_score = _safe_int(lead.get("website_audit_score"))
    audit_tag   = _audit_label(audit_score)
    has_site    = _has_website(lead)
    pain_points = _pain_list(lead)
    wa_status   = get_whatsapp_status(lead)
    resolved_tone = _preferred_tone(country, tone)

    location = f"{city}, {country}".strip(", ") if country else city

    # ── Search Intent Line ──────────────────
    intent_line = get_search_intent_line_by_tone(industry, city, resolved_tone)

    # ── Opener ──────────────────────────────
    opener = _build_opener(
        name=name,
        industry=industry,
        city=city,
        location=location,
        country=country,
        rating=rating,
        reviews=reviews,
        audit_score=audit_score,
        audit_tag=audit_tag,
        has_site=has_site,
        tone=resolved_tone,
        intent_line=intent_line,
        wa_status=wa_status,
    )

    # ── Pain Block ───────────────────────────
    pain_block = _build_pain_block(pain_points, resolved_tone)

    # ── Offer ────────────────────────────────
    offer = _build_offer(industry, resolved_tone)

    # ── CTA ──────────────────────────────────
    cta = _build_cta(name, resolved_tone, cta_link, wa_status)

    # ── Sign-off ─────────────────────────────
    sign_off = f"— {sender_name} ({company_name})"

    # ── Assemble ─────────────────────────────
    parts = [opener]
    if pain_block:
        parts.append(pain_block)
    parts += [offer, cta, sign_off]

    message = "\n\n".join(parts)
    message = _sanitise_message(message, lead)

    return message.strip()


# ══════════════════════════════════════════════
# OPENER BLOCK — with search intent injection
# ══════════════════════════════════════════════

def _build_opener(
    name: str,
    industry: str,
    city: str,
    location: str,
    country: str,
    rating: float,
    reviews: int,
    audit_score: int,
    audit_tag: str,
    has_site: bool,
    tone: str,
    intent_line: str,
    wa_status: str,
) -> str:

    audit_ref = f"(website audit: {audit_score}/10 — {audit_tag})"

    # intent_line is appended after the first sentence when present
    intent_part = f"\n\n{intent_line}" if intent_line else ""

    # Country-aware greeting style
    if tone == "formal" or country.lower() in {"uk", "ireland", "south africa"}:
        greeting = f"Dear {name},\n\n"
    elif tone == "direct":
        greeting = f"{name},\n\n"
    else:
        greeting = f"Hi {name},\n\n"

    # ── No website ───────────────────────────
    if not has_site:
        if tone == "formal":
            body = (
                f"I recently reviewed {name}'s web presence {audit_ref} "
                f"and found no website on record for your {industry} "
                f"business in {location}."
            )
        elif tone == "direct":
            body = (
                f"No website found for {name} {audit_ref}."
            )
        else:
            body = (
                f"I ran a quick audit on {name}'s web presence — "
                f"there's no website on record at all {audit_ref}."
            )

        if wa_status == "estimated":
            body += (
                f"{intent_part}\n\n"
                "If this WhatsApp number isn't the best one for you, no problem — "
                "I can also follow up by call or email."
            )
        else:
            body += intent_part

        if tone == "formal":
            body += (
                f"\n\nA professional website would significantly improve how "
                f"new clients find and trust {name}."
            )
        return greeting + body

    # ── Has website — strong rating ──────────
    if rating >= 4.0 and reviews >= 20:
        if tone == "formal":
            body = (
                f"I reviewed {name}'s online presence {audit_ref}. "
                f"Your {rating}★ rating across {reviews} reviews "
                f"demonstrates clear quality — however, your website "
                f"does not yet reflect that standard."
            )
        elif tone == "direct":
            body = (
                f"{rating}★ with {reviews} reviews is solid {audit_ref}. "
                f"But your website isn't converting that reputation into new clients."
            )
        else:
            body = (
                f"Your {rating}★ rating with {reviews} reviews is genuinely "
                f"impressive {audit_ref}. But the website isn't doing that "
                f"reputation justice right now."
            )

        return greeting + body + intent_part

    # ── Has website — weak reviews ────────────
    if reviews < 10:
        if tone == "formal":
            body = (
                f"I conducted a brief audit of {name}'s web presence "
                f"{audit_ref}. While a website exists, trust signals "
                f"such as reviews and contact optimisation are low."
            )
        elif tone == "direct":
            body = (
                f"Website exists but trust signals are weak {audit_ref}. "
                f"Fewer than {max(reviews, 1)} reviews means new customers may hesitate."
            )
        else:
            body = (
                f"I had a quick look at {name}'s website {audit_ref}. "
                f"It's there — but trust signals like reviews and contact "
                f"features are lower than they should be for a {industry}."
            )

        if wa_status == "estimated":
            body += (
                f"{intent_part}\n\n"
                "If this is the wrong WhatsApp number, just let me know and I won't disturb you again."
            )
        else:
            body += intent_part

        return greeting + body

    # ── Has website — general ─────────────────
    if tone == "formal":
        body = (
            f"I reviewed {name}'s current website {audit_ref} and "
            f"identified several areas where improvement would "
            f"meaningfully increase client enquiries."
        )
    elif tone == "direct":
        body = (
            f"Ran your site through an audit {audit_ref}. "
            f"There is clear room to improve how it converts visitors "
            f"into paying clients."
        )
    else:
        body = (
            f"I had a look at {name}'s website {audit_ref}. "
            f"There's a solid foundation there — but a few improvements "
            f"could make a real difference to how many enquiries come through."
        )

    return greeting + body + intent_part


# ══════════════════════════════════════════════
# PAIN BLOCK
# ══════════════════════════════════════════════

def _build_pain_block(pain_points: list[str], tone: str) -> str:
    if not pain_points:
        return ""

    if tone == "formal":
        header = "Key findings from the audit:"
    elif tone == "direct":
        header = "Issues found:"
    else:
        header = "Here's what I found:"

    bullets = "\n".join(f"• {p}" for p in pain_points[:3])
    return f"{header}\n{bullets}"


# ══════════════════════════════════════════════
# OFFER BLOCK
# ══════════════════════════════════════════════

def _build_offer(industry: str, tone: str) -> str:
    if tone == "formal":
        return (
            f"KNCS specialises in professional website design for "
            f"{industry} businesses — mobile-optimised, with WhatsApp "
            f"integration and local SEO built in."
        )
    if tone == "direct":
        return (
            f"I build websites for {industry} businesses — "
            f"mobile-ready, WhatsApp contact button included, "
            f"designed to bring in local clients."
        )
    return (
        f"I build websites specifically for {industry} businesses — "
        f"mobile-friendly, with a WhatsApp button and local SEO "
        f"so the right people can actually find and contact you."
    )


# ══════════════════════════════════════════════
# CTA BLOCK
# ══════════════════════════════════════════════

def _build_cta(name: str, tone: str, cta_link: str = "", wa_status: str = "unknown") -> str:
    link_line = f"\n{cta_link}" if cta_link else ""
    verified = wa_status in {"verified", "opted_in", "business_confirmed"}

    if tone == "formal":
        base = (
            "Would you be available for a brief 10-minute call to "
            "review what an improved website could do for your enquiries?"
        )
    elif tone == "direct":
        base = "Worth a quick 10-min call? I can show you exactly what I'd do."
    else:
        base = (
            f"Would you be open to a quick 10-minute call? "
            f"I can show you exactly what a new site would look like for "
            f"{name} — no pressure at all."
        )

    if not verified:
        base += (
            " If this is only an estimated WhatsApp number, no worries — "
            "I can follow up by call or email instead."
        )

    return base + link_line


# ══════════════════════════════════════════════
# WA PREFILL LINK BUILDER
# ══════════════════════════════════════════════

def build_wa_prefill_link(wa_number_digits: str, message: str) -> str:
    """
    Builds a wa.me prefilled link.

    Args:
        wa_number_digits : Phone number digits only (no + or spaces)
        message          : Message text to prefill

    Returns:
        Full wa.me URL string
    """
    digits = re.sub(r"\D", "", str(wa_number_digits or ""))
    if not digits:
        return ""

    encoded = quote(message, safe="")
    return f"https://wa.me/{digits}?text={encoded}"