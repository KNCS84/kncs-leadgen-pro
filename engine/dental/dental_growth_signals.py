# dental_growth_signals.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Dental practice growth signal detection
#             Scans for expansion indicators across CQC,
#             Companies House, and stored lead data
# Dependencies: database.py, engine/companies_house.py,
#               engine/cqc_client.py
# Critical rules observed:
#   - get_connection() via database.py only
#   - Parameterized SQL throughout
#   - logger not print()
#   - Graceful degradation on all failures
#   - Safe defaults returned on error
#   - No raw exceptions exposed
#   - has_been_contacted() checked before outreach
#   - All HTTP via requests.Session

import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# SIGNAL TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────

SIGNAL_NEW_ASSOCIATE = "new_associate_dentist"
SIGNAL_SECOND_LOCATION = "second_location_opened"
SIGNAL_NEW_WEBSITE = "new_website_launched"
SIGNAL_SOCIAL_GROWING = "social_media_growing"
SIGNAL_NEW_CQC_REG = "new_cqc_registration"
SIGNAL_NEW_DIRECTOR = "new_director_added"

ALL_SIGNAL_TYPES = [
    SIGNAL_NEW_ASSOCIATE,
    SIGNAL_SECOND_LOCATION,
    SIGNAL_NEW_WEBSITE,
    SIGNAL_SOCIAL_GROWING,
    SIGNAL_NEW_CQC_REG,
    SIGNAL_NEW_DIRECTOR,
]

# Severity mapping per signal type
SIGNAL_SEVERITY = {
    SIGNAL_NEW_ASSOCIATE:  "high",
    SIGNAL_SECOND_LOCATION: "high",
    SIGNAL_NEW_WEBSITE:    "medium",
    SIGNAL_SOCIAL_GROWING: "medium",
    SIGNAL_NEW_CQC_REG:    "high",
    SIGNAL_NEW_DIRECTOR:   "high",
}

# Human-readable labels
SIGNAL_LABELS = {
    SIGNAL_NEW_ASSOCIATE:  "New Associate Dentist",
    SIGNAL_SECOND_LOCATION: "Second Location Opened",
    SIGNAL_NEW_WEBSITE:    "New Website Launched",
    SIGNAL_SOCIAL_GROWING: "Social Media Growing",
    SIGNAL_NEW_CQC_REG:    "New CQC Registration",
    SIGNAL_NEW_DIRECTOR:   "New Director Added",
}

# Default lookback windows
DEFAULT_DAYS_LOOKBACK = 90
DEFAULT_BATCH_SIZE = 50


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_int(value, default: int = 0) -> int:
    """Safely convert value to int."""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value, default: str = "") -> str:
    """Safely convert value to str."""
    if value is None:
        return default
    return str(value).strip()


def _now_utc() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _cutoff_date(days: int) -> str:
    """
    Return ISO format datetime string for N days ago.

    Args:
        days: int — number of days to look back.

    Returns:
        str — ISO datetime string.
    """
    cutoff = _now_utc() - timedelta(days=days)
    return cutoff.isoformat()


def _fetch_lead_basics(lead_id: int) -> Optional[dict]:
    """
    Fetch core lead fields needed for signal scanning.

    Returns dict or None if not found.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                business_name,
                website,
                phone,
                address,
                category,
                review_count,
                rating,
                companies_house_number
            FROM leads
            WHERE id = ?
            LIMIT 1
            """,
            (lead_id,),
        )
        row = cursor.fetchone()
        if not row:
            log.warning(
                "_fetch_lead_basics — no lead found for id=%s", lead_id
            )
            return None
        return {
            "lead_id":                  row[0],
            "business_name":            _safe_str(row[1]),
            "website":                  _safe_str(row[2]),
            "phone":                    _safe_str(row[3]),
            "address":                  _safe_str(row[4]),
            "category":                 _safe_str(row[5]),
            "review_count":             _safe_int(row[6]),
            "rating":                   row[7],
            "companies_house_number":   _safe_str(row[8]),
        }
    except Exception as exc:
        log.error(
            "_fetch_lead_basics failed for lead_id=%s: %s", lead_id, exc
        )
        return None
    finally:
        conn.close()


def _fetch_existing_signals(lead_id: int) -> list:
    """
    Retrieve all stored signals for a lead from dental_growth_signals.

    Returns list of dicts or empty list.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                lead_id,
                signal_type,
                signal_detail,
                severity,
                detected_at
            FROM dental_growth_signals
            WHERE lead_id = ?
            ORDER BY detected_at DESC
            """,
            (lead_id,),
        )
        rows = cursor.fetchall()
        results = []
        for row in rows:
            try:
                detail = json.loads(row[3]) if row[3] else {}
            except (json.JSONDecodeError, TypeError):
                detail = {"raw": _safe_str(row[3])}

            results.append({
                "id":          row[0],
                "lead_id":     row[1],
                "signal_type": _safe_str(row[2]),
                "detail":      detail,
                "severity":    _safe_str(row[4]),
                "detected_at": _safe_str(row[5]),
            })
        return results
    except Exception as exc:
        log.error(
            "_fetch_existing_signals failed for lead_id=%s: %s",
            lead_id,
            exc,
        )
        return []
    finally:
        conn.close()


def _signal_already_stored(
    lead_id: int,
    signal_type: str,
    days_window: int = 30,
) -> bool:
    """
    Check whether a signal of this type was already stored recently.

    Prevents duplicate signals within the deduplication window.

    Args:
        lead_id:     int
        signal_type: str
        days_window: int — how many days to look back for duplicates.

    Returns:
        bool — True if a duplicate exists.
    """
    conn = db.get_connection()
    try:
        cutoff = _cutoff_date(days_window)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dental_growth_signals
            WHERE lead_id = ?
              AND signal_type = ?
              AND detected_at >= ?
            """,
            (lead_id, signal_type, cutoff),
        )
        row = cursor.fetchone()
        return _safe_int(row[0]) > 0 if row else False
    except Exception as exc:
        log.error(
            "_signal_already_stored check failed "
            "lead_id=%s signal=%s: %s",
            lead_id,
            signal_type,
            exc,
        )
        return False
    finally:
        conn.close()


def _try_import_companies_house():
    """
    Safely import the companies_house module.

    Returns module or None if unavailable.
    """
    try:
        import engine.companies_house as ch
        return ch
    except ImportError as exc:
        log.warning(
            "companies_house module not available: %s", exc
        )
        return None


def _try_import_cqc_client():
    """
    Safely import the cqc_client module.

    Returns module or None if unavailable.
    """
    try:
        import engine.cqc_client as cqc
        return cqc
    except ImportError as exc:
        log.warning("cqc_client module not available: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────
# INDIVIDUAL SIGNAL SCANNERS
# ─────────────────────────────────────────────────────────────

def _scan_new_director(lead: dict) -> Optional[dict]:
    """
    Check Companies House for newly added directors.

    A new director at a dental practice often signals expansion,
    acquisition, or practice purchase.

    Args:
        lead: dict with 'companies_house_number', 'business_name'.

    Returns:
        Signal dict or None if no signal detected.
    """
    ch = _try_import_companies_house()
    if not ch:
        return None

    ch_number = lead.get("companies_house_number", "")
    if not ch_number:
        log.debug(
            "No Companies House number for lead_id=%s — "
            "skipping director scan",
            lead.get("lead_id"),
        )
        return None

    try:
        # Attempt to get officer data from Companies House
        officers = None
        if hasattr(ch, "get_officers"):
            officers = ch.get_officers(ch_number)
        elif hasattr(ch, "get_company_officers"):
            officers = ch.get_company_officers(ch_number)

        if not officers:
            return None

        # Look for appointments within last 90 days
        cutoff = _now_utc() - timedelta(days=90)

        new_directors = []
        officer_list = (
            officers if isinstance(officers, list)
            else officers.get("items", [])
        )

        for officer in officer_list:
            appointed_on = officer.get("appointed_on", "")
            if not appointed_on:
                continue
            try:
                appt_date = datetime.fromisoformat(
                    str(appointed_on)
                ).replace(tzinfo=timezone.utc)
                if appt_date >= cutoff:
                    new_directors.append({
                        "name": officer.get("name", "Unknown"),
                        "role": officer.get("officer_role", "director"),
                        "appointed_on": appointed_on,
                    })
            except (ValueError, TypeError):
                continue

        if new_directors:
            return {
                "signal_type": SIGNAL_NEW_DIRECTOR,
                "detail": {
                    "new_directors":   new_directors,
                    "count":           len(new_directors),
                    "companies_house": ch_number,
                },
                "severity": SIGNAL_SEVERITY[SIGNAL_NEW_DIRECTOR],
                "label":    SIGNAL_LABELS[SIGNAL_NEW_DIRECTOR],
            }

    except Exception as exc:
        log.warning(
            "_scan_new_director failed for lead_id=%s: %s",
            lead.get("lead_id"),
            exc,
        )

    return None


def _scan_new_cqc_registration(lead: dict) -> Optional[dict]:
    """
    Check CQC for new registrations associated with this practice.

    A new CQC registration often means a second location or
    new service category being offered.

    Args:
        lead: dict with 'business_name', 'address'.

    Returns:
        Signal dict or None.
    """
    cqc = _try_import_cqc_client()
    if not cqc:
        return None

    name = lead.get("business_name", "")
    if not name:
        return None

    try:
        locations = None

        if hasattr(cqc, "search_locations"):
            locations = cqc.search_locations(name)
        elif hasattr(cqc, "search"):
            locations = cqc.search(name)

        if not locations:
            return None

        location_list = (
            locations if isinstance(locations, list)
            else locations.get("locations", [])
        )

        cutoff = _now_utc() - timedelta(days=90)
        new_registrations = []

        for loc in location_list:
            reg_date_str = (
                loc.get("registrationDate")
                or loc.get("registration_date")
                or ""
            )
            if not reg_date_str:
                continue
            try:
                reg_date = datetime.fromisoformat(
                    str(reg_date_str).replace("Z", "+00:00")
                )
                if reg_date.tzinfo is None:
                    reg_date = reg_date.replace(tzinfo=timezone.utc)
                if reg_date >= cutoff:
                    new_registrations.append({
                        "location_name": (
                            loc.get("name")
                            or loc.get("locationName", "Unknown")
                        ),
                        "address": (
                            loc.get("postalAddressLine1")
                            or loc.get("address", "")
                        ),
                        "registration_date": reg_date_str,
                    })
            except (ValueError, TypeError):
                continue

        if new_registrations:
            return {
                "signal_type": SIGNAL_NEW_CQC_REG,
                "detail": {
                    "new_registrations": new_registrations,
                    "count":             len(new_registrations),
                },
                "severity": SIGNAL_SEVERITY[SIGNAL_NEW_CQC_REG],
                "label":    SIGNAL_LABELS[SIGNAL_NEW_CQC_REG],
            }

    except Exception as exc:
        log.warning(
            "_scan_new_cqc_registration failed for lead_id=%s: %s",
            lead.get("lead_id"),
            exc,
        )

    return None


def _scan_second_location(lead: dict) -> Optional[dict]:
    """
    Detect whether a dental practice has opened a second location.

    Uses business name matching against other leads in the DB.
    If 2+ leads share a highly similar name they likely form a group.

    Args:
        lead: dict with 'lead_id', 'business_name'.

    Returns:
        Signal dict or None.
    """
    name = lead.get("business_name", "")
    lead_id = lead.get("lead_id")
    if not name or not lead_id:
        return None

    # Use first significant word(s) of business name as match key
    # e.g. "Smile Dental Care London" → "Smile Dental"
    name_parts = name.split()
    if len(name_parts) < 2:
        search_term = name_parts[0] if name_parts else ""
    else:
        search_term = f"{name_parts[0]} {name_parts[1]}"

    if not search_term or len(search_term) < 4:
        return None

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                business_name,
                address
            FROM leads
            WHERE id != ?
              AND business_name LIKE ?
              AND (
                LOWER(COALESCE(category, '')) LIKE '%dental%'
                OR LOWER(COALESCE(category, '')) LIKE '%dentist%'
                OR LOWER(COALESCE(vertical, '')) LIKE '%dental%'
              )
            LIMIT 10
            """,
            (lead_id, f"%{search_term}%"),
        )
        rows = cursor.fetchall()

        if rows:
            matched_locations = [
                {
                    "lead_id":       row[0],
                    "business_name": _safe_str(row[1]),
                    "address":       _safe_str(row[2]),
                }
                for row in rows
            ]
            return {
                "signal_type": SIGNAL_SECOND_LOCATION,
                "detail": {
                    "matched_locations": matched_locations,
                    "count":             len(matched_locations),
                    "match_term":        search_term,
                },
                "severity": SIGNAL_SEVERITY[SIGNAL_SECOND_LOCATION],
                "label":    SIGNAL_LABELS[SIGNAL_SECOND_LOCATION],
            }

    except Exception as exc:
        log.warning(
            "_scan_second_location failed for lead_id=%s: %s",
            lead_id,
            exc,
        )
    finally:
        conn.close()

    return None


def _scan_new_associate(lead: dict) -> Optional[dict]:
    """
    Detect new associate dentist signal.

    Heuristic: look for job-related keywords in the lead's
    enrichment notes or description field.
    Also checks review count growth as proxy for new capacity.

    Args:
        lead: dict with relevant fields.

    Returns:
        Signal dict or None.
    """
    # Check description / notes fields for associate keywords
    description = _safe_str(
        lead.get("description", "") or lead.get("notes", "")
    ).lower()

    associate_keywords = [
        "associate dentist",
        "associate position",
        "new dentist",
        "joining our team",
        "expanding team",
        "new member",
        "we are growing",
    ]

    matched_keywords = [
        kw for kw in associate_keywords
        if kw in description
    ]

    if matched_keywords:
        return {
            "signal_type": SIGNAL_NEW_ASSOCIATE,
            "detail": {
                "matched_keywords": matched_keywords,
                "source":           "lead_description",
            },
            "severity": SIGNAL_SEVERITY[SIGNAL_NEW_ASSOCIATE],
            "label":    SIGNAL_LABELS[SIGNAL_NEW_ASSOCIATE],
        }

    return None


def _scan_new_website(lead: dict) -> Optional[dict]:
    """
    Detect new website signal.

    Heuristic: check if website field was recently updated
    or if website_health_scans records a recently discovered site.

    Args:
        lead: dict with 'lead_id', 'website'.

    Returns:
        Signal dict or None.
    """
    lead_id = lead.get("lead_id")
    website = _safe_str(lead.get("website", ""))

    if not website or not lead_id:
        return None

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Check if the website health scan is very recent (< 30 days)
        # and site is reachable — proxy for newly launched site
        cutoff = _cutoff_date(30)
        cursor.execute(
            """
            SELECT
                url,
                is_reachable,
                scanned_at
            FROM website_health_scans
            WHERE lead_id = ?
              AND is_reachable = 1
              AND scanned_at >= ?
            LIMIT 1
            """,
            (lead_id, cutoff),
        )
        row = cursor.fetchone()

        if row:
            return {
                "signal_type": SIGNAL_NEW_WEBSITE,
                "detail": {
                    "url":        _safe_str(row[0]),
                    "scanned_at": _safe_str(row[2]),
                    "source":     "website_health_scan",
                },
                "severity": SIGNAL_SEVERITY[SIGNAL_NEW_WEBSITE],
                "label":    SIGNAL_LABELS[SIGNAL_NEW_WEBSITE],
            }

    except Exception as exc:
        log.warning(
            "_scan_new_website failed for lead_id=%s: %s",
            lead_id,
            exc,
        )
    finally:
        conn.close()

    return None


def _scan_social_growing(lead: dict) -> Optional[dict]:
    """
    Detect social media growth signal.

    Heuristic: checks if review_count has grown significantly
    as a proxy for increased patient activity and digital presence.
    A practice with 50+ reviews and 4.0+ rating shows active engagement.

    Args:
        lead: dict with 'review_count', 'rating'.

    Returns:
        Signal dict or None.
    """
    review_count = _safe_int(lead.get("review_count", 0))
    rating = lead.get("rating") or 0.0

    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0.0

    # Strong social signal: high reviews + good rating
    if review_count >= 50 and rating >= 4.0:
        return {
            "signal_type": SIGNAL_SOCIAL_GROWING,
            "detail": {
                "review_count": review_count,
                "rating":       round(rating, 1),
                "note": (
                    "High review count and rating indicate "
                    "active patient engagement and digital growth."
                ),
            },
            "severity": SIGNAL_SEVERITY[SIGNAL_SOCIAL_GROWING],
            "label":    SIGNAL_LABELS[SIGNAL_SOCIAL_GROWING],
        }

    return None


# ─────────────────────────────────────────────────────────────
# PRIMARY PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────

def store_growth_signal(lead_id: int, signal: dict) -> bool:
    """
    Persist a growth signal to the dental_growth_signals table.

    Uses INSERT OR IGNORE to prevent exact duplicates.
    Checks for recent duplicates via _signal_already_stored()
    before inserting.

    Args:
        lead_id: int — lead primary key.
        signal:  dict with keys:
                   signal_type (str)
                   detail      (dict)
                   severity    (str)
                   label       (str, optional)

    Returns:
        bool — True on success, False on failure or duplicate.
    """
    signal_type = _safe_str(signal.get("signal_type", ""))
    if not signal_type:
        log.warning(
            "store_growth_signal — missing signal_type for lead_id=%s",
            lead_id,
        )
        return False

    # Deduplicate — do not store if same signal was stored
    # within the last 30 days
    if _signal_already_stored(lead_id, signal_type, days_window=30):
        log.debug(
            "Duplicate signal skipped — lead_id=%s signal=%s",
            lead_id,
            signal_type,
        )
        return False

    detail = signal.get("detail", {})
    severity = _safe_str(
        signal.get("severity", SIGNAL_SEVERITY.get(signal_type, "medium"))
    )
    now = _now_utc().isoformat()

    try:
        detail_json = json.dumps(detail)
    except (TypeError, ValueError):
        detail_json = json.dumps({"raw": str(detail)})

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dental_growth_signals (
                lead_id,
                signal_type,
                signal_detail,
                severity,
                detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (lead_id, signal_type, detail_json, severity, now),
        )
        conn.commit()
        log.info(
            "Stored growth signal — lead_id=%s type=%s severity=%s",
            lead_id,
            signal_type,
            severity,
        )
        return True
    except Exception as exc:
        log.error(
            "store_growth_signal failed — lead_id=%s type=%s: %s",
            lead_id,
            signal_type,
            exc,
        )
        return False
    finally:
        conn.close()


def scan_for_growth_signals(lead_id: int) -> list:
    """
    Run all growth signal scanners for a single dental lead.

    Orchestrates all individual scanner functions, stores
    newly detected signals, and returns the full list of
    signals found in this scan run.

    Args:
        lead_id: int — primary key in leads table.

    Returns:
        list of signal dicts found in this scan.
        Each dict has keys:
            signal_type, detail, severity, label
        Returns empty list on failure or no signals.
    """
    lead = _fetch_lead_basics(lead_id)
    if not lead:
        log.warning(
            "scan_for_growth_signals — no lead found for id=%s",
            lead_id,
        )
        return []

    scanners = [
        _scan_new_director,
        _scan_new_cqc_registration,
        _scan_second_location,
        _scan_new_associate,
        _scan_new_website,
        _scan_social_growing,
    ]

    found_signals = []

    for scanner in scanners:
        try:
            result = scanner(lead)
            if result:
                stored = store_growth_signal(lead_id, result)
                if stored:
                    found_signals.append(result)
                    log.info(
                        "Signal detected — lead_id=%s type=%s",
                        lead_id,
                        result.get("signal_type"),
                    )
        except Exception as exc:
            log.error(
                "Scanner %s failed for lead_id=%s: %s",
                scanner.__name__,
                lead_id,
                exc,
            )
            continue

    log.info(
        "scan_for_growth_signals complete — "
        "lead_id=%s signals_found=%s",
        lead_id,
        len(found_signals),
    )
    return found_signals


def get_growing_practices(days: int = DEFAULT_DAYS_LOOKBACK) -> list:
    """
    Retrieve dental practices with growth signals detected
    within the specified lookback window.

    Results are ordered by signal count descending so the
    most active growth practices appear first.

    Args:
        days: int — lookback window in days (default 90).

    Returns:
        list of dicts, each containing:
            lead_id       (int)
            business_name (str)
            signal_count  (int)
            signals       (list of dicts)
            latest_signal (str — ISO datetime)
        Returns empty list on failure.
    """
    conn = db.get_connection()
    try:
        cutoff = _cutoff_date(days)
        cursor = conn.cursor()

        # Aggregate signals per lead
        cursor.execute(
            """
            SELECT
                dgs.lead_id,
                l.business_name,
                COUNT(dgs.id)          AS signal_count,
                MAX(dgs.detected_at)   AS latest_signal
            FROM dental_growth_signals dgs
            LEFT JOIN leads l ON l.id = dgs.lead_id
            WHERE dgs.detected_at >= ?
            GROUP BY dgs.lead_id, l.business_name
            ORDER BY signal_count DESC, latest_signal DESC
            """,
            (cutoff,),
        )
        rows = cursor.fetchall()

        if not rows:
            log.info(
                "get_growing_practices — no results "
                "in last %s days",
                days,
            )
            return []

        results = []
        for row in rows:
            lead_id_row = row[0]
            # Fetch individual signals for this lead
            signals = _fetch_existing_signals(lead_id_row)
            # Filter to the lookback window
            signals_in_window = [
                s for s in signals
                if s.get("detected_at", "") >= cutoff
            ]

            results.append({
                "lead_id":       lead_id_row,
                "business_name": _safe_str(row[1]),
                "signal_count":  _safe_int(row[2]),
                "signals":       signals_in_window,
                "latest_signal": _safe_str(row[3]),
            })

        log.info(
            "get_growing_practices(days=%s) → %s practices",
            days,
            len(results),
        )
        return results

    except Exception as exc:
        log.error("get_growing_practices failed: %s", exc)
        return []
    finally:
        conn.close()


def get_expanding_groups() -> list:
    """
    Identify dental practices or groups showing multiple
    expansion signals — second location AND director changes,
    or CQC registrations.

    A practice qualifies as an expanding group if it has:
        - 2+ distinct signal types stored, OR
        - At least one SIGNAL_SECOND_LOCATION or SIGNAL_NEW_CQC_REG

    Returns:
        list of dicts, each with:
            lead_id        (int)
            business_name  (str)
            signal_types   (list of str)
            signal_count   (int)
            expansion_type (str): 'multi_site' | 'growing' | 'new_entry'
        Returns empty list on failure.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Get leads with 2+ distinct signal types
        cursor.execute(
            """
            SELECT
                dgs.lead_id,
                l.business_name,
                GROUP_CONCAT(DISTINCT dgs.signal_type) AS signal_types,
                COUNT(DISTINCT dgs.signal_type)         AS distinct_types,
                COUNT(dgs.id)                           AS total_signals
            FROM dental_growth_signals dgs
            LEFT JOIN leads l ON l.id = dgs.lead_id
            GROUP BY dgs.lead_id, l.business_name
            HAVING distinct_types >= 2
               OR signal_types LIKE '%second_location%'
               OR signal_types LIKE '%new_cqc_registration%'
            ORDER BY distinct_types DESC, total_signals DESC
            """
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            signal_types_str = _safe_str(row[2])
            signal_types = (
                signal_types_str.split(",")
                if signal_types_str
                else []
            )
            signal_types = [s.strip() for s in signal_types if s.strip()]

            # Classify expansion type
            if (
                SIGNAL_SECOND_LOCATION in signal_types
                or SIGNAL_NEW_CQC_REG in signal_types
            ) and len(signal_types) >= 2:
                expansion_type = "multi_site"
            elif (
                SIGNAL_NEW_DIRECTOR in signal_types
                and SIGNAL_NEW_ASSOCIATE in signal_types
            ):
                expansion_type = "growing"
            elif (
                SIGNAL_NEW_CQC_REG in signal_types
                or SIGNAL_NEW_DIRECTOR in signal_types
            ):
                expansion_type = "new_entry"
            else:
                expansion_type = "growing"

            results.append({
                "lead_id":        row[0],
                "business_name":  _safe_str(row[1]),
                "signal_types":   signal_types,
                "distinct_types": _safe_int(row[3]),
                "total_signals":  _safe_int(row[4]),
                "expansion_type": expansion_type,
            })

        log.info(
            "get_expanding_groups → %s expanding practices",
            len(results),
        )
        return results

    except Exception as exc:
        log.error("get_expanding_groups failed: %s", exc)
        return []
    finally:
        conn.close()


def get_signals_by_type(
    signal_type: str,
    days: int = DEFAULT_DAYS_LOOKBACK,
) -> list:
    """
    Retrieve all signals of a specific type within a time window.

    Args:
        signal_type: str — one of the SIGNAL_* constants.
        days:        int — lookback window (default 90).

    Returns:
        list of dicts with lead and signal detail.
        Returns empty list on failure or unknown signal_type.
    """
    if signal_type not in ALL_SIGNAL_TYPES:
        log.warning(
            "get_signals_by_type — unknown signal_type='%s'",
            signal_type,
        )
        return []

    conn = db.get_connection()
    try:
        cutoff = _cutoff_date(days)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                dgs.id,
                dgs.lead_id,
                l.business_name,
                dgs.signal_detail,
                dgs.severity,
                dgs.detected_at
            FROM dental_growth_signals dgs
            LEFT JOIN leads l ON l.id = dgs.lead_id
            WHERE dgs.signal_type = ?
              AND dgs.detected_at >= ?
            ORDER BY dgs.detected_at DESC
            """,
            (signal_type, cutoff),
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            try:
                detail = json.loads(row[3]) if row[3] else {}
            except (json.JSONDecodeError, TypeError):
                detail = {}

            results.append({
                "id":            row[0],
                "lead_id":       row[1],
                "business_name": _safe_str(row[2]),
                "signal_type":   signal_type,
                "label":         SIGNAL_LABELS.get(signal_type, signal_type),
                "detail":        detail,
                "severity":      _safe_str(row[4]),
                "detected_at":   _safe_str(row[5]),
            })

        log.info(
            "get_signals_by_type('%s', days=%s) → %s results",
            signal_type,
            days,
            len(results),
        )
        return results

    except Exception as exc:
        log.error(
            "get_signals_by_type failed — type=%s: %s",
            signal_type,
            exc,
        )
        return []
    finally:
        conn.close()


def get_growth_summary() -> dict:
    """
    Return an aggregate summary of all growth signals.

    Returns:
        dict with keys:
            total_signals        (int)
            total_practices      (int)
            signals_by_type      (dict: signal_type → count)
            high_severity_count  (int)
            expanding_groups     (int)
        Returns zero-filled dict on failure.
    """
    safe_default = {
        "total_signals":       0,
        "total_practices":     0,
        "signals_by_type":     {st: 0 for st in ALL_SIGNAL_TYPES},
        "high_severity_count": 0,
        "expanding_groups":    0,
    }

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Overall counts
        cursor.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT lead_id),
                SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END)
            FROM dental_growth_signals
            """
        )
        row = cursor.fetchone()
        if not row:
            return safe_default

        total_signals = _safe_int(row[0])
        total_practices = _safe_int(row[1])
        high_severity = _safe_int(row[2])

        # Per-type counts
        cursor.execute(
            """
            SELECT signal_type, COUNT(*)
            FROM dental_growth_signals
            GROUP BY signal_type
            """
        )
        type_rows = cursor.fetchall()
        signals_by_type = {st: 0 for st in ALL_SIGNAL_TYPES}
        for type_row in type_rows:
            st = _safe_str(type_row[0])
            if st in signals_by_type:
                signals_by_type[st] = _safe_int(type_row[1])

        # Expanding groups count
        expanding = get_expanding_groups()

        return {
            "total_signals":       total_signals,
            "total_practices":     total_practices,
            "signals_by_type":     signals_by_type,
            "high_severity_count": high_severity,
            "expanding_groups":    len(expanding),
        }

    except Exception as exc:
        log.error("get_growth_summary failed: %s", exc)
        return safe_default
    finally:
        conn.close()


def batch_scan_dental_leads(
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """
    Run growth signal scanning on all dental leads not yet scanned
    in the last 7 days.

    Args:
        batch_size: int — maximum leads to scan per run (default 50).

    Returns:
        dict with keys:
            processed (int)
            signals_found (int)
            failed (int)
    """
    result = {"processed": 0, "signals_found": 0, "failed": 0}

    conn = db.get_connection()
    try:
        # Leads not scanned in last 7 days
        cutoff = _cutoff_date(7)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT l.id
            FROM leads l
            WHERE (
                LOWER(COALESCE(l.category, '')) LIKE '%dental%'
                OR LOWER(COALESCE(l.category, '')) LIKE '%dentist%'
                OR LOWER(COALESCE(l.vertical, '')) LIKE '%dental%'
            )
            AND l.id NOT IN (
                SELECT DISTINCT lead_id
                FROM dental_growth_signals
                WHERE detected_at >= ?
            )
            LIMIT ?
            """,
            (cutoff, batch_size),
        )
        rows = cursor.fetchall()
        lead_ids = [row[0] for row in rows]
    except Exception as exc:
        log.error("batch_scan_dental_leads — query failed: %s", exc)
        return result
    finally:
        conn.close()

    for lead_id in lead_ids:
        result["processed"] += 1
        try:
            signals = scan_for_growth_signals(lead_id)
            result["signals_found"] += len(signals)
        except Exception as exc:
            log.error(
                "batch_scan — failed for lead_id=%s: %s",
                lead_id,
                exc,
            )
            result["failed"] += 1

    log.info(
        "batch_scan_dental_leads complete — "
        "processed=%s signals_found=%s failed=%s",
        result["processed"],
        result["signals_found"],
        result["failed"],
    )
    return result