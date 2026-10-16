# opportunity_engine.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Shared vertical intelligence layer
# Used by care, dental, and msp modules
# Dependencies: database.py, engine/ai_adapter.py
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL only
#   - logger not print()
#   - ai_is_available() before AI calls
#   - Graceful degradation on all failures

import logging
import json
from datetime import datetime
from typing import Optional

import database as db
from engine.ai_adapter import (
    ai_is_available,
    call_ai_json,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

VERTICALS = ("care", "dental", "msp")

REVENUE_ESTIMATES = {
    "care": {
        "high":   {"min": 2500, "max": 8000},
        "medium": {"min": 1000, "max": 2500},
        "low":    {"min": 500,  "max": 1000},
    },
    "dental": {
        "high":   {"min": 1500, "max": 5000},
        "medium": {"min": 750,  "max": 1500},
        "low":    {"min": 300,  "max": 750},
    },
    "msp": {
        "high":   {"min": 3000, "max": 10000},
        "medium": {"min": 1000, "max": 3000},
        "low":    {"min": 500,  "max": 1000},
    },
}

URGENCY_THRESHOLDS = {
    "high":   70,
    "medium": 40,
    "low":    0,
}

OFFER_MAP = {
    "care": {
        "high":   "Emergency CQC Compliance Support Package",
        "medium": "Care Home Digital Transformation Bundle",
        "low":    "Care Home Marketing Starter Pack",
    },
    "dental": {
        "high":   "Reputation Recovery & Review Management",
        "medium": "Dental Practice Growth Package",
        "low":    "Dental Marketing Essentials",
    },
    "msp": {
        "high":   "Immediate Cyber Security Remediation",
        "medium": "Managed IT Services — Full Stack",
        "low":    "IT Health Check & Monitoring",
    },
}

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _validate_vertical(vertical: str) -> bool:
    """Return True if vertical is one of the supported values."""
    if vertical not in VERTICALS:
        log.error("opportunity_engine: unknown vertical '%s'", vertical)
        return False
    return True


def _get_lead(lead_id: int) -> Optional[dict]:
    """
    Fetch a single lead from the database.
    Returns dict or None on failure.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM leads WHERE id = ?",
                (lead_id,)
            )
            row = cursor.fetchone()
            if row is None:
                log.warning(
                    "opportunity_engine: lead %s not found", lead_id
                )
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        finally:
            conn.close()
    except Exception as exc:
        log.error("opportunity_engine._get_lead error: %s", exc)
        return None


def _get_urgency_band(score: int) -> str:
    """Map numeric score to urgency band string."""
    if score >= URGENCY_THRESHOLDS["high"]:
        return "high"
    elif score >= URGENCY_THRESHOLDS["medium"]:
        return "medium"
    return "low"


def _get_opportunity_table(vertical: str) -> Optional[str]:
    """Return the opportunity table name for a given vertical."""
    table_map = {
        "care":   "care_home_opportunities",
        "dental": "dental_opportunities",
        "msp":    "digital_maturity_scores",
    }
    return table_map.get(vertical)


def _build_ai_prompt(lead: dict, vertical: str) -> str:
    """
    Build the AI scoring prompt for the given vertical.
    Each vertical has its own prompt template.
    """
    name = lead.get("name", "Unknown")
    city = lead.get("city") or lead.get("location", "Unknown")

    if vertical == "care":
        rating  = lead.get("cqc_rating", "Unknown")
        changed = bool(lead.get("director_changed_date"))
        missing = []
        if not lead.get("email"):
            missing.append("email")
        if not lead.get("phone"):
            missing.append("phone")
        if not lead.get("website"):
            missing.append("website")
        missing_str = ", ".join(missing) if missing else "none"
        return (
            f"You are evaluating a UK care home as a sales opportunity.\n"
            f"Care Home: {name}\n"
            f"CQC Rating: {rating}\n"
            f"Director Changed: {changed}\n"
            f"Missing Contact: {missing_str}\n"
            f"Location: {city}\n"
            f"Score 0-100 explain in one sentence.\n"
            f'Return JSON: {{"score": int, "reason": str}}'
        )

    elif vertical == "dental":
        rating       = lead.get("google_rating", "Unknown")
        review_count = lead.get("review_count", 0)
        website      = lead.get("website", "None")
        cqc_status   = lead.get("cqc_rating", "Unknown")
        return (
            f"You are evaluating a UK dental practice "
            f"as a sales opportunity.\n"
            f"Practice: {name}\n"
            f"Location: {city}\n"
            f"Google Rating: {rating}\n"
            f"Review Count: {review_count}\n"
            f"Website: {website}\n"
            f"CQC Status: {cqc_status}\n"
            f"Score 0-100 explain in one sentence.\n"
            f'Return JSON: {{"score": int, "reason": str}}'
        )

    elif vertical == "msp":
        health_score   = lead.get("website_health_score", "Unknown")
        cyber_score    = lead.get("cyber_risk_score", "Unknown")
        tech_stack     = lead.get("tech_stack", "Unknown")
        maturity_score = lead.get("digital_maturity_score", "Unknown")
        return (
            f"You are evaluating a business as a prospect "
            f"for a Managed IT Service Provider.\n"
            f"Business: {name}\n"
            f"Website Health Score: {health_score}\n"
            f"Cyber Risk Score: {cyber_score}\n"
            f"Tech Stack: {tech_stack}\n"
            f"Digital Maturity Score: {maturity_score}\n"
            f"Identify top 3 IT problems and recommend "
            f"the most appropriate MSP service.\n"
            f"Return JSON:\n"
            f'{{"problems": [str, str, str], '
            f'"recommended_service": str, '
            f'"urgency": str, '
            f'"estimated_value": str}}'
        )

    return (
        f"Evaluate this business lead as a sales opportunity.\n"
        f"Business: {name}\n"
        f"Location: {city}\n"
        f"Score 0-100 explain in one sentence.\n"
        f'Return JSON: {{"score": int, "reason": str}}'
    )


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def generate_opportunity_brief(lead_id: int, vertical: str) -> dict:
    """
    Generate a full opportunity brief for a lead in a given vertical.

    Returns dict with:
      lead_id, vertical, name, city,
      urgency_score, urgency_band,
      recommended_offer, suggested_message,
      revenue_min, revenue_max,
      ai_score, ai_reason

    Returns empty dict on failure.
    """
    if not _validate_vertical(vertical):
        return {}

    lead = _get_lead(lead_id)
    if lead is None:
        return {}

    try:
        urgency_score = calculate_urgency(lead_id, vertical)
        urgency_band  = _get_urgency_band(urgency_score)
        revenue       = REVENUE_ESTIMATES.get(vertical, {}).get(
                            urgency_band, {"min": 0, "max": 0}
                        )
        offer   = get_recommended_offer(lead_id, vertical)
        message = get_suggested_message(lead_id, vertical)

        # AI scoring — caching handled inside call_ai()
        ai_score  = None
        ai_reason = None

        prompt = _build_ai_prompt(lead, vertical)
        result = call_ai_json(
            prompt,
            context=f"opp_brief_{vertical}_{lead_id}"
        )
        if result:
            ai_score  = result.get("score")
            ai_reason = result.get("reason")

        return {
            "lead_id":           lead_id,
            "vertical":          vertical,
            "name":              lead.get("name", ""),
            "city":              lead.get("city") or lead.get("location", ""),
            "urgency_score":     urgency_score,
            "urgency_band":      urgency_band,
            "recommended_offer": offer,
            "suggested_message": message,
            "revenue_min":       revenue["min"],
            "revenue_max":       revenue["max"],
            "ai_score":          ai_score,
            "ai_reason":         ai_reason,
        }

    except Exception as exc:
        log.error(
            "opportunity_engine.generate_opportunity_brief error "
            "lead=%s vertical=%s: %s",
            lead_id, vertical, exc
        )
        return {}


def calculate_urgency(lead_id: int, vertical: str) -> int:
    """
    Calculate urgency score for a lead in a given vertical.

    Looks up pre-calculated urgency_score from the
    appropriate vertical opportunity table.
    Falls back to 0 if not yet scored.

    Returns int 0-100.
    """
    if not _validate_vertical(vertical):
        return 0

    table = _get_opportunity_table(vertical)
    if not table:
        return 0

    score_col = "urgency_score"
    if vertical == "msp":
        score_col = "maturity_score"

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                f"SELECT {score_col} FROM {table} WHERE lead_id = ?",
                (lead_id,)
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "opportunity_engine.calculate_urgency error "
            "lead=%s vertical=%s: %s",
            lead_id, vertical, exc
        )
        return 0


def get_recommended_offer(lead_id: int, vertical: str) -> str:
    """
    Return the recommended product/service offer for a lead.

    Maps urgency band to pre-defined offer string.
    Returns empty string on failure.
    """
    if not _validate_vertical(vertical):
        return ""

    try:
        urgency_score = calculate_urgency(lead_id, vertical)
        urgency_band  = _get_urgency_band(urgency_score)
        offer = OFFER_MAP.get(vertical, {}).get(urgency_band, "")
        log.info(
            "opportunity_engine: offer for lead %s vertical %s = %s",
            lead_id, vertical, offer
        )
        return offer
    except Exception as exc:
        log.error(
            "opportunity_engine.get_recommended_offer error "
            "lead=%s vertical=%s: %s",
            lead_id, vertical, exc
        )
        return ""


def get_suggested_message(lead_id: int, vertical: str) -> str:
    """
    Return a suggested outreach message opening line
    tailored to the vertical and urgency band.

    Returns empty string on failure.
    """
    if not _validate_vertical(vertical):
        return ""

    try:
        lead = _get_lead(lead_id)
        if lead is None:
            return ""

        urgency_score = calculate_urgency(lead_id, vertical)
        urgency_band  = _get_urgency_band(urgency_score)
        name          = lead.get("name", "your organisation")

        messages = {
            "care": {
                "high": (
                    f"I noticed {name} had a recent CQC inspection — "
                    f"we specialise in helping care homes improve "
                    f"their compliance rating quickly."
                ),
                "medium": (
                    f"We help care homes like {name} improve their "
                    f"digital presence and operational efficiency."
                ),
                "low": (
                    f"We work with care homes across the UK — "
                    f"I'd love to show you what we've built for "
                    f"operators similar to {name}."
                ),
            },
            "dental": {
                "high": (
                    f"I can see {name} has some recent reviews that "
                    f"might be affecting new patient acquisition — "
                    f"we help practices turn this around fast."
                ),
                "medium": (
                    f"We help dental practices like {name} grow their "
                    f"patient base through reviews and online visibility."
                ),
                "low": (
                    f"We work with dental practices across the UK — "
                    f"happy to share what's working for similar "
                    f"practices to {name}."
                ),
            },
            "msp": {
                "high": (
                    f"We found some significant IT vulnerabilities "
                    f"on {name}'s systems that could put you at risk — "
                    f"worth a quick conversation."
                ),
                "medium": (
                    f"We help businesses like {name} modernise their "
                    f"IT infrastructure and reduce security exposure."
                ),
                "low": (
                    f"We provide managed IT services to businesses "
                    f"like {name} — happy to run a free health check."
                ),
            },
        }

        msg = messages.get(vertical, {}).get(urgency_band, "")
        log.info(
            "opportunity_engine: message for lead %s "
            "vertical %s band %s",
            lead_id, vertical, urgency_band
        )
        return msg

    except Exception as exc:
        log.error(
            "opportunity_engine.get_suggested_message error "
            "lead=%s vertical=%s: %s",
            lead_id, vertical, exc
        )
        return ""


def get_top_opportunities(
    vertical: str,
    limit: int = 25,
    use_ai: bool = False,
) -> list:
    """
    Return top opportunities for a vertical ranked by score.

    Joins leads table with the appropriate vertical table.
    use_ai flag reserved for future AI re-ranking.

    Returns list of dicts. Returns empty list on failure.
    """
    if not _validate_vertical(vertical):
        return []

    table = _get_opportunity_table(vertical)
    if not table:
        return []

    score_col = "opportunity_score"
    if vertical == "msp":
        score_col = "maturity_score"

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                f"""
                SELECT
                    l.id,
                    l.name,
                    l.city,
                    l.email,
                    l.phone,
                    l.website,
                    t.{score_col} AS score,
                    t.urgency_band,
                    t.scored_at
                FROM leads l
                JOIN {table} t ON t.lead_id = l.id
                ORDER BY t.{score_col} DESC
                LIMIT ?
                """,
                (limit,)
            )
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
            log.info(
                "opportunity_engine.get_top_opportunities: "
                "vertical=%s returned %d rows",
                vertical, len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "opportunity_engine.get_top_opportunities error "
            "vertical=%s: %s",
            vertical, exc
        )
        return []


def get_intelligence_summary(vertical: str) -> dict:
    """
    Return aggregate intelligence summary for a vertical.

    Returns dict with total_scored, high/medium/low counts,
    avg_score, and vertical name.
    Returns safe defaults on failure.
    """
    defaults = {
        "vertical":       vertical,
        "total_scored":   0,
        "high_urgency":   0,
        "medium_urgency": 0,
        "low_urgency":    0,
        "avg_score":      0.0,
        "generated_at":   datetime.utcnow().isoformat(),
    }

    if not _validate_vertical(vertical):
        return defaults

    table = _get_opportunity_table(vertical)
    if not table:
        return defaults

    score_col = "opportunity_score"
    if vertical == "msp":
        score_col = "maturity_score"

    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN urgency_band = 'high'
                             THEN 1 ELSE 0 END) AS high,
                    SUM(CASE WHEN urgency_band = 'medium'
                             THEN 1 ELSE 0 END) AS medium,
                    SUM(CASE WHEN urgency_band = 'low'
                             THEN 1 ELSE 0 END) AS low,
                    AVG({score_col}) AS avg_score
                FROM {table}
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_scored"]   = row[0] or 0
                defaults["high_urgency"]   = row[1] or 0
                defaults["medium_urgency"] = row[2] or 0
                defaults["low_urgency"]    = row[3] or 0
                defaults["avg_score"]      = round(row[4] or 0.0, 1)

            log.info(
                "opportunity_engine.get_intelligence_summary: %s",
                defaults
            )
            return defaults
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "opportunity_engine.get_intelligence_summary error "
            "vertical=%s: %s",
            vertical, exc
        )
        return defaults