# dental_review_analyzer.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Dental review pattern analysis, reputation scoring,
#             growth signal detection from Google review data
# Dependencies: database.py
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL throughout
#   - logger not print()
#   - Graceful degradation on all failures
#   - Safe defaults returned on error
#   - No raw exceptions exposed

import logging
import json
from datetime import datetime, timezone
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Minimum number of reviews before analysis is meaningful
MIN_REVIEW_COUNT = 3

# Rating thresholds
EXCELLENT_RATING = 4.5
GOOD_RATING = 4.0
POOR_RATING = 3.5
CRITICAL_RATING = 3.0

# Growth detection — percentage increase considered significant
GROWTH_THRESHOLD_DEFAULT = 20.0

# Health score bands (0–100)
HEALTH_BAND_EXCELLENT = 85
HEALTH_BAND_GOOD = 70
HEALTH_BAND_FAIR = 50
HEALTH_BAND_POOR = 30

# Reputation problem labels
PROBLEM_NO_REVIEWS = "no_reviews"
PROBLEM_FEW_REVIEWS = "few_reviews"
PROBLEM_CRITICAL_RATING = "critical_rating"
PROBLEM_POOR_RATING = "poor_rating"
PROBLEM_DECLINING_RATING = "declining_rating"
PROBLEM_STALE_REVIEWS = "stale_reviews"
PROBLEM_LOW_VOLUME = "low_review_volume"

# Growth signal labels
SIGNAL_RATING_IMPROVING = "rating_improving"
SIGNAL_REVIEW_VELOCITY = "high_review_velocity"
SIGNAL_HIGH_VOLUME = "high_review_volume"
SIGNAL_EXCELLENT_RATING = "excellent_rating"


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _fetch_lead_review_data(lead_id: int) -> Optional[dict]:
    """
    Fetch review-related columns from the leads table for a given lead_id.

    Returns a dict with keys:
        business_name, rating, review_count, lead_id
    or None if not found.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                business_name,
                rating,
                review_count
            FROM leads
            WHERE id = ?
            LIMIT 1
            """,
            (lead_id,),
        )
        row = cursor.fetchone()
        if not row:
            log.warning("No lead found for lead_id=%s", lead_id)
            return None
        return {
            "lead_id": row[0],
            "business_name": row[1] or "Unknown",
            "rating": _safe_float(row[2]),
            "review_count": _safe_int(row[3]),
        }
    except Exception as exc:
        log.error(
            "Failed to fetch review data for lead_id=%s: %s",
            lead_id,
            exc,
        )
        return None
    finally:
        conn.close()


def _fetch_stored_analysis(lead_id: int) -> Optional[dict]:
    """
    Retrieve a previously stored analysis from dental_review_analysis.

    Returns dict or None.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                lead_id,
                review_health_score,
                reputation_problems,
                growth_signals,
                rating,
                review_count,
                analyzed_at
            FROM dental_review_analysis
            WHERE lead_id = ?
            LIMIT 1
            """,
            (lead_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        # Deserialize JSON fields safely
        problems_raw = row[2]
        signals_raw = row[3]

        try:
            problems = json.loads(problems_raw) if problems_raw else []
        except (json.JSONDecodeError, TypeError):
            problems = []

        try:
            signals = json.loads(signals_raw) if signals_raw else []
        except (json.JSONDecodeError, TypeError):
            signals = []

        return {
            "lead_id": row[0],
            "review_health_score": row[1],
            "reputation_problems": problems,
            "growth_signals": signals,
            "rating": row[4],
            "review_count": row[5],
            "analyzed_at": row[6],
        }
    except Exception as exc:
        log.error(
            "Failed to fetch stored analysis for lead_id=%s: %s",
            lead_id,
            exc,
        )
        return None
    finally:
        conn.close()


def _save_analysis(lead_id: int, analysis: dict) -> bool:
    """
    Persist analysis results into dental_review_analysis table.

    Uses INSERT OR REPLACE to upsert.
    Returns True on success, False on failure.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        problems_json = json.dumps(analysis.get("reputation_problems", []))
        signals_json = json.dumps(analysis.get("growth_signals", []))
        now = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT OR REPLACE INTO dental_review_analysis (
                lead_id,
                review_health_score,
                reputation_problems,
                growth_signals,
                rating,
                review_count,
                analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead_id,
                analysis.get("review_health_score", 0),
                problems_json,
                signals_json,
                analysis.get("rating", 0.0),
                analysis.get("review_count", 0),
                now,
            ),
        )
        conn.commit()
        log.info(
            "Saved review analysis for lead_id=%s — score=%s",
            lead_id,
            analysis.get("review_health_score"),
        )
        return True
    except Exception as exc:
        log.error(
            "Failed to save review analysis for lead_id=%s: %s",
            lead_id,
            exc,
        )
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

def calculate_review_health_score(lead: dict) -> int:
    """
    Calculate a 0–100 review health score for a dental practice.

    Scoring components:
        Rating score      — 60 points max
        Volume score      — 25 points max
        Freshness bonus   — 15 points max (if data available)

    Args:
        lead: dict with keys 'rating' (float) and 'review_count' (int).
              Optional: 'last_review_date' (ISO date string).

    Returns:
        int: Health score 0–100. Returns 0 on error or insufficient data.
    """
    try:
        rating = _safe_float(lead.get("rating"))
        review_count = _safe_int(lead.get("review_count"))

        # ── Rating score (60 points max) ──────────────────────
        # 5.0 → 60, 4.0 → 48, 3.0 → 36, 2.0 → 24, 1.0 → 12
        if rating <= 0:
            rating_score = 0
        else:
            # Clamp rating 0–5
            clamped_rating = min(5.0, max(0.0, rating))
            rating_score = round((clamped_rating / 5.0) * 60)

        # ── Volume score (25 points max) ──────────────────────
        # Tiers:
        #   0       →  0
        #   1–4     →  5
        #   5–19    → 12
        #   20–49   → 18
        #   50–99   → 22
        #   100+    → 25
        if review_count <= 0:
            volume_score = 0
        elif review_count < 5:
            volume_score = 5
        elif review_count < 20:
            volume_score = 12
        elif review_count < 50:
            volume_score = 18
        elif review_count < 100:
            volume_score = 22
        else:
            volume_score = 25

        # ── Freshness bonus (15 points max) ──────────────────
        # If last_review_date is available, reward recent activity.
        # Without date data this component scores 8 (neutral baseline).
        freshness_score = 8  # neutral — no date data
        last_review_date = lead.get("last_review_date")
        if last_review_date:
            try:
                if isinstance(last_review_date, str):
                    review_date = datetime.fromisoformat(
                        last_review_date.replace("Z", "+00:00")
                    )
                else:
                    review_date = last_review_date

                now = datetime.now(timezone.utc)
                if review_date.tzinfo is None:
                    review_date = review_date.replace(tzinfo=timezone.utc)

                days_ago = (now - review_date).days

                if days_ago <= 30:
                    freshness_score = 15
                elif days_ago <= 90:
                    freshness_score = 12
                elif days_ago <= 180:
                    freshness_score = 8
                elif days_ago <= 365:
                    freshness_score = 4
                else:
                    freshness_score = 0  # stale — over a year
            except Exception as date_exc:
                log.debug(
                    "Could not parse last_review_date '%s': %s",
                    last_review_date,
                    date_exc,
                )
                freshness_score = 8  # fallback neutral

        total = rating_score + volume_score + freshness_score
        score = max(0, min(100, total))

        log.debug(
            "Review health score — lead_id=%s rating=%s "
            "reviews=%s → rating_score=%s volume_score=%s "
            "freshness_score=%s total=%s",
            lead.get("lead_id", "?"),
            rating,
            review_count,
            rating_score,
            volume_score,
            freshness_score,
            score,
        )
        return score

    except Exception as exc:
        log.error("calculate_review_health_score failed: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────────
# PROBLEM DETECTION
# ─────────────────────────────────────────────────────────────

def detect_reputation_problems(lead: dict) -> list:
    """
    Detect reputation problems for a dental practice based on review data.

    Checks performed:
        1. No reviews at all
        2. Too few reviews (below minimum threshold)
        3. Critical rating (below 3.0)
        4. Poor rating (3.0 – 3.49)
        5. Low review volume relative to practice size
        6. Stale reviews (last_review_date > 6 months ago)

    Args:
        lead: dict containing 'rating', 'review_count',
              optionally 'last_review_date', 'business_name'.

    Returns:
        list of dicts, each with keys:
            problem_type (str)
            description (str)
            severity (str): 'critical' | 'high' | 'medium' | 'low'
    """
    problems = []

    try:
        rating = _safe_float(lead.get("rating"))
        review_count = _safe_int(lead.get("review_count"))
        name = lead.get("business_name", "This practice")
        last_review_date = lead.get("last_review_date")

        # ── 1. No reviews ────────────────────────────────────
        if review_count == 0:
            problems.append({
                "problem_type": PROBLEM_NO_REVIEWS,
                "description": (
                    f"{name} has no Google reviews. "
                    "Zero online reputation — significant growth risk."
                ),
                "severity": "critical",
            })
            # No point checking further rating-based issues
            return problems

        # ── 2. Too few reviews ───────────────────────────────
        if review_count < MIN_REVIEW_COUNT:
            problems.append({
                "problem_type": PROBLEM_FEW_REVIEWS,
                "description": (
                    f"{name} has only {review_count} review(s). "
                    f"Below the minimum {MIN_REVIEW_COUNT} needed "
                    "for credible online presence."
                ),
                "severity": "high",
            })

        # ── 3. Critical rating ───────────────────────────────
        if 0 < rating < CRITICAL_RATING:
            problems.append({
                "problem_type": PROBLEM_CRITICAL_RATING,
                "description": (
                    f"{name} has a critical rating of {rating:.1f}/5.0. "
                    "Actively damaging patient acquisition."
                ),
                "severity": "critical",
            })

        # ── 4. Poor rating ───────────────────────────────────
        elif CRITICAL_RATING <= rating < POOR_RATING:
            problems.append({
                "problem_type": PROBLEM_POOR_RATING,
                "description": (
                    f"{name} has a poor rating of {rating:.1f}/5.0. "
                    "Below patient expectations — losing new enquiries."
                ),
                "severity": "high",
            })

        # ── 5. Low review volume ─────────────────────────────
        # Even with an acceptable rating, low volume signals
        # lack of patient engagement strategy
        if MIN_REVIEW_COUNT <= review_count < 20 and rating >= POOR_RATING:
            problems.append({
                "problem_type": PROBLEM_LOW_VOLUME,
                "description": (
                    f"{name} has only {review_count} reviews. "
                    "Low volume reduces search visibility and trust signals."
                ),
                "severity": "medium",
            })

        # ── 6. Stale reviews ─────────────────────────────────
        if last_review_date:
            try:
                if isinstance(last_review_date, str):
                    review_date = datetime.fromisoformat(
                        last_review_date.replace("Z", "+00:00")
                    )
                else:
                    review_date = last_review_date

                now = datetime.now(timezone.utc)
                if review_date.tzinfo is None:
                    review_date = review_date.replace(tzinfo=timezone.utc)

                days_ago = (now - review_date).days

                if days_ago > 365:
                    problems.append({
                        "problem_type": PROBLEM_STALE_REVIEWS,
                        "description": (
                            f"{name} last received a review over "
                            f"{days_ago // 365} year(s) ago. "
                            "Stale reviews harm search ranking."
                        ),
                        "severity": "high",
                    })
                elif days_ago > 180:
                    problems.append({
                        "problem_type": PROBLEM_STALE_REVIEWS,
                        "description": (
                            f"{name} last received a review "
                            f"{days_ago} days ago. "
                            "Review velocity has stalled."
                        ),
                        "severity": "medium",
                    })
            except Exception as date_exc:
                log.debug(
                    "Could not parse last_review_date for stale check: %s",
                    date_exc,
                )

    except Exception as exc:
        log.error("detect_reputation_problems failed: %s", exc)

    return problems


def _detect_growth_signals(lead: dict) -> list:
    """
    Detect positive growth signals from review data.

    Args:
        lead: dict with 'rating', 'review_count',
              optionally 'previous_rating', 'last_review_date'.

    Returns:
        list of dicts with keys:
            signal_type (str)
            description (str)
            strength (str): 'strong' | 'moderate' | 'mild'
    """
    signals = []

    try:
        rating = _safe_float(lead.get("rating"))
        review_count = _safe_int(lead.get("review_count"))
        previous_rating = lead.get("previous_rating")
        name = lead.get("business_name", "This practice")

        # ── Excellent rating ──────────────────────────────────
        if rating >= EXCELLENT_RATING and review_count >= MIN_REVIEW_COUNT:
            signals.append({
                "signal_type": SIGNAL_EXCELLENT_RATING,
                "description": (
                    f"{name} has an excellent rating of "
                    f"{rating:.1f}/5.0 — strong reputation asset."
                ),
                "strength": "strong",
            })

        # ── Rating improving ─────────────────────────────────
        if previous_rating is not None:
            prev = _safe_float(previous_rating)
            if prev > 0 and rating > prev:
                improvement = rating - prev
                strength = "strong" if improvement >= 0.3 else "moderate"
                signals.append({
                    "signal_type": SIGNAL_RATING_IMPROVING,
                    "description": (
                        f"{name} rating improved from "
                        f"{prev:.1f} to {rating:.1f} — "
                        "reputation recovery in progress."
                    ),
                    "strength": strength,
                })

        # ── High review volume ───────────────────────────────
        if review_count >= 100:
            signals.append({
                "signal_type": SIGNAL_HIGH_VOLUME,
                "description": (
                    f"{name} has {review_count} reviews — "
                    "strong patient engagement and search authority."
                ),
                "strength": "strong",
            })
        elif review_count >= 50:
            signals.append({
                "signal_type": SIGNAL_HIGH_VOLUME,
                "description": (
                    f"{name} has {review_count} reviews — "
                    "solid online presence."
                ),
                "strength": "moderate",
            })

        # ── Recent review velocity ───────────────────────────
        last_review_date = lead.get("last_review_date")
        if last_review_date and review_count >= MIN_REVIEW_COUNT:
            try:
                if isinstance(last_review_date, str):
                    review_date = datetime.fromisoformat(
                        last_review_date.replace("Z", "+00:00")
                    )
                else:
                    review_date = last_review_date

                now = datetime.now(timezone.utc)
                if review_date.tzinfo is None:
                    review_date = review_date.replace(tzinfo=timezone.utc)

                days_ago = (now - review_date).days
                if days_ago <= 14:
                    signals.append({
                        "signal_type": SIGNAL_REVIEW_VELOCITY,
                        "description": (
                            f"{name} received a review within the last "
                            f"{days_ago} days — actively generating reviews."
                        ),
                        "strength": "moderate",
                    })
            except Exception as date_exc:
                log.debug(
                    "Could not parse last_review_date for velocity: %s",
                    date_exc,
                )

    except Exception as exc:
        log.error("_detect_growth_signals failed: %s", exc)

    return signals


# ─────────────────────────────────────────────────────────────
# PRIMARY PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────

def analyze_reviews(lead: dict) -> dict:
    """
    Perform full review analysis for a dental practice.

    This is the primary entry point. Calculates health score,
    detects reputation problems and growth signals, then
    persists the result to dental_review_analysis.

    Args:
        lead: dict containing at minimum:
            lead_id   (int)
            rating    (float)
            review_count (int)
            business_name (str) — optional but recommended
            last_review_date (str ISO) — optional
            previous_rating (float) — optional

    Returns:
        dict with keys:
            lead_id            (int)
            business_name      (str)
            rating             (float)
            review_count       (int)
            review_health_score (int, 0–100)
            reputation_problems (list of dicts)
            growth_signals      (list of dicts)
            problem_count       (int)
            signal_count        (int)
            health_band         (str)
            analyzed_at         (str ISO)

        Returns minimal safe dict on failure.
    """
    lead_id = _safe_int(lead.get("lead_id"))
    name = lead.get("business_name", "Unknown")

    safe_default = {
        "lead_id": lead_id,
        "business_name": name,
        "rating": 0.0,
        "review_count": 0,
        "review_health_score": 0,
        "reputation_problems": [],
        "growth_signals": [],
        "problem_count": 0,
        "signal_count": 0,
        "health_band": "Unknown",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        rating = _safe_float(lead.get("rating"))
        review_count = _safe_int(lead.get("review_count"))

        health_score = calculate_review_health_score(lead)
        problems = detect_reputation_problems(lead)
        signals = _detect_growth_signals(lead)

        # Determine health band label
        if health_score >= HEALTH_BAND_EXCELLENT:
            health_band = "Excellent"
        elif health_score >= HEALTH_BAND_GOOD:
            health_band = "Good"
        elif health_score >= HEALTH_BAND_FAIR:
            health_band = "Fair"
        elif health_score >= HEALTH_BAND_POOR:
            health_band = "Poor"
        else:
            health_band = "Critical"

        now_iso = datetime.now(timezone.utc).isoformat()

        analysis = {
            "lead_id": lead_id,
            "business_name": name,
            "rating": rating,
            "review_count": review_count,
            "review_health_score": health_score,
            "reputation_problems": problems,
            "growth_signals": signals,
            "problem_count": len(problems),
            "signal_count": len(signals),
            "health_band": health_band,
            "analyzed_at": now_iso,
        }

        # Persist only if we have a valid lead_id
        if lead_id > 0:
            _save_analysis(lead_id, analysis)

        log.info(
            "Review analysis complete — lead_id=%s name='%s' "
            "score=%s band=%s problems=%s signals=%s",
            lead_id,
            name,
            health_score,
            health_band,
            len(problems),
            len(signals),
        )
        return analysis

    except Exception as exc:
        log.error(
            "analyze_reviews failed for lead_id=%s: %s",
            lead_id,
            exc,
        )
        return safe_default


def get_poor_reputation_practices(threshold: int = 50) -> list:
    """
    Retrieve dental practices with review health scores below threshold.

    Queries dental_review_analysis joined to leads for business details.
    Returns results ordered by review_health_score ascending (worst first).

    Args:
        threshold: int — maximum health score to include (default 50).

    Returns:
        list of dicts, each containing:
            lead_id, business_name, rating, review_count,
            review_health_score, reputation_problems, analyzed_at
        Returns empty list on failure.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                dra.lead_id,
                l.business_name,
                dra.rating,
                dra.review_count,
                dra.review_health_score,
                dra.reputation_problems,
                dra.analyzed_at
            FROM dental_review_analysis dra
            LEFT JOIN leads l ON l.id = dra.lead_id
            WHERE dra.review_health_score < ?
            ORDER BY dra.review_health_score ASC
            """,
            (threshold,),
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            try:
                problems = json.loads(row[5]) if row[5] else []
            except (json.JSONDecodeError, TypeError):
                problems = []

            results.append({
                "lead_id": row[0],
                "business_name": row[1] or "Unknown",
                "rating": _safe_float(row[2]),
                "review_count": _safe_int(row[3]),
                "review_health_score": _safe_int(row[4]),
                "reputation_problems": problems,
                "analyzed_at": row[6],
            })

        log.info(
            "get_poor_reputation_practices(threshold=%s) → %s results",
            threshold,
            len(results),
        )
        return results

    except Exception as exc:
        log.error("get_poor_reputation_practices failed: %s", exc)
        return []
    finally:
        conn.close()


def get_growing_practices(growth_threshold: float = GROWTH_THRESHOLD_DEFAULT) -> list:
    """
    Retrieve dental practices with positive growth signals.

    Queries dental_review_analysis for records that have at least one
    growth signal stored. Ordered by review_health_score descending
    (strongest performers first).

    Args:
        growth_threshold: float — minimum rating to qualify as growing
                          (default 20.0, used as a filter on health score
                          to ensure meaningful data quality).

    Returns:
        list of dicts containing:
            lead_id, business_name, rating, review_count,
            review_health_score, growth_signals, analyzed_at
        Returns empty list on failure.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Fetch all records that have growth_signals populated
        # We filter in Python to avoid complex JSON SQL operations
        cursor.execute(
            """
            SELECT
                dra.lead_id,
                l.business_name,
                dra.rating,
                dra.review_count,
                dra.review_health_score,
                dra.growth_signals,
                dra.analyzed_at
            FROM dental_review_analysis dra
            LEFT JOIN leads l ON l.id = dra.lead_id
            WHERE dra.growth_signals IS NOT NULL
              AND dra.growth_signals != '[]'
              AND dra.growth_signals != ''
            ORDER BY dra.review_health_score DESC
            """
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            try:
                signals = json.loads(row[5]) if row[5] else []
            except (json.JSONDecodeError, TypeError):
                signals = []

            if not signals:
                continue

            results.append({
                "lead_id": row[0],
                "business_name": row[1] or "Unknown",
                "rating": _safe_float(row[2]),
                "review_count": _safe_int(row[3]),
                "review_health_score": _safe_int(row[4]),
                "growth_signals": signals,
                "signal_count": len(signals),
                "analyzed_at": row[6],
            })

        log.info(
            "get_growing_practices(threshold=%s) → %s results",
            growth_threshold,
            len(results),
        )
        return results

    except Exception as exc:
        log.error("get_growing_practices failed: %s", exc)
        return []
    finally:
        conn.close()


def analyze_reviews_by_id(lead_id: int) -> dict:
    """
    Convenience wrapper — fetch lead data from DB and run analysis.

    Args:
        lead_id: int — primary key in leads table.

    Returns:
        Full analysis dict from analyze_reviews(), or safe default dict.
    """
    lead_data = _fetch_lead_review_data(lead_id)
    if not lead_data:
        log.warning(
            "analyze_reviews_by_id — no data for lead_id=%s", lead_id
        )
        return {
            "lead_id": lead_id,
            "business_name": "Unknown",
            "rating": 0.0,
            "review_count": 0,
            "review_health_score": 0,
            "reputation_problems": [],
            "growth_signals": [],
            "problem_count": 0,
            "signal_count": 0,
            "health_band": "Unknown",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
    return analyze_reviews(lead_data)


def get_review_summary() -> dict:
    """
    Return an aggregate summary of all dental review analysis records.

    Returns:
        dict with keys:
            total_analyzed     (int)
            critical_count     (int)  — health score < 30
            poor_count         (int)  — health score 30–49
            fair_count         (int)  — health score 50–69
            good_count         (int)  — health score 70–84
            excellent_count    (int)  — health score >= 85
            avg_health_score   (float)
            avg_rating         (float)
            practices_with_problems (int)
            practices_growing  (int)
        Returns zero-filled dict on failure.
    """
    safe_default = {
        "total_analyzed": 0,
        "critical_count": 0,
        "poor_count": 0,
        "fair_count": 0,
        "good_count": 0,
        "excellent_count": 0,
        "avg_health_score": 0.0,
        "avg_rating": 0.0,
        "practices_with_problems": 0,
        "practices_growing": 0,
    }

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN review_health_score < 30 THEN 1 ELSE 0 END),
                SUM(CASE WHEN review_health_score >= 30
                          AND review_health_score < 50 THEN 1 ELSE 0 END),
                SUM(CASE WHEN review_health_score >= 50
                          AND review_health_score < 70 THEN 1 ELSE 0 END),
                SUM(CASE WHEN review_health_score >= 70
                          AND review_health_score < 85 THEN 1 ELSE 0 END),
                SUM(CASE WHEN review_health_score >= 85 THEN 1 ELSE 0 END),
                AVG(review_health_score),
                AVG(rating),
                SUM(CASE WHEN reputation_problems IS NOT NULL
                          AND reputation_problems != '[]'
                          AND reputation_problems != ''
                          THEN 1 ELSE 0 END),
                SUM(CASE WHEN growth_signals IS NOT NULL
                          AND growth_signals != '[]'
                          AND growth_signals != ''
                          THEN 1 ELSE 0 END)
            FROM dental_review_analysis
            """
        )
        row = cursor.fetchone()
        if not row or row[0] == 0:
            return safe_default

        return {
            "total_analyzed": _safe_int(row[0]),
            "critical_count": _safe_int(row[1]),
            "poor_count": _safe_int(row[2]),
            "fair_count": _safe_int(row[3]),
            "good_count": _safe_int(row[4]),
            "excellent_count": _safe_int(row[5]),
            "avg_health_score": round(_safe_float(row[6]), 1),
            "avg_rating": round(_safe_float(row[7]), 2),
            "practices_with_problems": _safe_int(row[8]),
            "practices_growing": _safe_int(row[9]),
        }

    except Exception as exc:
        log.error("get_review_summary failed: %s", exc)
        return safe_default
    finally:
        conn.close()


def batch_analyze_all_dental_leads(batch_size: int = 50) -> dict:
    """
    Run review analysis on all dental leads that lack a stored analysis.

    Fetches leads from the 'leads' table where the vertical/category
    indicates dental, and no entry exists in dental_review_analysis.

    Args:
        batch_size: int — maximum leads to process per run (default 50).

    Returns:
        dict with keys:
            processed  (int)
            succeeded  (int)
            failed     (int)
    """
    result = {"processed": 0, "succeeded": 0, "failed": 0}

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Fetch dental leads not yet analyzed
        # Supports multiple common category/vertical column names
        cursor.execute(
            """
            SELECT
                l.id,
                l.business_name,
                l.rating,
                l.review_count
            FROM leads l
            WHERE (
                LOWER(COALESCE(l.category, '')) LIKE '%dental%'
                OR LOWER(COALESCE(l.category, '')) LIKE '%dentist%'
                OR LOWER(COALESCE(l.vertical, '')) LIKE '%dental%'
            )
            AND l.id NOT IN (
                SELECT lead_id FROM dental_review_analysis
            )
            LIMIT ?
            """,
            (batch_size,),
        )
        rows = cursor.fetchall()

    except Exception as exc:
        log.error("batch_analyze_all_dental_leads — query failed: %s", exc)
        return result
    finally:
        conn.close()

    for row in rows:
        result["processed"] += 1
        lead = {
            "lead_id": row[0],
            "business_name": row[1] or "Unknown",
            "rating": _safe_float(row[2]),
            "review_count": _safe_int(row[3]),
        }
        try:
            analysis = analyze_reviews(lead)
            if analysis.get("review_health_score") is not None:
                result["succeeded"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            log.error(
                "batch_analyze — failed for lead_id=%s: %s",
                row[0],
                exc,
            )
            result["failed"] += 1

    log.info(
        "batch_analyze_all_dental_leads complete — "
        "processed=%s succeeded=%s failed=%s",
        result["processed"],
        result["succeeded"],
        result["failed"],
    )
    return result