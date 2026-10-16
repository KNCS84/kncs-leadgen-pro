# care_intelligence.py
# KNCS LeadGen Pro — v2.1
# FIXES:
#   BUG-CI-01: l.name -> l.business_name in get_top_opportunities() SQL
#   BUG-CI-02: lead.get("name") -> lead.get("business_name") in _build_ai_prompt()
# All other logic unchanged from v2.0

import logging
from datetime import datetime, timedelta
from typing import Optional

import database as db
from engine.ai_adapter import call_ai_json

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

DIRECTOR_CHANGE_WINDOW_DAYS = 90

OPP_SCORE = {
    "cqc_inadequate":            40,
    "cqc_requires_improvement":  30,
    "cqc_good":                  10,
    "director_changed_90d":      20,
    "operator_group_member":     15,
    "no_website":                10,
    "no_email":                  10,
    "phone_verified":             5,
}

URG_SCORE = {
    "cqc_inspection_overdue":      25,
    "recent_negative_inspection":  25,
    "director_change_detected":    20,
    "multiple_missing_enrichment": 15,
    "no_current_supplier_signals": 15,
}

REVENUE_ESTIMATES = {
    "high":   {"min": 2500, "max": 8000},
    "medium": {"min": 1000, "max": 2500},
    "low":    {"min": 500,  "max": 1000},
}

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _get_lead(lead_id: int) -> Optional[dict]:
    """
    Fetch a single lead row from the database.
    Returns a dict of column to value, or None on failure.
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
                    "care_intelligence: lead_id %s not found", lead_id
                )
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        finally:
            conn.close()
    except Exception as exc:
        log.error("care_intelligence._get_lead error: %s", exc)
        return None


def _get_all_care_leads() -> list:
    """
    Return all leads tagged as care homes.
    Matches on industry field containing 'care'.
    Returns empty list on failure.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT * FROM leads
                WHERE LOWER(industry) LIKE '%care%'
                ORDER BY id DESC
                """
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        log.error("care_intelligence._get_all_care_leads error: %s", exc)
        return []


def _director_changed_recently(lead: dict) -> bool:
    """
    Return True if director change recorded within
    DIRECTOR_CHANGE_WINDOW_DAYS days.
    """
    try:
        raw = (
            lead.get("director_changed_date")
            or lead.get("director_change_date")
        )
        if not raw:
            return False
        changed_date = datetime.fromisoformat(str(raw))
        cutoff = datetime.utcnow() - timedelta(
            days=DIRECTOR_CHANGE_WINDOW_DAYS
        )
        return changed_date >= cutoff
    except Exception as exc:
        log.warning(
            "care_intelligence._director_changed_recently "
            "parse error: %s", exc
        )
        return False


def _is_in_operator_group(lead: dict) -> bool:
    """
    Return True if this lead is linked to an identified
    operator group in the care_operator_groups table.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT 1 FROM care_operator_groups
                WHERE lead_id = ?
                LIMIT 1
                """,
                (lead.get("id"),)
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "care_intelligence._is_in_operator_group error: %s", exc
        )
        return False


def _cqc_inspection_overdue(lead: dict) -> bool:
    """
    Return True if last CQC inspection date is over
    12 months ago or no inspection date is recorded.
    """
    try:
        raw = (
            lead.get("cqc_last_inspection_date")
            or lead.get("last_inspection_date")
        )
        if not raw:
            return True
        inspection_date = datetime.fromisoformat(str(raw))
        cutoff = datetime.utcnow() - timedelta(days=365)
        return inspection_date < cutoff
    except Exception as exc:
        log.warning(
            "care_intelligence._cqc_inspection_overdue "
            "parse error: %s", exc
        )
        return True


def _recent_negative_inspection(lead: dict) -> bool:
    """
    Return True if CQC rating is Inadequate or Requires
    Improvement AND the inspection was in the last 180 days.
    """
    try:
        rating = (lead.get("cqc_rating") or "").strip().lower()
        if rating not in ("inadequate", "requires improvement"):
            return False
        raw = (
            lead.get("cqc_last_inspection_date")
            or lead.get("last_inspection_date")
        )
        if not raw:
            return False
        inspection_date = datetime.fromisoformat(str(raw))
        cutoff = datetime.utcnow() - timedelta(days=180)
        return inspection_date >= cutoff
    except Exception as exc:
        log.warning(
            "care_intelligence._recent_negative_inspection "
            "error: %s", exc
        )
        return False


def _count_missing_enrichment(lead: dict) -> int:
    """
    Count how many key enrichment fields are missing.
    Fields checked: email, phone, website, decision_maker_name.
    """
    missing = 0
    for field in ("email", "phone", "website", "decision_maker_name"):
        val = lead.get(field)
        if not val or str(val).strip() == "":
            missing += 1
    return missing


def _build_ai_prompt(lead: dict) -> str:
    """Build the AI scoring prompt for a care home lead."""
    # BUG-CI-02: was lead.get("name") — column is business_name
    name    = lead.get("business_name", "Unknown")
    rating  = lead.get("cqc_rating", "Unknown")
    city    = lead.get("city") or lead.get("location", "Unknown")
    changed = _director_changed_recently(lead)

    missing = []
    if not lead.get("email"):
        missing.append("email")
    if not lead.get("phone"):
        missing.append("phone")
    if not lead.get("website"):
        missing.append("website")
    missing_str = ", ".join(missing) if missing else "none"

    return (
        "You are evaluating a UK care home as a sales opportunity.\n"
        "Care Home: " + name + "\n"
        "CQC Rating: " + str(rating) + "\n"
        "Director Changed: " + str(changed) + "\n"
        "Missing Contact: " + missing_str + "\n"
        "Location: " + str(city) + "\n"
        "Score 0-100 explain in one sentence.\n"
        'Return JSON: {"score": int, "reason": str}'
    )


def _get_urgency_band(urgency_score: int) -> str:
    """Map urgency score to named band."""
    if urgency_score >= 70:
        return "high"
    elif urgency_score >= 40:
        return "medium"
    return "low"


def _estimate_revenue(urgency_band: str) -> dict:
    """Return revenue estimate range for urgency band."""
    return REVENUE_ESTIMATES.get(
        urgency_band, REVENUE_ESTIMATES["low"]
    )


# ─────────────────────────────────────────────────────────────
# INTELLIGENCE PERSISTENCE
# ─────────────────────────────────────────────────────────────

def _save_opportunity(
    lead_id: int,
    opp_score: int,
    urgency_score: int,
    ai_score: Optional[int],
    ai_reason: Optional[str],
    urgency_band: str,
    revenue_min: int,
    revenue_max: int,
) -> bool:
    """
    Persist scored opportunity to care_home_opportunities table.
    Uses INSERT OR REPLACE to handle re-scoring runs.
    Returns True on success, False on failure.
    """
    try:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO care_home_opportunities
                  (lead_id, opportunity_score, urgency_score,
                   ai_score, ai_reason, urgency_band,
                   revenue_estimate_min, revenue_estimate_max,
                   scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    opp_score,
                    urgency_score,
                    ai_score,
                    ai_reason,
                    urgency_band,
                    revenue_min,
                    revenue_max,
                    datetime.utcnow().isoformat(),
                )
            )
            conn.commit()
            log.info(
                "care_intelligence: saved opportunity for lead %s",
                lead_id
            )
            return True
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "care_intelligence._save_opportunity error: %s", exc
        )
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC SCORING FUNCTIONS
# ─────────────────────────────────────────────────────────────

def calculate_opportunity_score(lead: dict) -> int:
    """
    Calculate opportunity score 0-100 for a care home lead.

    Scoring:
      CQC Inadequate           +40
      CQC Requires Improvement +30
      CQC Good                 +10
      Director changed 90d     +20
      Operator group member    +15
      No website               +10
      No email                 +10
      Phone verified           +5

    Returns int score clamped to 0-100.
    """
    score  = 0
    rating = (lead.get("cqc_rating") or "").strip().lower()

    if rating == "inadequate":
        score += OPP_SCORE["cqc_inadequate"]
        log.debug(
            "care opp: +%d CQC inadequate",
            OPP_SCORE["cqc_inadequate"]
        )
    elif rating == "requires improvement":
        score += OPP_SCORE["cqc_requires_improvement"]
        log.debug(
            "care opp: +%d CQC requires improvement",
            OPP_SCORE["cqc_requires_improvement"]
        )
    elif rating == "good":
        score += OPP_SCORE["cqc_good"]
        log.debug(
            "care opp: +%d CQC good",
            OPP_SCORE["cqc_good"]
        )

    if _director_changed_recently(lead):
        score += OPP_SCORE["director_changed_90d"]
        log.debug(
            "care opp: +%d director changed recently",
            OPP_SCORE["director_changed_90d"]
        )

    if _is_in_operator_group(lead):
        score += OPP_SCORE["operator_group_member"]
        log.debug(
            "care opp: +%d operator group member",
            OPP_SCORE["operator_group_member"]
        )

    if not lead.get("website"):
        score += OPP_SCORE["no_website"]
        log.debug(
            "care opp: +%d no website",
            OPP_SCORE["no_website"]
        )

    if not lead.get("email"):
        score += OPP_SCORE["no_email"]
        log.debug(
            "care opp: +%d no email",
            OPP_SCORE["no_email"]
        )

    phone = lead.get("phone") or ""
    if phone.strip():
        score += OPP_SCORE["phone_verified"]
        log.debug(
            "care opp: +%d phone verified",
            OPP_SCORE["phone_verified"]
        )

    final = min(max(score, 0), 100)
    log.info(
        "care opportunity_score for lead %s = %d",
        lead.get("id"), final
    )
    return final


def calculate_urgency_score(lead: dict) -> int:
    """
    Calculate urgency score 0-100 for a care home lead.

    Scoring:
      CQC inspection overdue        +25
      Recent negative inspection    +25
      Director change detected      +20
      Multiple missing enrichment   +15
      No current supplier signals   +15

    Returns int score clamped to 0-100.
    """
    score = 0

    if _cqc_inspection_overdue(lead):
        score += URG_SCORE["cqc_inspection_overdue"]
        log.debug(
            "care urgency: +%d inspection overdue",
            URG_SCORE["cqc_inspection_overdue"]
        )

    if _recent_negative_inspection(lead):
        score += URG_SCORE["recent_negative_inspection"]
        log.debug(
            "care urgency: +%d recent negative inspection",
            URG_SCORE["recent_negative_inspection"]
        )

    if _director_changed_recently(lead):
        score += URG_SCORE["director_change_detected"]
        log.debug(
            "care urgency: +%d director change",
            URG_SCORE["director_change_detected"]
        )

    if _count_missing_enrichment(lead) >= 2:
        score += URG_SCORE["multiple_missing_enrichment"]
        log.debug(
            "care urgency: +%d multiple missing enrichment",
            URG_SCORE["multiple_missing_enrichment"]
        )

    if not lead.get("supplier") and not lead.get("current_supplier"):
        score += URG_SCORE["no_current_supplier_signals"]
        log.debug(
            "care urgency: +%d no supplier signals",
            URG_SCORE["no_current_supplier_signals"]
        )

    final = min(max(score, 0), 100)
    log.info(
        "care urgency_score for lead %s = %d",
        lead.get("id"), final
    )
    return final


def _get_ai_score(lead: dict) -> Optional[dict]:
    """
    Attempt to get an AI score for the lead.
    call_ai_json() handles circuit breaker and caching internally.
    Returns dict {"score": int, "reason": str} or None.
    """
    lead_id = lead.get("id")

    try:
        prompt = _build_ai_prompt(lead)
        result = call_ai_json(
            prompt,
            context="care_intelligence_lead_" + str(lead_id)
        )

        if result is None:
            return None

        if "score" not in result:
            log.warning(
                "care_intelligence: AI response missing score key "
                "for lead %s", lead_id
            )
            return None

        log.info(
            "care_intelligence: AI score for lead %s = %s",
            lead_id, result.get("score")
        )
        return result

    except Exception as exc:
        log.error(
            "care_intelligence._get_ai_score error lead %s: %s",
            lead_id, exc
        )
        return None


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def refresh_intelligence(lead_id: int) -> bool:
    """
    Re-score a single care home lead and persist the result.

    Runs:
      calculate_opportunity_score()
      calculate_urgency_score()
      Optional AI scoring
      Saves to care_home_opportunities

    Returns True on success, False on failure.
    """
    log.info(
        "care_intelligence.refresh_intelligence: lead_id=%s", lead_id
    )

    lead = _get_lead(lead_id)
    if lead is None:
        log.error(
            "care_intelligence.refresh_intelligence: "
            "lead %s not found", lead_id
        )
        return False

    opp_score     = calculate_opportunity_score(lead)
    urgency_score = calculate_urgency_score(lead)
    urgency_band  = _get_urgency_band(urgency_score)
    revenue       = _estimate_revenue(urgency_band)

    ai_result = _get_ai_score(lead)
    ai_score  = ai_result.get("score")  if ai_result else None
    ai_reason = ai_result.get("reason") if ai_result else None

    return _save_opportunity(
        lead_id=lead_id,
        opp_score=opp_score,
        urgency_score=urgency_score,
        ai_score=ai_score,
        ai_reason=ai_reason,
        urgency_band=urgency_band,
        revenue_min=revenue["min"],
        revenue_max=revenue["max"],
    )


def get_top_opportunities(
    limit: int = 25,
    filters: Optional[dict] = None,
) -> list:
    """
    Return top care home opportunities ranked by
    opportunity_score DESC.

    Optional filters dict supports:
      urgency_band: "high" | "medium" | "low"
      min_score:    int
      cqc_rating:   str

    Returns list of dicts combining lead + opportunity data.
    Returns empty list on failure.
    """
    filters = filters or {}

    try:
        conn = db.get_connection()
        try:
            where_clauses = []
            params        = []

            urgency_band = filters.get("urgency_band")
            if urgency_band:
                where_clauses.append("cho.urgency_band = ?")
                params.append(urgency_band)

            min_score = filters.get("min_score")
            if min_score is not None:
                where_clauses.append(
                    "cho.opportunity_score >= ?"
                )
                params.append(int(min_score))

            cqc_rating = filters.get("cqc_rating")
            if cqc_rating:
                where_clauses.append(
                    "LOWER(l.cqc_rating) = LOWER(?)"
                )
                params.append(cqc_rating)

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            params.append(limit)

            # BUG-CI-01: was l.name — column is business_name
            cursor = conn.execute(
                """
                SELECT
                    l.id,
                    l.business_name,
                    l.city,
                    l.cqc_rating,
                    l.email,
                    l.phone,
                    l.website,
                    cho.opportunity_score,
                    cho.urgency_score,
                    cho.urgency_band,
                    cho.ai_score,
                    cho.ai_reason,
                    cho.revenue_estimate_min,
                    cho.revenue_estimate_max,
                    cho.scored_at
                FROM leads l
                JOIN care_home_opportunities cho
                  ON cho.lead_id = l.id
                """
                + where_sql +
                """
                ORDER BY cho.opportunity_score DESC
                LIMIT ?
                """,
                params
            )
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
            log.info(
                "care_intelligence.get_top_opportunities: "
                "returned %d rows", len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "care_intelligence.get_top_opportunities error: %s", exc
        )
        return []


def get_intelligence_summary() -> dict:
    """
    Return aggregate statistics for the care intelligence
    dashboard.

    Returns dict with:
      total_scored, high_urgency, medium_urgency, low_urgency,
      avg_opp_score, avg_urgency_score,
      inadequate_count, requires_improvement_count,
      director_changes_90d, no_website_count

    Returns safe defaults on failure.
    """
    defaults = {
        "total_scored":                    0,
        "high_urgency":                    0,
        "medium_urgency":                  0,
        "low_urgency":                     0,
        "avg_opp_score":                   0.0,
        "avg_urgency_score":               0.0,
        "inadequate_count":                0,
        "requires_improvement_count":      0,
        "director_changes_90d":            0,
        "no_website_count":                0,
    }

    try:
        conn = db.get_connection()
        try:
            # Band counts and averages
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN urgency_band = 'high'
                             THEN 1 ELSE 0 END) AS high,
                    SUM(CASE WHEN urgency_band = 'medium'
                             THEN 1 ELSE 0 END) AS medium,
                    SUM(CASE WHEN urgency_band = 'low'
                             THEN 1 ELSE 0 END) AS low,
                    AVG(opportunity_score) AS avg_opp,
                    AVG(urgency_score)     AS avg_urg
                FROM care_home_opportunities
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_scored"]      = row[0] or 0
                defaults["high_urgency"]      = row[1] or 0
                defaults["medium_urgency"]    = row[2] or 0
                defaults["low_urgency"]       = row[3] or 0
                defaults["avg_opp_score"]     = round(row[4] or 0.0, 1)
                defaults["avg_urgency_score"] = round(row[5] or 0.0, 1)

            # CQC rating breakdown
            # Uses industry column — matches v9.9+ schema
            cursor2 = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN LOWER(cqc_rating) = 'inadequate'
                             THEN 1 ELSE 0 END) AS inadeq,
                    SUM(CASE WHEN LOWER(cqc_rating) =
                             'requires improvement'
                             THEN 1 ELSE 0 END) AS req_imp
                FROM leads
                WHERE LOWER(industry) LIKE '%care%'
                """
            )
            row2 = cursor2.fetchone()
            if row2:
                defaults["inadequate_count"]           = row2[0] or 0
                defaults["requires_improvement_count"] = row2[1] or 0

            # Director changes in last 90 days
            cutoff = (
                datetime.utcnow()
                - timedelta(days=DIRECTOR_CHANGE_WINDOW_DAYS)
            ).isoformat()
            cursor3 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%care%'
                AND (
                    director_changed_date >= ?
                    OR director_change_date >= ?
                )
                """,
                (cutoff, cutoff)
            )
            row3 = cursor3.fetchone()
            defaults["director_changes_90d"] = row3[0] if row3 else 0

            # No website count
            cursor4 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%care%'
                AND (website IS NULL OR TRIM(website) = '')
                """
            )
            row4 = cursor4.fetchone()
            defaults["no_website_count"] = row4[0] if row4 else 0

            log.info(
                "care_intelligence.get_intelligence_summary: %s",
                defaults
            )
            return defaults

        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "care_intelligence.get_intelligence_summary error: %s",
            exc
        )
        return defaults


def refresh_all_care_intelligence(batch_size: int = 100) -> dict:
    """
    Re-score all care home leads in the database.

    Safe to call from a background job or Streamlit button.
    All exceptions caught internally.

    Returns summary dict:
      processed, succeeded, failed
    """
    log.info(
        "care_intelligence.refresh_all: starting "
        "batch_size=%d", batch_size
    )
    leads = _get_all_care_leads()

    processed = 0
    succeeded = 0
    failed    = 0

    for lead in leads:
        lead_id = lead.get("id")
        try:
            ok = refresh_intelligence(lead_id)
            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as exc:
            log.error(
                "care_intelligence.refresh_all: "
                "error on lead %s: %s", lead_id, exc
            )
            failed += 1
        processed += 1

    summary = {
        "processed": processed,
        "succeeded": succeeded,
        "failed":    failed,
    }
    log.info(
        "care_intelligence.refresh_all complete: %s", summary
    )
    return summary