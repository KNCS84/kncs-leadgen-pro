# care_acquisition_signals.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Detects acquisition signals for care homes.
#            Signal types: director_change, cqc_rating_change,
#            new_registration, recent_inspection.
#            Sources: CQC API, Companies House API.
# Dependencies: database.py, engine/cqc_client.py,
#               engine/companies_house.py
# Critical rules observed:
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — one failed signal scan does not
#     block others
#   - No exceptions raised to caller

import logging
from datetime import datetime, timedelta
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# SIGNAL TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────

SIGNAL_DIRECTOR_CHANGE    = "director_change"
SIGNAL_CQC_RATING_CHANGE  = "cqc_rating_change"
SIGNAL_NEW_REGISTRATION   = "new_registration"
SIGNAL_RECENT_INSPECTION  = "recent_inspection"

VALID_SIGNAL_TYPES = {
    SIGNAL_DIRECTOR_CHANGE,
    SIGNAL_CQC_RATING_CHANGE,
    SIGNAL_NEW_REGISTRATION,
    SIGNAL_RECENT_INSPECTION,
}

# CQC rating order — used to detect upgrades vs downgrades
CQC_RATING_ORDER = {
    "outstanding": 4,
    "good":        3,
    "requires improvement": 2,
    "inadequate":  1,
    "unknown":     0,
}

# ─────────────────────────────────────────────────────────────
# SAFE EXTERNAL CLIENT IMPORTS
# ─────────────────────────────────────────────────────────────
# Both clients may not be configured in all environments.
# Import failures are caught and logged — signals that require
# these clients will be skipped gracefully.

try:
    from engine import cqc_client as _cqc
    _CQC_AVAILABLE = True
except Exception as _e:
    log.warning("care_acquisition_signals — cqc_client unavailable: %s", _e)
    _cqc = None
    _CQC_AVAILABLE = False

try:
    from engine import companies_house as _ch
    _CH_AVAILABLE = True
except Exception as _e:
    log.warning("care_acquisition_signals — companies_house unavailable: %s", _e)
    _ch = None
    _CH_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _cutoff_date(days: int) -> str:
    """
    Return ISO date string for N days ago.
    Used in WHERE clauses filtering by signal recency.

    Args:
        days: Number of days to look back.

    Returns:
        ISO format date string e.g. '2024-11-01T00:00:00'.
    """
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _fetch_lead(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch core lead data needed for signal scanning.

    Returns:
        dict with lead fields or None if not found.
    """
    try:
        cur = conn.execute(
            """
            SELECT  id            AS lead_id,
                    business_name,
                    company_number,
                    director_name,
                    cqc_location_id,
                    cqc_rating,
                    address
            FROM    leads
            WHERE   id = ?
            LIMIT   1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error("_fetch_lead lead_id=%s error=%s", lead_id, exc)
        return None


def _signal_already_stored(
    lead_id:     int,
    signal_type: str,
    detail:      str,
    conn,
) -> bool:
    """
    Check if an identical signal is already recorded.
    Prevents duplicate signal rows on repeated scans.

    Returns:
        True if duplicate exists, False otherwise.
    """
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM   care_acquisition_signals
            WHERE  lead_id     = ?
              AND  signal_type = ?
              AND  detail      = ?
            """,
            (lead_id, signal_type, detail),
        )
        row = cur.fetchone()
        return (row["cnt"] > 0) if row else False
    except Exception as exc:
        log.error("_signal_already_stored error=%s", exc)
        return False


# ─────────────────────────────────────────────────────────────
# SIGNAL DETECTORS (INTERNAL)
# ─────────────────────────────────────────────────────────────

def _detect_director_change(lead: dict, conn) -> list:
    """
    Detect director changes by querying Companies House
    and comparing against the stored director_name on the lead.

    Returns:
        List of signal dicts detected for this lead.
    """
    signals = []
    company_number = lead.get("company_number") or ""

    if not company_number:
        log.debug(
            "_detect_director_change lead_id=%s — no company_number",
            lead["lead_id"],
        )
        return signals

    if not _CH_AVAILABLE:
        log.debug("_detect_director_change — companies_house client unavailable")
        return signals

    try:
        officers = _ch.get_company_officers(company_number)
        if not officers:
            return signals

        stored_director = (lead.get("director_name") or "").lower().strip()

        for officer in officers:
            name      = (officer.get("name") or "").lower().strip()
            role      = (officer.get("officer_role") or "").lower()
            appointed = officer.get("appointed_on") or ""
            resigned  = officer.get("resigned_on") or ""

            # New director appointed
            if "director" in role and appointed and not resigned:
                if stored_director and name != stored_director:
                    detail = (
                        f"New director appointed: "
                        f"{officer.get('name', 'Unknown')} "
                        f"(appointed {appointed})"
                    )
                    signals.append({
                        "signal_type": SIGNAL_DIRECTOR_CHANGE,
                        "detail":      detail,
                    })

            # Director resigned
            if "director" in role and resigned:
                if stored_director and stored_director in name:
                    detail = (
                        f"Director resigned: "
                        f"{officer.get('name', 'Unknown')} "
                        f"(resigned {resigned})"
                    )
                    signals.append({
                        "signal_type": SIGNAL_DIRECTOR_CHANGE,
                        "detail":      detail,
                    })

    except Exception as exc:
        log.error(
            "_detect_director_change lead_id=%s error=%s",
            lead["lead_id"], exc,
        )

    return signals


def _detect_cqc_rating_change(lead: dict, conn) -> list:
    """
    Detect CQC rating changes by comparing the stored rating
    against the current rating from the CQC API.

    Returns:
        List of signal dicts detected for this lead.
    """
    signals = []
    cqc_id  = lead.get("cqc_location_id") or ""

    if not cqc_id:
        log.debug(
            "_detect_cqc_rating_change lead_id=%s — no cqc_location_id",
            lead["lead_id"],
        )
        return signals

    if not _CQC_AVAILABLE:
        log.debug("_detect_cqc_rating_change — cqc_client unavailable")
        return signals

    try:
        location = _cqc.get_location(cqc_id)
        if not location:
            return signals

        current_rating = (
            location.get("currentRatings", {})
            .get("overall", {})
            .get("rating", "unknown")
            .lower()
        )
        stored_rating = (lead.get("cqc_rating") or "unknown").lower()

        if current_rating == stored_rating:
            return signals

        current_rank = CQC_RATING_ORDER.get(current_rating, 0)
        stored_rank  = CQC_RATING_ORDER.get(stored_rating, 0)

        if current_rank < stored_rank:
            direction = "DOWNGRADE"
        elif current_rank > stored_rank:
            direction = "UPGRADE"
        else:
            direction = "CHANGE"

        detail = (
            f"CQC rating {direction}: "
            f"{stored_rating.title()} → {current_rating.title()}"
        )
        signals.append({
            "signal_type": SIGNAL_CQC_RATING_CHANGE,
            "detail":      detail,
        })

    except Exception as exc:
        log.error(
            "_detect_cqc_rating_change lead_id=%s error=%s",
            lead["lead_id"], exc,
        )

    return signals


def _detect_new_registration(lead: dict, conn) -> list:
    """
    Detect if the care home was recently registered with CQC
    (within the last 180 days).

    Returns:
        List of signal dicts detected for this lead.
    """
    signals  = []
    cqc_id   = lead.get("cqc_location_id") or ""

    if not cqc_id:
        return signals

    if not _CQC_AVAILABLE:
        return signals

    try:
        location = _cqc.get_location(cqc_id)
        if not location:
            return signals

        reg_date_str = location.get("registrationDate") or ""
        if not reg_date_str:
            return signals

        # Parse registration date
        try:
            reg_date = datetime.strptime(reg_date_str[:10], "%Y-%m-%d")
        except ValueError:
            return signals

        age_days = (datetime.utcnow() - reg_date).days

        if age_days <= 180:
            detail = (
                f"New CQC registration: "
                f"{lead.get('business_name', 'Unknown')} "
                f"registered {reg_date_str[:10]} "
                f"({age_days} days ago)"
            )
            signals.append({
                "signal_type": SIGNAL_NEW_REGISTRATION,
                "detail":      detail,
            })

    except Exception as exc:
        log.error(
            "_detect_new_registration lead_id=%s error=%s",
            lead["lead_id"], exc,
        )

    return signals


def _detect_recent_inspection(lead: dict, conn) -> list:
    """
    Detect if the care home had a CQC inspection in the last 90 days.
    Post-inspection is a high-receptivity window for supplier outreach.

    Returns:
        List of signal dicts detected for this lead.
    """
    signals = []
    cqc_id  = lead.get("cqc_location_id") or ""

    if not cqc_id:
        return signals

    if not _CQC_AVAILABLE:
        return signals

    try:
        location = _cqc.get_location(cqc_id)
        if not location:
            return signals

        last_inspection = location.get("lastInspectionDate") or ""
        if not last_inspection:
            return signals

        try:
            insp_date = datetime.strptime(last_inspection[:10], "%Y-%m-%d")
        except ValueError:
            return signals

        age_days = (datetime.utcnow() - insp_date).days

        if age_days <= 90:
            rating = (
                location.get("currentRatings", {})
                .get("overall", {})
                .get("rating", "Unknown")
            )
            detail = (
                f"Recent CQC inspection: "
                f"{last_inspection[:10]} "
                f"({age_days} days ago) — "
                f"Rating: {rating}"
            )
            signals.append({
                "signal_type": SIGNAL_RECENT_INSPECTION,
                "detail":      detail,
            })

    except Exception as exc:
        log.error(
            "_detect_recent_inspection lead_id=%s error=%s",
            lead["lead_id"], exc,
        )

    return signals


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def store_signal(
    lead_id:     int,
    signal_type: str,
    detail:      str,
) -> bool:
    """
    Persist a single acquisition signal to care_acquisition_signals.
    Skips duplicates silently.

    Args:
        lead_id:     Primary key of the lead.
        signal_type: One of the SIGNAL_* constants.
        detail:      Human-readable description of the signal.

    Returns:
        True if stored (or already exists), False on DB error.
    """
    if signal_type not in VALID_SIGNAL_TYPES:
        log.warning(
            "store_signal — invalid signal_type=%s lead_id=%s",
            signal_type, lead_id,
        )
        return False

    conn = db.get_connection()
    try:
        if _signal_already_stored(lead_id, signal_type, detail, conn):
            log.debug(
                "store_signal — duplicate skipped lead_id=%s type=%s",
                lead_id, signal_type,
            )
            return True

        conn.execute(
            """
            INSERT INTO care_acquisition_signals
                (lead_id, signal_type, detail, detected_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                lead_id,
                signal_type,
                detail,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        log.info(
            "store_signal saved lead_id=%s type=%s",
            lead_id, signal_type,
        )
        return True

    except Exception as exc:
        log.error(
            "store_signal lead_id=%s type=%s error=%s",
            lead_id, signal_type, exc,
        )
        return False
    finally:
        conn.close()


def scan_for_acquisition_signals(lead_id: int) -> list:
    """
    Run all four signal detectors for one care home lead.

    Detectors run independently — failure in one does not
    block the others.

    Args:
        lead_id: Primary key of the lead.

    Returns:
        List of signal dicts that were detected AND stored.
        Each dict contains:
            lead_id, signal_type, detail, detected_at
        Empty list if no signals found or on error.
    """
    found = []

    conn = db.get_connection()
    try:
        lead = _fetch_lead(lead_id, conn)
        if not lead:
            log.warning(
                "scan_for_acquisition_signals — lead_id=%s not found",
                lead_id,
            )
            return []
    except Exception as exc:
        log.error(
            "scan_for_acquisition_signals fetch error lead_id=%s: %s",
            lead_id, exc,
        )
        return []
    finally:
        conn.close()

    # Run each detector independently
    detectors = [
        _detect_director_change,
        _detect_cqc_rating_change,
        _detect_new_registration,
        _detect_recent_inspection,
    ]

    conn = db.get_connection()
    try:
        for detector in detectors:
            try:
                raw_signals = detector(lead, conn)
                for sig in raw_signals:
                    stored = store_signal(
                        lead_id     = lead_id,
                        signal_type = sig["signal_type"],
                        detail      = sig["detail"],
                    )
                    if stored:
                        found.append({
                            "lead_id":     lead_id,
                            "signal_type": sig["signal_type"],
                            "detail":      sig["detail"],
                            "detected_at": datetime.utcnow().isoformat(),
                        })
            except Exception as exc:
                log.error(
                    "scan_for_acquisition_signals detector=%s lead_id=%s error=%s",
                    detector.__name__, lead_id, exc,
                )

    finally:
        conn.close()

    log.info(
        "scan_for_acquisition_signals lead_id=%s — %s signals found",
        lead_id, len(found),
    )
    return found


def get_recent_director_changes(days: int = 90) -> list:
    """
    Return all director_change signals detected within the last N days.

    Args:
        days: Look-back window in days. Default 90.

    Returns:
        List of signal dicts ordered by detected_at descending.
        Each dict contains:
            lead_id, business_name, signal_type, detail, detected_at
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  cas.lead_id,
                    l.business_name,
                    cas.signal_type,
                    cas.detail,
                    cas.detected_at
            FROM    care_acquisition_signals cas
            JOIN    leads l ON l.id = cas.lead_id
            WHERE   cas.signal_type = ?
              AND   cas.detected_at >= ?
            ORDER   BY cas.detected_at DESC
            """,
            (SIGNAL_DIRECTOR_CHANGE, _cutoff_date(days)),
        )
        rows = [dict(row) for row in cur.fetchall()]
        log.info(
            "get_recent_director_changes days=%s returned %s rows",
            days, len(rows),
        )
        return rows
    except Exception as exc:
        log.error("get_recent_director_changes error=%s", exc)
        return []
    finally:
        conn.close()


def get_rating_changes(days: int = 90) -> list:
    """
    Return all cqc_rating_change signals within the last N days.

    Args:
        days: Look-back window in days. Default 90.

    Returns:
        List of signal dicts ordered by detected_at descending.
        Each dict contains:
            lead_id, business_name, signal_type, detail, detected_at
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  cas.lead_id,
                    l.business_name,
                    cas.signal_type,
                    cas.detail,
                    cas.detected_at
            FROM    care_acquisition_signals cas
            JOIN    leads l ON l.id = cas.lead_id
            WHERE   cas.signal_type = ?
              AND   cas.detected_at >= ?
            ORDER   BY cas.detected_at DESC
            """,
            (SIGNAL_CQC_RATING_CHANGE, _cutoff_date(days)),
        )
        rows = [dict(row) for row in cur.fetchall()]
        log.info(
            "get_rating_changes days=%s returned %s rows",
            days, len(rows),
        )
        return rows
    except Exception as exc:
        log.error("get_rating_changes error=%s", exc)
        return []
    finally:
        conn.close()


def get_new_registrations(days: int = 180) -> list:
    """
    Return all new_registration signals within the last N days.

    Args:
        days: Look-back window in days. Default 180.

    Returns:
        List of signal dicts ordered by detected_at descending.
        Each dict contains:
            lead_id, business_name, signal_type, detail, detected_at
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  cas.lead_id,
                    l.business_name,
                    cas.signal_type,
                    cas.detail,
                    cas.detected_at
            FROM    care_acquisition_signals cas
            JOIN    leads l ON l.id = cas.lead_id
            WHERE   cas.signal_type = ?
              AND   cas.detected_at >= ?
            ORDER   BY cas.detected_at DESC
            """,
            (SIGNAL_NEW_REGISTRATION, _cutoff_date(days)),
        )
        rows = [dict(row) for row in cur.fetchall()]
        log.info(
            "get_new_registrations days=%s returned %s rows",
            days, len(rows),
        )
        return rows
    except Exception as exc:
        log.error("get_new_registrations error=%s", exc)
        return []
    finally:
        conn.close()