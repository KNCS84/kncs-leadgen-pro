# engine/router.py
# KNCS LeadGen Pro — v10.1 (Audit Compliant)
# Matches Master Technical Audit & Project Handover Document v1.0
#
# Fixes applied in this revision:
#   BUG-024: valid_explicit = {"verified", "opted_in"} — "estimated" excluded
#   BUG-025: _market_prefers_call_over_email() — Nigeria routes call before email
#   BUG-026: WA_FIELD_GROUP atomic enforcement — entire group skipped on violation
#   ISSUE-A/B: write_channel_fields() and apply_routing_updates() own their
#              connections via get_connection() — no raw conn parameter
#   ISSUE-G:   ai_call_ready threshold corrected to fit_score >= 60 (0-100 scale)
#   ISSUE-J:   apply_routing_updates() skips ALL WA fields on incomplete group,
#              not just whatsapp_ready

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from database import get_connection

try:
    from engine.country_router import enrich_lead_with_country, get_market_profile
except Exception:
    def enrich_lead_with_country(lead: dict) -> dict:
        return lead

    def get_market_profile(country: str) -> dict:
        return {
            "market":             "other",
            "preferred_channels": ["call", "email", "whatsapp_verified"],
            "wa_primary":         False,
            "tone":               "direct",
        }


# ─────────────────────────────────────────────────────────
# Allowed columns for routing updates
# SQL injection prevention — only these columns writable via routing
# ─────────────────────────────────────────────────────────
ROUTING_ALLOWED_COLUMNS = {
    "country", "market", "timezone", "phone_country_code",
    "country_confidence", "call_ready", "email_ready",
    "whatsapp_ready", "whatsapp_verified", "wa_verified",
    "wa_opt_in", "wa_eligible", "wa_status",
    "ai_call_ready", "manual_review", "best_channel",
    "channel_label", "routing_reason", "phone_verified",
    "whatsapp_check_attempted", "whatsapp_check_at",
}

# BUG-026: these fields must all be written together or not at all
WA_FIELD_GROUP = {
    "whatsapp_ready", "whatsapp_verified", "wa_verified",
    "wa_opt_in", "wa_eligible", "wa_status",
}


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def safe_int(value, default: int = 0) -> int:
    """Safe int — never raises on empty string or None."""
    try:
        if value is None or value == "":
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _norm(value) -> str:
    return str(value or "").strip().lower()


# ─────────────────────────────────────────────────────────
# Phone / email validation
# ─────────────────────────────────────────────────────────

def is_valid_phone(phone: str) -> bool:
    if not phone:
        return False
    phone   = str(phone).strip()
    cleaned = re.sub(r"[\s\-\(\)\.]", "", phone)
    digit_count = sum(c.isdigit() for c in cleaned)
    if digit_count < 7 or digit_count > 15:
        return False
    placeholders = {"0000000", "1111111", "1234567", "9999999"}
    if any(p in cleaned for p in placeholders):
        return False
    return True


def is_valid_email(email: str) -> bool:
    if not email:
        return False
    email   = str(email).strip().lower()
    pattern = r"^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+$"
    if not re.match(pattern, email):
        return False
    fake_domains = {
        "example.com", "test.com", "placeholder.com",
        "noreply.com", "no-reply.com",
    }
    domain = email.split("@")[-1]
    if domain in fake_domains:
        return False
    return True


# ─────────────────────────────────────────────────────────
# Industry sets
# ─────────────────────────────────────────────────────────
WHATSAPP_FRIENDLY_INDUSTRIES = {
    "restaurant", "hotel", "salon", "beauty", "spa", "clinic",
    "pharmacy", "logistics", "transport", "travel", "events",
    "catering", "cleaning", "plumbing", "electrical", "retail",
    "fashion", "real estate", "property", "agency", "freelance",
    "consulting", "photography", "fitness", "gym", "laundry",
    "dentist", "mechanic", "garage", "builder", "construction",
}

CALL_HEAVY_INDUSTRIES = {
    "hotel", "restaurant", "clinic", "pharmacy", "plumbing",
    "electrical", "transport", "logistics", "cleaning",
    "construction", "security", "insurance", "estate agent",
    "solicitor", "accountant", "dentist", "mechanic", "garage",
    "lawyer", "doctor", "doctor's office",
}

WHATSAPP_PRIMARY_COUNTRIES = {
    "nigeria", "ghana", "kenya", "south africa",
    "india", "pakistan", "bangladesh", "indonesia",
    "philippines", "malaysia", "singapore", "uae",
    "united arab emirates", "saudi arabia", "egypt",
    "brazil", "mexico", "colombia", "argentina", "chile",
}

WHATSAPP_MEDIUM_COUNTRIES = {
    "united kingdom", "uk", "ireland", "germany", "france",
    "italy", "spain", "netherlands", "belgium", "switzerland",
    "austria", "sweden", "norway", "denmark", "finland", "poland",
}


# ─────────────────────────────────────────────────────────
# Market preference helpers
# BUG-025: Nigeria routes call before email
# ─────────────────────────────────────────────────────────

def _market_prefers_call_over_email(lead: dict) -> bool:
    """
    BUG-025: Returns True when market convention is to call before email.
    Nigeria / Africa markets prioritise voice over email.
    """
    country  = _norm(lead.get("country") or "")
    market   = _norm(lead.get("market") or "")
    profile  = get_market_profile(lead.get("country", "") or "")
    preferred = profile.get("preferred_channels", [])

    if country == "nigeria" or market in {"nigeria", "africa"}:
        return True
    # Any market profile that lists "call" before "email"
    if preferred and preferred[0] == "call" and "email" in preferred:
        return True
    return False


# ─────────────────────────────────────────────────────────
# WhatsApp readiness
# BUG-024: bypass_statuses = {"verified", "opted_in"} — "estimated" excluded
# ─────────────────────────────────────────────────────────

def estimate_whatsapp_readiness(lead: dict) -> dict:
    """
    Estimate WA readiness from lead data.
    Accepts an ALREADY-ENRICHED lead — no double enrichment here.

    BUG-024: bypass statuses = {"verified", "opted_in"} only.
             "estimated" is NEVER in bypass — it does not skip the
             UK/EU market guard or grant verified status.
    """
    phone = lead.get("phone", "") or ""
    if not is_valid_phone(phone):
        return {
            "whatsapp_ready": 0,
            "confidence":     "high",
            "wa_status":      "unknown",
            "wa_verified":    0,
            "wa_opt_in":      0,
            "wa_eligible":    0,
            "reason":         "No valid phone number present",
        }

    wa_verified_flag = (
        safe_int(lead.get("wa_verified"))
        or safe_int(lead.get("whatsapp_verified"))
    )
    wa_opt_in_flag = safe_int(lead.get("wa_opt_in"))

    raw_status = _norm(lead.get("wa_status") or "")
    ver_status = _norm(lead.get("verification_status") or "")

    # BUG-024: ONLY "verified" and "opted_in" are bypass statuses.
    # "estimated" is explicitly NOT included — it must go through the
    # full market logic below and cannot bypass the UK/EU guard.
    valid_explicit = {"verified", "opted_in"}

    if raw_status in valid_explicit:
        explicit_status = raw_status
    elif ver_status in valid_explicit:
        explicit_status = ver_status
    else:
        explicit_status = ""

    # Fast path: explicit verification always wins
    if explicit_status in valid_explicit or wa_verified_flag:
        return {
            "whatsapp_ready": 1,
            "confidence":     "high",
            "wa_status":      "verified" if wa_verified_flag else explicit_status,
            "wa_verified":    1,
            "wa_opt_in":      1 if (explicit_status == "opted_in" or wa_opt_in_flag) else 0,
            "wa_eligible":    1,
            "reason":         "Manually verified as WhatsApp active",
        }

    # business_confirmed — high confidence, not bypass
    if raw_status == "business_confirmed" or ver_status == "business_confirmed":
        return {
            "whatsapp_ready": 1,
            "confidence":     "high",
            "wa_status":      "business_confirmed",
            "wa_verified":    0,
            "wa_opt_in":      0,
            "wa_eligible":    1,
            "reason":         "Business confirmed via verification",
        }

    country  = _norm(lead.get("country") or "")
    industry = _norm(lead.get("industry") or "")
    market   = _norm(lead.get("market") or "")

    industry_match = any(ind in industry for ind in WHATSAPP_FRIENDLY_INDUSTRIES)
    phone_verified = safe_int(lead.get("phone_verified"))

    # Primary WA markets (Nigeria, Africa, Asia, LatAm, ME)
    if country in WHATSAPP_PRIMARY_COUNTRIES or market in {
        "nigeria", "africa", "latam", "asia", "middle_east"
    }:
        if phone_verified and industry_match:
            return {
                "whatsapp_ready": 1,
                "confidence":     "high",
                "wa_status":      "business_confirmed",
                "wa_verified":    0,
                "wa_opt_in":      0,
                "wa_eligible":    1,
                "reason": (
                    f"Primary WhatsApp market + "
                    f"WhatsApp-friendly industry ({industry})"
                ),
            }
        return {
            "whatsapp_ready": 1,
            "confidence":     "high" if industry_match else "medium",
            "wa_status":      "estimated",
            "wa_verified":    0,
            "wa_opt_in":      0,
            "wa_eligible":    1,
            "reason": (
                f"Primary WhatsApp market ({country or market})"
                + (
                    f" + WhatsApp-friendly industry ({industry})"
                    if industry_match else ""
                )
            ),
        }

    # BUG-024: UK/EU/NA guard — "estimated" cannot bypass this block.
    # Leads reaching here with wa_status="estimated" in DB are re-evaluated
    # correctly — they do NOT get whatsapp_ready=1 for UK/EU markets.
    if country in WHATSAPP_MEDIUM_COUNTRIES or market in {
        "uk", "europe", "north_america"
    }:
        return {
            "whatsapp_ready": 0,
            "confidence":     "medium" if industry_match else "low",
            "wa_status":      "unknown",
            "wa_verified":    0,
            "wa_opt_in":      0,
            "wa_eligible":    0,
            "reason": (
                "UK/EU/NA market — use email/call first "
                "unless WhatsApp confirmed"
            ),
        }

    # Unknown country
    return {
        "whatsapp_ready": 0,
        "confidence":     "low",
        "wa_status":      "unknown",
        "wa_verified":    0,
        "wa_opt_in":      0,
        "wa_eligible":    0,
        "reason":         "Country unknown — cannot confirm WhatsApp readiness",
    }


# ─────────────────────────────────────────────────────────
# Full channel routing
# BUG-024: verified > estimated in channel priority
# BUG-025: Nigeria routes call before email
# BUG-026: WA field group integrity
# ISSUE-G: ai_call_ready threshold on 0-100 scale (>= 60)
# ─────────────────────────────────────────────────────────

def assign_channel(lead: dict) -> dict:
    """
    Assign best outreach channel to a lead.

    Enrich once here — do NOT re-enrich inside estimate_whatsapp_readiness.
    BUG-024: verified leads get priority label over estimated.
    BUG-025: Nigeria market routes to call before email.
    BUG-026: WA field group written atomically.
    ISSUE-G: ai_call_ready uses fit_score >= 60 (scorer outputs 0-100 scale).
    """
    # Enrich ONCE — pass enriched lead to all downstream helpers
    lead = enrich_lead_with_country(dict(lead or {}))

    phone     = lead.get("phone", "") or ""
    email     = lead.get("email", "") or ""
    industry  = _norm(lead.get("industry") or "")
    # ISSUE-G: fit_score is on 0-100 scale from scorer.py
    fit_score = safe_int(lead.get("fit_score"))

    has_phone = is_valid_phone(phone)
    has_email = is_valid_email(email)

    # WA readiness — pass already-enriched lead, no second enrichment
    wa_result      = estimate_whatsapp_readiness(lead)
    whatsapp_ready = safe_int(wa_result["whatsapp_ready"])
    wa_status      = wa_result["wa_status"]

    # Verified vs estimated distinction (BUG-024)
    wa_verified = 1 if wa_status in {"verified", "opted_in"} else 0
    wa_opt_in   = 1 if wa_status == "opted_in" else 0
    wa_eligible = safe_int(wa_result.get("wa_eligible", whatsapp_ready))

    call_ready  = 1 if has_phone else 0
    email_ready = 1 if has_email else 0

    # ISSUE-G: threshold corrected to 60 — scorer.py outputs 0-100
    ai_call_ready = (
        1
        if has_phone
        and any(ind in industry for ind in CALL_HEAVY_INDUSTRIES)
        and fit_score >= 60
        else 0
    )
    manual_review = 1 if not has_phone and not has_email else 0

    market_profile     = get_market_profile(lead.get("country", "") or "")
    preferred_channels = market_profile.get("preferred_channels", [])
    market             = market_profile.get("market", "other")

    market_prefers_call = _market_prefers_call_over_email(lead)

    # ── Best channel selection ─────────────────────────────
    # Priority order:
    # 1. WhatsApp verified/opted_in (highest confidence)
    # 2. WhatsApp business_confirmed
    # 3. WhatsApp estimated (primary WA markets)
    # 4. AI Call (call-heavy industry + high fit)
    # 5. Call before email (Nigeria/Africa market preference — BUG-025)
    # 6. Email (market prefers email first)
    # 7. Email + Call (both present, non-call-preference market)
    # 8. Call only
    # 9. Manual (no contact data)

    if whatsapp_ready and wa_status in {"verified", "opted_in"}:
        best_channel   = "whatsapp"
        channel_label  = "WhatsApp ✓ Verified"
        routing_reason = wa_result["reason"]

    elif whatsapp_ready and wa_status == "business_confirmed":
        best_channel   = "whatsapp"
        channel_label  = "WhatsApp ✓ Business"
        routing_reason = wa_result["reason"]

    elif whatsapp_ready:
        # Estimated — primary WA markets only (BUG-024: UK/EU never reaches here)
        best_channel   = "whatsapp"
        channel_label  = "WhatsApp (Estimated)"
        routing_reason = wa_result["reason"]

    elif ai_call_ready and fit_score >= 60:
        best_channel   = "ai_call"
        channel_label  = "AI Call"
        routing_reason = (
            f"Call-heavy industry ({industry}) "
            f"with strong fit score ({fit_score}/100)"
        )

    elif market_prefers_call and call_ready:
        # BUG-025: Nigeria/Africa — call is preferred channel regardless
        # of whether email is also available
        best_channel   = "manual"
        channel_label  = "Call First — Market Preference"
        routing_reason = (
            "Nigeria/Africa market: voice call preferred over email. "
            "Initiate with phone call."
        )

    elif email_ready and preferred_channels and preferred_channels[0] == "email":
        best_channel   = "email"
        channel_label  = "Email First"
        routing_reason = "Market profile prefers email before call/WhatsApp"

    elif email_ready and has_phone and not market_prefers_call:
        best_channel   = "email"
        channel_label  = "Email + Call"
        routing_reason = "Both email and phone present — lead with email first."

    elif call_ready:
        best_channel   = "manual"
        channel_label  = "Call Only"
        routing_reason = "Phone present — use manual call."

    elif manual_review:
        best_channel   = "manual"
        channel_label  = "Manual Research"
        routing_reason = (
            "No valid phone or email — needs manual research."
            + (" Invalid phone detected." if phone else "")
        )

    else:
        best_channel   = "manual"
        channel_label  = "Unrouted"
        routing_reason = "Insufficient data to route automatically."

    return {
        # Geography
        "country":            lead.get("country", ""),
        "market":             market,
        "timezone":           lead.get("timezone", ""),
        "phone_country_code": lead.get("phone_country_code", ""),
        "country_confidence": lead.get("country_confidence", "low"),

        # Contact readiness
        "call_ready":    call_ready,
        "email_ready":   email_ready,
        "phone_verified": 1 if has_phone else 0,

        # WhatsApp fields (BUG-026: written as a complete group)
        "whatsapp_ready":    whatsapp_ready,
        "whatsapp_verified": wa_verified,
        "wa_verified":       wa_verified,
        "wa_opt_in":         wa_opt_in,
        "wa_eligible":       wa_eligible,
        "wa_status":         wa_status,

        # Routing
        "ai_call_ready":            ai_call_ready,
        "manual_review":            manual_review,
        "best_channel":             best_channel,
        "channel_label":            channel_label,
        "routing_reason":           routing_reason,
        "whatsapp_check_attempted": 1,
        "whatsapp_check_at":        datetime.now(timezone.utc).isoformat(),

        # verification_status intentionally OMITTED:
        # Writing "unverified" back to DB contaminates wa_status lookups.
        # wa_status field above carries the ground truth.
    }


# ─────────────────────────────────────────────────────────
# Batch routing
# ─────────────────────────────────────────────────────────

def batch_route_leads(leads: list) -> list:
    """Route a list of leads. leads=None guard applied."""
    leads   = leads or []
    results = []
    for lead in leads:
        try:
            updates = assign_channel(lead)
            results.append({"id": lead.get("id"), "updates": updates})
        except Exception as exc:
            results.append({
                "id":     lead.get("id"),
                "updates": {
                    "channel_label":  "Error",
                    "routing_reason": f"Routing failed: {exc}",
                    "manual_review":  1,
                },
            })
    return results


# ─────────────────────────────────────────────────────────
# Atomic channel field write
# BUG-026: whatsapp_ready=1 requires wa_status — entire WA group
#          is written atomically or not at all
# ISSUE-A: owns its connection via get_connection()
# ─────────────────────────────────────────────────────────

def write_channel_fields(lead_id: int, fields: dict) -> bool:
    """
    BUG-026: Atomic enforcement — whatsapp_ready=1 requires wa_status.
    If the WA field group is incomplete, the ENTIRE WA group is skipped.
    ISSUE-A: Opens its own connection via get_connection() — never accepts
             a raw conn parameter that bypasses PRAGMA settings.
    """
    if not lead_id or not fields:
        return False

    safe_fields = {k: v for k, v in fields.items() if k in ROUTING_ALLOWED_COLUMNS}
    if not safe_fields:
        return False

    # BUG-026: enforce WA_FIELD_GROUP atomicity
    safe_fields = _enforce_wa_group_atomicity(safe_fields)
    if not safe_fields:
        return False

    conn = get_connection()
    try:
        set_clause = ", ".join(f"{col} = ?" for col in safe_fields.keys())
        values     = list(safe_fields.values()) + [lead_id]
        conn.execute(
            f"UPDATE leads SET {set_clause}, updated_at = datetime('now') "
            f"WHERE id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def _enforce_wa_group_atomicity(fields: dict) -> dict:
    """
    BUG-026: If whatsapp_ready=1 is present without wa_status,
    remove the ENTIRE WA field group from the update dict.
    Partial WA writes corrupt channel routing state.
    Returns the cleaned fields dict.
    """
    if "whatsapp_ready" not in fields:
        return fields

    ready_val = fields.get("whatsapp_ready")
    is_ready  = ready_val in {1, "1", True}

    if is_ready and not fields.get("wa_status"):
        # Remove entire WA group — not just whatsapp_ready
        return {k: v for k, v in fields.items() if k not in WA_FIELD_GROUP}

    return fields


def apply_routing_updates(lead_id: int, updates: dict) -> bool:
    """
    Write routing result to DB for a single lead.
    BUG-026: WA_FIELD_GROUP atomic enforcement via _enforce_wa_group_atomicity().
    ISSUE-B: owns its connection via get_connection().
    Column whitelist prevents SQL injection.

    Signature changed from (conn, routing_results: list) to
    (lead_id: int, updates: dict) — callers update one lead at a time.
    For batch use, call apply_routing_updates_batch().
    """
    if not lead_id or not updates:
        return False

    safe_updates = {
        col: val
        for col, val in updates.items()
        if col in ROUTING_ALLOWED_COLUMNS
    }
    if not safe_updates:
        return False

    # BUG-026: atomic WA group enforcement
    safe_updates = _enforce_wa_group_atomicity(safe_updates)
    if not safe_updates:
        return False

    conn = get_connection()
    try:
        set_clause = ", ".join(f"{col} = ?" for col in safe_updates.keys())
        values     = list(safe_updates.values()) + [lead_id]
        conn.execute(
            f"UPDATE leads SET {set_clause}, updated_at = datetime('now') "
            f"WHERE id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def apply_routing_updates_batch(routing_results: list) -> None:
    """
    Batch variant — writes routing results for a list of leads.
    Each lead gets its own connection to avoid long-held transactions.
    BUG-026: atomicity enforced per-lead via apply_routing_updates().
    """
    if not routing_results:
        return
    for result in routing_results:
        lead_id = result.get("id")
        updates = result.get("updates") or {}
        if not lead_id or not updates:
            continue
        try:
            apply_routing_updates(lead_id, updates)
        except Exception:
            pass  # Per-lead failure does not abort the batch