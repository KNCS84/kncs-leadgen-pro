# digital_maturity_scorer.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Combines website health, cyber risk, and tech stack age
#            into a single Digital Maturity Score per lead.
# Dependencies: database.py, engine/msp/website_health_scanner.py,
#               engine/msp/cyber_risk_analyzer.py,
#               engine/msp/tech_stack_detector.py
# Critical rules observed:
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — partial data still scores
#   - INSERT OR REPLACE into digital_maturity_scores
#   - No exceptions raised to caller

import logging
from datetime import datetime
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# SCORE WEIGHTS
# ─────────────────────────────────────────────────────────────
WEIGHT_HEALTH = 0.40
WEIGHT_CYBER  = 0.40
WEIGHT_TECH   = 0.20

# ─────────────────────────────────────────────────────────────
# BAND DEFINITIONS
# ─────────────────────────────────────────────────────────────
MATURITY_BANDS = [
    (0,  30,  "Critical"),
    (31, 50,  "Poor"),
    (51, 70,  "Fair"),
    (71, 85,  "Good"),
    (86, 100, "Excellent"),
]

URGENCY_MAP = {
    "Critical":  "high",
    "Poor":      "high",
    "Fair":      "medium",
    "Good":      "low",
    "Excellent": "low",
}

# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _fetch_health_score(lead_id: int, conn) -> Optional[float]:
    """
    Pull the most recent health_score from website_health_scans.
    Returns None if no row exists or scan errored.
    """
    try:
        cur = conn.execute(
            """
            SELECT health_score
            FROM   website_health_scans
            WHERE  lead_id = ?
            ORDER  BY scanned_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        if row and row["health_score"] is not None:
            return float(row["health_score"])
        return None
    except Exception as exc:
        log.error("_fetch_health_score lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_cyber_score(lead_id: int, conn) -> Optional[float]:
    """
    Pull the most recent risk_score from cyber_risk_profiles.
    Higher risk_score = worse maturity.
    We invert: cyber_maturity = max(0, 100 - risk_score)
    Returns the INVERTED score, or None if no row exists.
    """
    try:
        cur = conn.execute(
            """
            SELECT risk_score
            FROM   cyber_risk_profiles
            WHERE  lead_id = ?
            ORDER  BY analyzed_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        if row and row["risk_score"] is not None:
            raw = float(row["risk_score"])
            return max(0.0, 100.0 - raw)
        return None
    except Exception as exc:
        log.error("_fetch_cyber_score lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_tech_score(lead_id: int, conn) -> Optional[float]:
    """
    Pull the most recent tech_age_score from technology_profiles.
    Higher tech_age_score = older tech = worse maturity.
    We invert: tech_maturity = max(0, 100 - tech_age_score)
    Returns the INVERTED score, or None if no row exists.
    """
    try:
        cur = conn.execute(
            """
            SELECT tech_age_score
            FROM   technology_profiles
            WHERE  lead_id = ?
            ORDER  BY detected_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        if row and row["tech_age_score"] is not None:
            raw = float(row["tech_age_score"])
            return max(0.0, 100.0 - raw)
        return None
    except Exception as exc:
        log.error("_fetch_tech_score lead_id=%s error=%s", lead_id, exc)
        return None


def _compute_weighted_score(
    health: Optional[float],
    cyber: Optional[float],
    tech: Optional[float],
) -> int:
    """
    Weighted combination with graceful degradation.

    If a component is missing we redistribute its weight across
    the components that ARE available so the score remains
    meaningful rather than being artificially depressed.

    Returns an integer 0-100.
    """
    components = []
    weights    = []

    if health is not None:
        components.append(health)
        weights.append(WEIGHT_HEALTH)

    if cyber is not None:
        components.append(cyber)
        weights.append(WEIGHT_CYBER)

    if tech is not None:
        components.append(tech)
        weights.append(WEIGHT_TECH)

    if not components:
        log.warning("_compute_weighted_score: no component data — returning 50")
        return 50   # neutral fallback

    total_weight = sum(weights)
    weighted_sum = sum(c * w for c, w in zip(components, weights))

    # Normalise to the available weight so bands remain valid
    score = weighted_sum / total_weight
    return int(round(min(100.0, max(0.0, score))))


def _save_maturity_record(
    lead_id:        int,
    maturity_score: int,
    health_score:   Optional[float],
    cyber_score:    Optional[float],
    tech_age_score: Optional[float],
    maturity_band:  str,
    urgency_band:   str,
    conn,
) -> bool:
    """
    INSERT OR REPLACE into digital_maturity_scores.
    Returns True on success, False on failure.
    """
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO digital_maturity_scores
                (lead_id, maturity_score, health_score,
                 cyber_score, tech_age_score,
                 maturity_band, urgency_band, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead_id,
                maturity_score,
                health_score,
                cyber_score,
                tech_age_score,
                maturity_band,
                urgency_band,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        log.error("_save_maturity_record lead_id=%s error=%s", lead_id, exc)
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def get_maturity_band(score: int) -> str:
    """
    Map a 0-100 maturity score to its band label.

    Args:
        score: Integer maturity score 0-100.

    Returns:
        Band label string: Critical | Poor | Fair | Good | Excellent
    """
    for low, high, label in MATURITY_BANDS:
        if low <= score <= high:
            return label
    # Clamp edge cases
    return "Critical" if score < 0 else "Excellent"


def get_urgency_band(maturity_band: str) -> str:
    """
    Map a maturity band label to an urgency string.

    Args:
        maturity_band: One of Critical | Poor | Fair | Good | Excellent

    Returns:
        Urgency string: high | medium | low
    """
    return URGENCY_MAP.get(maturity_band, "medium")


def calculate_maturity_score(lead_id: int) -> int:
    """
    Calculate and persist the digital maturity score for one lead.

    Reads from website_health_scans, cyber_risk_profiles,
    technology_profiles. Writes to digital_maturity_scores.

    Degrades gracefully if one or two component scans are missing.

    Args:
        lead_id: Primary key of the lead.

    Returns:
        Integer maturity score 0-100.
        Returns 50 (neutral) if all component data is absent.
    """
    conn = db.get_connection()
    try:
        health = _fetch_health_score(lead_id, conn)
        cyber  = _fetch_cyber_score(lead_id, conn)
        tech   = _fetch_tech_score(lead_id, conn)

        score         = _compute_weighted_score(health, cyber, tech)
        band          = get_maturity_band(score)
        urgency       = get_urgency_band(band)

        saved = _save_maturity_record(
            lead_id        = lead_id,
            maturity_score = score,
            health_score   = health,
            cyber_score    = cyber,
            tech_age_score = tech,
            maturity_band  = band,
            urgency_band   = urgency,
            conn           = conn,
        )

        if saved:
            log.info(
                "Maturity scored lead_id=%s score=%s band=%s urgency=%s",
                lead_id, score, band, urgency,
            )
        else:
            log.warning("Maturity scored but NOT saved lead_id=%s", lead_id)

        return score

    except Exception as exc:
        log.error("calculate_maturity_score lead_id=%s error=%s", lead_id, exc)
        return 50
    finally:
        conn.close()


def refresh_maturity_score(lead_id: int) -> bool:
    """
    Recalculate and overwrite the maturity score for one lead.

    Args:
        lead_id: Primary key of the lead.

    Returns:
        True if the score was calculated and saved successfully.
        False on any failure.
    """
    try:
        conn = db.get_connection()
        try:
            health = _fetch_health_score(lead_id, conn)
            cyber  = _fetch_cyber_score(lead_id, conn)
            tech   = _fetch_tech_score(lead_id, conn)

            score   = _compute_weighted_score(health, cyber, tech)
            band    = get_maturity_band(score)
            urgency = get_urgency_band(band)

            return _save_maturity_record(
                lead_id        = lead_id,
                maturity_score = score,
                health_score   = health,
                cyber_score    = cyber,
                tech_age_score = tech,
                maturity_band  = band,
                urgency_band   = urgency,
                conn           = conn,
            )
        finally:
            conn.close()
    except Exception as exc:
        log.error("refresh_maturity_score lead_id=%s error=%s", lead_id, exc)
        return False


def refresh_all_maturity_scores(batch_size: int = 50) -> dict:
    """
    Recalculate maturity scores for every lead that has at least one
    component scan (health, cyber, or tech) recorded.

    Processes in batches to avoid memory pressure on large datasets.

    Args:
        batch_size: Number of leads to process per database round-trip.

    Returns:
        dict with keys:
            processed  (int)  — leads attempted
            succeeded  (int)  — scores saved
            failed     (int)  — leads that errored
            skipped    (int)  — leads with no scan data at all
    """
    results = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    conn = db.get_connection()
    try:
        # Collect all lead_ids that appear in at least one scan table
        cur = conn.execute(
            """
            SELECT DISTINCT lead_id FROM (
                SELECT lead_id FROM website_health_scans
                UNION
                SELECT lead_id FROM cyber_risk_profiles
                UNION
                SELECT lead_id FROM technology_profiles
            )
            """
        )
        lead_ids = [row["lead_id"] for row in cur.fetchall()]
    except Exception as exc:
        log.error("refresh_all_maturity_scores — fetch lead_ids error=%s", exc)
        conn.close()
        return results
    finally:
        conn.close()

    log.info("refresh_all_maturity_scores — %s leads to process", len(lead_ids))

    for i in range(0, len(lead_ids), batch_size):
        batch = lead_ids[i : i + batch_size]
        for lead_id in batch:
            results["processed"] += 1
            try:
                success = refresh_maturity_score(lead_id)
                if success:
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
            except Exception as exc:
                log.error(
                    "refresh_all_maturity_scores lead_id=%s error=%s",
                    lead_id, exc,
                )
                results["failed"] += 1

        log.info(
            "refresh_all_maturity_scores — batch %s/%s done",
            min(i + batch_size, len(lead_ids)),
            len(lead_ids),
        )

    log.info("refresh_all_maturity_scores complete — %s", results)
    return results


def get_critical_opportunities(limit: int = 50) -> list:
    """
    Return leads with maturity_band in ('Critical', 'Poor'),
    ordered by maturity_score ascending (worst first).

    Args:
        limit: Maximum number of rows to return.

    Returns:
        List of dicts, each containing:
            lead_id, maturity_score, health_score,
            cyber_score, tech_age_score,
            maturity_band, urgency_band, scored_at
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  dms.lead_id,
                    dms.maturity_score,
                    dms.health_score,
                    dms.cyber_score,
                    dms.tech_age_score,
                    dms.maturity_band,
                    dms.urgency_band,
                    dms.scored_at
            FROM    digital_maturity_scores dms
            WHERE   dms.maturity_band IN ('Critical', 'Poor')
            ORDER   BY dms.maturity_score ASC
            LIMIT   ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        log.info("get_critical_opportunities returned %s rows", len(rows))
        return rows
    except Exception as exc:
        log.error("get_critical_opportunities error=%s", exc)
        return []
    finally:
        conn.close()


def get_maturity_summary() -> dict:
    """
    Aggregate counts and average scores across all bands.

    Returns:
        dict with keys:
            total_scored        (int)
            critical_count      (int)
            poor_count          (int)
            fair_count          (int)
            good_count          (int)
            excellent_count     (int)
            high_urgency_count  (int)
            avg_maturity_score  (float)
            avg_health_score    (float)
            avg_cyber_score     (float)
            avg_tech_score      (float)
        All values default to 0 on error.
    """
    empty = {
        "total_scored":       0,
        "critical_count":     0,
        "poor_count":         0,
        "fair_count":         0,
        "good_count":         0,
        "excellent_count":    0,
        "high_urgency_count": 0,
        "avg_maturity_score": 0.0,
        "avg_health_score":   0.0,
        "avg_cyber_score":    0.0,
        "avg_tech_score":     0.0,
    }

    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT
                COUNT(*)                                        AS total_scored,
                SUM(CASE WHEN maturity_band = 'Critical'
                         THEN 1 ELSE 0 END)                    AS critical_count,
                SUM(CASE WHEN maturity_band = 'Poor'
                         THEN 1 ELSE 0 END)                    AS poor_count,
                SUM(CASE WHEN maturity_band = 'Fair'
                         THEN 1 ELSE 0 END)                    AS fair_count,
                SUM(CASE WHEN maturity_band = 'Good'
                         THEN 1 ELSE 0 END)                    AS good_count,
                SUM(CASE WHEN maturity_band = 'Excellent'
                         THEN 1 ELSE 0 END)                    AS excellent_count,
                SUM(CASE WHEN urgency_band = 'high'
                         THEN 1 ELSE 0 END)                    AS high_urgency_count,
                AVG(maturity_score)                            AS avg_maturity_score,
                AVG(health_score)                              AS avg_health_score,
                AVG(cyber_score)                               AS avg_cyber_score,
                AVG(tech_age_score)                            AS avg_tech_score
            FROM digital_maturity_scores
            """
        )
        row = cur.fetchone()
        if not row:
            return empty

        return {
            "total_scored":       int(row["total_scored"]       or 0),
            "critical_count":     int(row["critical_count"]     or 0),
            "poor_count":         int(row["poor_count"]         or 0),
            "fair_count":         int(row["fair_count"]         or 0),
            "good_count":         int(row["good_count"]         or 0),
            "excellent_count":    int(row["excellent_count"]    or 0),
            "high_urgency_count": int(row["high_urgency_count"] or 0),
            "avg_maturity_score": round(float(row["avg_maturity_score"] or 0), 1),
            "avg_health_score":   round(float(row["avg_health_score"]   or 0), 1),
            "avg_cyber_score":    round(float(row["avg_cyber_score"]    or 0), 1),
            "avg_tech_score":     round(float(row["avg_tech_score"]     or 0), 1),
        }

    except Exception as exc:
        log.error("get_maturity_summary error=%s", exc)
        return empty
    finally:
        conn.close()