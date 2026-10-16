# dental_intelligence.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Master intelligence engine for Dental vertical
# Dependencies: database.py, engine/ai_adapter.py,
#               engine/cqc_client.py
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL only
#   - logger not print()
#   - ai_is_available() handled inside call_ai_json()
#   - call_ai() handles caching internally
#   - Graceful degradation on all failures
#   - No raw exceptions exposed to caller

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
PRACTICE_NEW_THRESHOLD_MONTHS = 24
REVIEW_GROWTH_THRESHOLD = 0.20  # 20% growth

OPP_SCORE = {
    "google_rating_below_35":    30,
    "google_rating_35_to_40":    15,
    "review_count_below_20":     20,
    "no_online_booking":         25,
    "no_website":                30,
    "cqc_inspection_overdue":    20,
    "practice_opened_24_months": 15,
    "director_changed_90d":      15,
}

GROWTH_SCORE = {
    "review_count_grew_20_pct":  30,
    "new_associate_dentist":     25,
    "second_location_detected":  30,
    "website_recently_launched": 20,
    "social_media_growing":      15,
}

REVENUE_ESTIMATES = {
    "high":   {"min": 1500, "max": 5000},
    "medium": {"min": 750,  "max": 1500},
    "low":    {"min": 300,  "max": 750},
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
                    "dental_intelligence: lead_id %s not found",
                    lead_id
                )
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        finally:
            conn.close()
    except Exception as exc:
        log.error("dental_intelligence._get_lead error: %s", exc)
        return None


def _get_all_dental_leads() -> list:
    """
    Return all leads tagged as dental practices.
    Matches on category field containing 'dental'.
    Returns empty list on failure.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT * FROM leads
                WHERE LOWER(industry) LIKE '%dental%'
                ORDER BY id DESC
                """
            )
            cols = [d[0] for d in cursor.description]
            return [
                dict(zip(cols, row))
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "dental_intelligence._get_all_dental_leads error: %s",
            exc
        )
        return []


def _get_google_rating(lead: dict) -> Optional[float]:
    """
    Extract Google rating as float.
    Returns None if not present or unparseable.
    """
    try:
        raw = lead.get("google_rating") or lead.get("rating")
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _get_review_count(lead: dict) -> int:
    """
    Extract review count as int.
    Returns 0 if not present or unparseable.
    """
    try:
        raw = (
            lead.get("review_count")
            or lead.get("google_review_count")
            or 0
        )
        return int(raw)
    except (TypeError, ValueError):
        return 0


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
            "dental_intelligence._director_changed_recently "
            "parse error: %s", exc
        )
        return False


def _practice_opened_recently(lead: dict) -> bool:
    """
    Return True if practice opened within
    PRACTICE_NEW_THRESHOLD_MONTHS months.
    """
    try:
        raw = (
            lead.get("date_established")
            or lead.get("incorporated_date")
            or lead.get("created_at")
        )
        if not raw:
            return False
        opened_date = datetime.fromisoformat(str(raw))
        cutoff = datetime.utcnow() - timedelta(
            days=PRACTICE_NEW_THRESHOLD_MONTHS * 30
        )
        return opened_date >= cutoff
    except Exception as exc:
        log.warning(
            "dental_intelligence._practice_opened_recently "
            "parse error: %s", exc
        )
        return False


def _cqc_inspection_overdue(lead: dict) -> bool:
    """
    Return True if last CQC inspection is over 12 months
    ago or no inspection date recorded.
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
            "dental_intelligence._cqc_inspection_overdue "
            "parse error: %s", exc
        )
        return True


def _has_online_booking(lead: dict) -> bool:
    """
    Return True if online booking system detected.
    Checks booking_url, online_booking, and website fields.
    """
    if lead.get("booking_url"):
        return True
    flag = lead.get("online_booking")
    if flag is not None:
        return bool(flag)
    # Check website for common booking keywords stored at enrichment
    booking_detected = lead.get("booking_detected")
    if booking_detected is not None:
        return bool(booking_detected)
    return False


def _review_count_grew(lead: dict) -> bool:
    """
    Return True if review count grew by at least
    REVIEW_GROWTH_THRESHOLD in the last 6 months.
    Uses review_count_6m_ago if available.
    """
    try:
        current = _get_review_count(lead)
        previous = lead.get("review_count_6m_ago")
        if previous is None or int(previous) == 0:
            return False
        previous = int(previous)
        growth = (current - previous) / previous
        return growth >= REVIEW_GROWTH_THRESHOLD
    except Exception as exc:
        log.warning(
            "dental_intelligence._review_count_grew error: %s",
            exc
        )
        return False


def _is_in_dental_group(lead: dict) -> bool:
    """
    Return True if this lead is linked to a dental group
    in the dental_groups table.
    """
    try:
        conn = db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT 1 FROM dental_groups
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
            "dental_intelligence._is_in_dental_group error: %s",
            exc
        )
        return False


def _get_urgency_band(score: int) -> str:
    """Map score to urgency band string."""
    if score >= 70:
        return "high"
    elif score >= 40:
        return "medium"
    return "low"


def _estimate_revenue(urgency_band: str) -> dict:
    """Return revenue estimate range for urgency band."""
    return REVENUE_ESTIMATES.get(
        urgency_band, REVENUE_ESTIMATES["low"]
    )


def _build_ai_prompt(lead: dict) -> str:
    """Build the AI scoring prompt for a dental practice lead."""
    name         = lead.get("name", "Unknown")
    city         = lead.get("city") or lead.get("location", "Unknown")
    rating       = _get_google_rating(lead) or "Unknown"
    review_count = _get_review_count(lead)
    website      = lead.get("website") or "None"
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


# ─────────────────────────────────────────────────────────────
# INTELLIGENCE PERSISTENCE
# ─────────────────────────────────────────────────────────────

def _save_opportunity(
    lead_id: int,
    opp_score: int,
    growth_score: int,
    ai_score: Optional[int],
    ai_reason: Optional[str],
    urgency_band: str,
    revenue_min: int,
    revenue_max: int,
) -> bool:
    """
    Persist scored opportunity to dental_opportunities table.
    Uses INSERT OR REPLACE to handle re-scoring runs.
    Returns True on success, False on failure.
    """
    try:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO dental_opportunities
                  (lead_id, opportunity_score, growth_score,
                   ai_score, ai_reason, urgency_band,
                   revenue_estimate_min, revenue_estimate_max,
                   scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    opp_score,
                    growth_score,
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
                "dental_intelligence: saved opportunity "
                "for lead %s", lead_id
            )
            return True
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "dental_intelligence._save_opportunity error: %s", exc
        )
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC SCORING FUNCTIONS
# ─────────────────────────────────────────────────────────────

def calculate_opportunity_score(lead: dict) -> int:
    """
    Calculate opportunity score 0-100 for a dental lead.

    Scoring:
      Google rating below 3.5      +30
      Google rating 3.5 to 4.0     +15
      Review count below 20        +20
      No online booking detected   +25
      No website found             +30
      CQC inspection overdue       +20
      Practice opened 24 months    +15
      Director changed in 90 days  +15

    Returns int score clamped to 0-100.
    """
    score  = 0
    rating = _get_google_rating(lead)

    if rating is not None:
        if rating < 3.5:
            score += OPP_SCORE["google_rating_below_35"]
            log.debug(
                "dental opp: +%d rating below 3.5",
                OPP_SCORE["google_rating_below_35"]
            )
        elif 3.5 <= rating <= 4.0:
            score += OPP_SCORE["google_rating_35_to_40"]
            log.debug(
                "dental opp: +%d rating 3.5-4.0",
                OPP_SCORE["google_rating_35_to_40"]
            )

    review_count = _get_review_count(lead)
    if review_count < 20:
        score += OPP_SCORE["review_count_below_20"]
        log.debug(
            "dental opp: +%d review count below 20",
            OPP_SCORE["review_count_below_20"]
        )

    if not _has_online_booking(lead):
        score += OPP_SCORE["no_online_booking"]
        log.debug(
            "dental opp: +%d no online booking",
            OPP_SCORE["no_online_booking"]
        )

    if not lead.get("website"):
        score += OPP_SCORE["no_website"]
        log.debug(
            "dental opp: +%d no website",
            OPP_SCORE["no_website"]
        )

    if _cqc_inspection_overdue(lead):
        score += OPP_SCORE["cqc_inspection_overdue"]
        log.debug(
            "dental opp: +%d CQC overdue",
            OPP_SCORE["cqc_inspection_overdue"]
        )

    if _practice_opened_recently(lead):
        score += OPP_SCORE["practice_opened_24_months"]
        log.debug(
            "dental opp: +%d practice new",
            OPP_SCORE["practice_opened_24_months"]
        )

    if _director_changed_recently(lead):
        score += OPP_SCORE["director_changed_90d"]
        log.debug(
            "dental opp: +%d director changed",
            OPP_SCORE["director_changed_90d"]
        )

    final = min(max(score, 0), 100)
    log.info(
        "dental opportunity_score for lead %s = %d",
        lead.get("id"), final
    )
    return final


def calculate_growth_score(lead: dict) -> int:
    """
    Calculate growth score 0-100 for a dental lead.

    Scoring:
      Review count grew 20%+ in 6 months  +30
      New associate dentist hired          +25
      Second location detected             +30
      Website recently launched            +20
      Social media activity growing        +15

    Returns int score clamped to 0-100.
    """
    score = 0

    if _review_count_grew(lead):
        score += GROWTH_SCORE["review_count_grew_20_pct"]
        log.debug(
            "dental growth: +%d review growth",
            GROWTH_SCORE["review_count_grew_20_pct"]
        )

    if lead.get("new_associate_dentist"):
        score += GROWTH_SCORE["new_associate_dentist"]
        log.debug(
            "dental growth: +%d new associate",
            GROWTH_SCORE["new_associate_dentist"]
        )

    if lead.get("second_location_detected") or _is_in_dental_group(lead):
        score += GROWTH_SCORE["second_location_detected"]
        log.debug(
            "dental growth: +%d second location",
            GROWTH_SCORE["second_location_detected"]
        )

    if lead.get("website_recently_launched"):
        score += GROWTH_SCORE["website_recently_launched"]
        log.debug(
            "dental growth: +%d website launched",
            GROWTH_SCORE["website_recently_launched"]
        )

    if lead.get("social_media_growing"):
        score += GROWTH_SCORE["social_media_growing"]
        log.debug(
            "dental growth: +%d social media",
            GROWTH_SCORE["social_media_growing"]
        )

    final = min(max(score, 0), 100)
    log.info(
        "dental growth_score for lead %s = %d",
        lead.get("id"), final
    )
    return final


def _get_ai_score(lead: dict) -> Optional[dict]:
    """
    Attempt to get an AI score for the dental lead.
    call_ai_json() handles circuit breaker and caching.
    Returns dict {"score": int, "reason": str} or None.
    """
    lead_id = lead.get("id")

    try:
        prompt = _build_ai_prompt(lead)
        result = call_ai_json(
            prompt,
            context=f"dental_intelligence_lead_{lead_id}"
        )

        if result is None:
            return None

        if "score" not in result:
            log.warning(
                "dental_intelligence: AI response missing "
                "score key for lead %s", lead_id
            )
            return None

        log.info(
            "dental_intelligence: AI score for lead %s = %s",
            lead_id, result.get("score")
        )
        return result

    except Exception as exc:
        log.error(
            "dental_intelligence._get_ai_score error "
            "lead %s: %s", lead_id, exc
        )
        return None


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def refresh_intelligence(lead_id: int) -> bool:
    """
    Re-score a single dental practice lead and persist result.

    Runs:
      calculate_opportunity_score()
      calculate_growth_score()
      Optional AI scoring
      Saves to dental_opportunities

    Returns True on success, False on failure.
    """
    log.info(
        "dental_intelligence.refresh_intelligence: lead_id=%s",
        lead_id
    )

    lead = _get_lead(lead_id)
    if lead is None:
        log.error(
            "dental_intelligence.refresh_intelligence: "
            "lead %s not found", lead_id
        )
        return False

    opp_score    = calculate_opportunity_score(lead)
    growth_score = calculate_growth_score(lead)

    # Use opportunity score to determine urgency band
    urgency_band = _get_urgency_band(opp_score)
    revenue      = _estimate_revenue(urgency_band)

    ai_result = _get_ai_score(lead)
    ai_score  = ai_result.get("score")  if ai_result else None
    ai_reason = ai_result.get("reason") if ai_result else None

    return _save_opportunity(
        lead_id=lead_id,
        opp_score=opp_score,
        growth_score=growth_score,
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
    Return top dental opportunities ranked by
    opportunity_score DESC.

    Optional filters dict supports:
      urgency_band:    "high" | "medium" | "low"
      min_score:       int
      min_growth:      int — minimum growth_score
      has_no_website:  bool

    Returns list of dicts. Returns empty list on failure.
    """
    filters = filters or {}

    try:
        conn = db.get_connection()
        try:
            where_clauses = []
            params        = []

            urgency_band = filters.get("urgency_band")
            if urgency_band:
                where_clauses.append("dop.urgency_band = ?")
                params.append(urgency_band)

            min_score = filters.get("min_score")
            if min_score is not None:
                where_clauses.append(
                    "dop.opportunity_score >= ?"
                )
                params.append(int(min_score))

            min_growth = filters.get("min_growth")
            if min_growth is not None:
                where_clauses.append("dop.growth_score >= ?")
                params.append(int(min_growth))

            has_no_website = filters.get("has_no_website")
            if has_no_website:
                where_clauses.append(
                    "(l.website IS NULL OR TRIM(l.website) = '')"
                )

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            params.append(limit)

            cursor = conn.execute(
                f"""
                SELECT
                    l.id,
                    l.business_name,
                    l.city,
                    l.cqc_rating,
                    l.email,
                    l.phone,
                    l.website,
                    l.rating,
                    l.reviews,
                    dop.opportunity_score,
                    dop.growth_score,
                    dop.urgency_band,
                    dop.ai_score,
                    dop.ai_reason,
                    dop.revenue_estimate_min,
                    dop.revenue_estimate_max,
                    dop.scored_at
                FROM leads l
                JOIN dental_opportunities dop
                  ON dop.lead_id = l.id
                {where_sql}
                ORDER BY dop.opportunity_score DESC
                LIMIT ?
                """,
                params
            )
            cols = [d[0] for d in cursor.description]
            rows = [
                dict(zip(cols, row))
                for row in cursor.fetchall()
            ]
            log.info(
                "dental_intelligence.get_top_opportunities: "
                "returned %d rows", len(rows)
            )
            return rows
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "dental_intelligence.get_top_opportunities error: %s",
            exc
        )
        return []


def get_intelligence_summary() -> dict:
    """
    Return aggregate statistics for the dental intelligence
    dashboard.

    Returns dict with:
      total_scored, high_urgency, medium_urgency, low_urgency,
      avg_opp_score, avg_growth_score,
      no_website_count, low_rating_count,
      no_booking_count, new_practices_count

    Returns safe defaults on failure.
    """
    defaults = {
        "total_scored":      0,
        "high_urgency":      0,
        "medium_urgency":    0,
        "low_urgency":       0,
        "avg_opp_score":     0.0,
        "avg_growth_score":  0.0,
        "no_website_count":  0,
        "low_rating_count":  0,
        "no_booking_count":  0,
        "new_practices_count": 0,
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
                    AVG(growth_score)      AS avg_growth
                FROM dental_opportunities
                """
            )
            row = cursor.fetchone()
            if row:
                defaults["total_scored"]    = row[0] or 0
                defaults["high_urgency"]    = row[1] or 0
                defaults["medium_urgency"]  = row[2] or 0
                defaults["low_urgency"]     = row[3] or 0
                defaults["avg_opp_score"]   = round(row[4] or 0.0, 1)
                defaults["avg_growth_score"]= round(row[5] or 0.0, 1)

            # No website count
            cursor2 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%dental%'
                AND (website IS NULL OR TRIM(website) = '')
                """
            )
            row2 = cursor2.fetchone()
            defaults["no_website_count"] = row2[0] if row2 else 0

            # Low rating count — below 3.5
            cursor3 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%dental%'
                AND google_rating IS NOT NULL
                AND CAST(google_rating AS REAL) < 3.5
                """
            )
            row3 = cursor3.fetchone()
            defaults["low_rating_count"] = row3[0] if row3 else 0

            # No booking count
            cursor4 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%dental%'
                AND (
                    booking_url IS NULL
                    OR TRIM(booking_url) = ''
                )
                AND (
                    online_booking IS NULL
                    OR online_booking = 0
                )
                """
            )
            row4 = cursor4.fetchone()
            defaults["no_booking_count"] = row4[0] if row4 else 0

            # New practices in last 24 months
            cutoff = (
                datetime.utcnow()
                - timedelta(days=PRACTICE_NEW_THRESHOLD_MONTHS * 30)
            ).isoformat()
            cursor5 = conn.execute(
                """
                SELECT COUNT(*) FROM leads
                WHERE LOWER(industry) LIKE '%dental%'
                AND (
                    date_established >= ?
                    OR incorporated_date >= ?
                )
                """,
                (cutoff, cutoff)
            )
            row5 = cursor5.fetchone()
            defaults["new_practices_count"] = row5[0] if row5 else 0

            log.info(
                "dental_intelligence.get_intelligence_summary: %s",
                defaults
            )
            return defaults

        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "dental_intelligence.get_intelligence_summary "
            "error: %s", exc
        )
        return defaults


def refresh_all_dental_intelligence(batch_size: int = 100) -> dict:
    """
    Re-score all dental practice leads in the database.

    Safe to call from a background job or Streamlit button.
    All exceptions caught internally.

    Returns summary dict:
      processed, succeeded, failed
    """
    log.info(
        "dental_intelligence.refresh_all: starting "
        "batch_size=%d", batch_size
    )
    leads = _get_all_dental_leads()

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
                "dental_intelligence.refresh_all: "
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
        "dental_intelligence.refresh_all complete: %s", summary
    )
    return summary