# care_supplier_matcher.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Matches care home leads to supplier types.
#            Scores each lead's relevance for a given supplier
#            category using CQC data, opportunity signals,
#            and lead profile fields.
# Dependencies: database.py, engine/care/care_intelligence.py
# Critical rules observed:
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — missing fields reduce score, not crash
#   - No exceptions raised to caller

import logging
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# SUPPLIER TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────

SUPPLIER_STAFFING          = "staffing"
SUPPLIER_SOFTWARE_TECH     = "software_technology"
SUPPLIER_INSURANCE         = "insurance"
SUPPLIER_TRAINING          = "training"
SUPPLIER_CATERING          = "catering"
SUPPLIER_MAINTENANCE       = "maintenance"
SUPPLIER_MEDICAL_SUPPLIES  = "medical_supplies"
SUPPLIER_CONSULTANCY       = "consultancy"

VALID_SUPPLIER_TYPES = {
    SUPPLIER_STAFFING,
    SUPPLIER_SOFTWARE_TECH,
    SUPPLIER_INSURANCE,
    SUPPLIER_TRAINING,
    SUPPLIER_CATERING,
    SUPPLIER_MAINTENANCE,
    SUPPLIER_MEDICAL_SUPPLIES,
    SUPPLIER_CONSULTANCY,
}

# Human-readable labels for UI display
SUPPLIER_LABELS = {
    SUPPLIER_STAFFING:         "Staffing & Recruitment",
    SUPPLIER_SOFTWARE_TECH:    "Software & Technology",
    SUPPLIER_INSURANCE:        "Insurance",
    SUPPLIER_TRAINING:         "Training & Compliance",
    SUPPLIER_CATERING:         "Catering & Food Services",
    SUPPLIER_MAINTENANCE:      "Maintenance & Facilities",
    SUPPLIER_MEDICAL_SUPPLIES: "Medical Supplies & Equipment",
    SUPPLIER_CONSULTANCY:      "Care Consultancy",
}

# ─────────────────────────────────────────────────────────────
# SCORING SIGNALS PER SUPPLIER TYPE
# ─────────────────────────────────────────────────────────────
# Each entry defines what care home characteristics
# positively indicate demand for that supplier type.
# Weights sum to 100 per supplier type.

SUPPLIER_SIGNAL_CONFIG = {

    SUPPLIER_STAFFING: {
        "description": (
            "Homes most likely to need agency or permanent "
            "recruitment support."
        ),
        "signals": [
            # CQC rating of Requires Improvement or Inadequate
            # often correlates with staffing issues
            {
                "field":    "cqc_rating",
                "matches":  ["requires improvement", "inadequate"],
                "points":   30,
                "label":    "Poor CQC rating — likely staffing pressure",
            },
            # New registrations need staff immediately
            {
                "signal_type": "new_registration",
                "points":      25,
                "label":       "New registration — immediate staffing need",
            },
            # Director change often precedes restructure and hiring
            {
                "signal_type": "director_change",
                "points":      20,
                "label":       "Director change — likely staffing review",
            },
            # Large bed count = large workforce
            {
                "field":    "bed_count",
                "min":      40,
                "points":   15,
                "label":    "Large home — high staffing volume",
            },
            # Any acquisition signal = transition period
            {
                "has_any_signal": True,
                "points":         10,
                "label":          "Active acquisition signal detected",
            },
        ],
    },

    SUPPLIER_SOFTWARE_TECH: {
        "description": (
            "Homes most likely to adopt care management software, "
            "digital care planning, or eMAR systems."
        ),
        "signals": [
            # New registration — needs systems from day one
            {
                "signal_type": "new_registration",
                "points":      30,
                "label":       "New registration — needs software from day one",
            },
            # Director change — new leader often digitises
            {
                "signal_type": "director_change",
                "points":      25,
                "label":       "New director — likely to modernise systems",
            },
            # Poor CQC rating — software can help compliance
            {
                "field":   "cqc_rating",
                "matches": ["requires improvement", "inadequate"],
                "points":  25,
                "label":   "Poor CQC — software can improve compliance",
            },
            # Opportunity score above threshold
            {
                "field":   "opportunity_score",
                "min":     60,
                "points":  20,
                "label":   "High opportunity score",
            },
        ],
    },

    SUPPLIER_INSURANCE: {
        "description": (
            "Homes requiring liability, professional indemnity, "
            "or property insurance review."
        ),
        "signals": [
            # New registration — mandatory insurance required
            {
                "signal_type": "new_registration",
                "points":      35,
                "label":       "New registration — insurance mandatory",
            },
            # Director change — new director may review providers
            {
                "signal_type": "director_change",
                "points":      25,
                "label":       "Director change — likely insurance review",
            },
            # CQC inspection — post-inspection risk review
            {
                "signal_type": "recent_inspection",
                "points":      20,
                "label":       "Recent inspection — risk profile updated",
            },
            # Large homes carry more liability
            {
                "field":  "bed_count",
                "min":    30,
                "points": 20,
                "label":  "Large home — higher insurance requirements",
            },
        ],
    },

    SUPPLIER_TRAINING: {
        "description": (
            "Homes most likely to need mandatory and specialist "
            "training — compliance, dementia, first aid, fire safety."
        ),
        "signals": [
            # Poor CQC rating = training gap identified
            {
                "field":   "cqc_rating",
                "matches": ["requires improvement", "inadequate"],
                "points":  35,
                "label":   "Poor CQC — training gaps likely cited",
            },
            # Recent inspection = training actions likely
            {
                "signal_type": "recent_inspection",
                "points":      25,
                "label":       "Recent inspection — training actions required",
            },
            # Rating downgrade = urgent training needed
            {
                "signal_type": "cqc_rating_change",
                "points":      25,
                "label":       "CQC rating change — urgent training response",
            },
            # New registration = staff induction training needed
            {
                "signal_type": "new_registration",
                "points":      15,
                "label":       "New home — staff induction training needed",
            },
        ],
    },

    SUPPLIER_CATERING: {
        "description": (
            "Homes requiring catering supply, dietary management, "
            "or food service contracts."
        ),
        "signals": [
            # Bed count drives catering volume
            {
                "field":  "bed_count",
                "min":    20,
                "points": 35,
                "label":  "20+ beds — meaningful catering contract",
            },
            {
                "field":  "bed_count",
                "min":    50,
                "points": 20,   # bonus points for large homes
                "label":  "50+ beds — high-volume catering opportunity",
            },
            # New registration needs catering supplier immediately
            {
                "signal_type": "new_registration",
                "points":      25,
                "label":       "New home — catering contract needed",
            },
            # Director change may trigger supplier review
            {
                "signal_type": "director_change",
                "points":      20,
                "label":       "Director change — supplier review likely",
            },
        ],
    },

    SUPPLIER_MAINTENANCE: {
        "description": (
            "Homes requiring building maintenance, HVAC, "
            "electrical, or facilities management."
        ),
        "signals": [
            # Older registration = older building = maintenance needs
            {
                "field":   "registration_age_years",
                "min":     5,
                "points":  30,
                "label":   "Established home — ongoing maintenance needs",
            },
            # Large home = larger estate to maintain
            {
                "field":  "bed_count",
                "min":    30,
                "points": 25,
                "label":  "Large home — significant maintenance estate",
            },
            # Recent inspection often flags building issues
            {
                "signal_type": "recent_inspection",
                "points":      25,
                "label":       "Recent inspection — environment actions possible",
            },
            # New director may commission building survey
            {
                "signal_type": "director_change",
                "points":      20,
                "label":       "Director change — facilities review likely",
            },
        ],
    },

    SUPPLIER_MEDICAL_SUPPLIES: {
        "description": (
            "Homes requiring continence products, wound care, "
            "PPE, or medical consumables."
        ),
        "signals": [
            # Bed count is the primary driver — more residents = more supplies
            {
                "field":  "bed_count",
                "min":    10,
                "points": 35,
                "label":  "Residential home — ongoing consumable demand",
            },
            {
                "field":  "bed_count",
                "min":    40,
                "points": 20,
                "label":  "Large home — high consumable volume",
            },
            # New registration = establish supplier relationships
            {
                "signal_type": "new_registration",
                "points":      25,
                "label":       "New home — supplier relationships to establish",
            },
            # Any care home with an active opportunity signal
            {
                "has_any_signal": True,
                "points":         20,
                "label":          "Active signal — transition period purchasing",
            },
        ],
    },

    SUPPLIER_CONSULTANCY: {
        "description": (
            "Homes most likely to engage a care consultant — "
            "for CQC improvement plans, turnaround, or growth strategy."
        ),
        "signals": [
            # Inadequate rating = urgent consultancy need
            {
                "field":   "cqc_rating",
                "matches": ["inadequate"],
                "points":  40,
                "label":   "Inadequate rating — urgent consultancy need",
            },
            # Requires improvement = improvement plan needed
            {
                "field":   "cqc_rating",
                "matches": ["requires improvement"],
                "points":  25,
                "label":   "Requires Improvement — consultancy opportunity",
            },
            # Rating downgrade = crisis response
            {
                "signal_type": "cqc_rating_change",
                "points":      20,
                "label":       "Rating downgraded — consultancy engagement likely",
            },
            # Director change = new leader needs guidance
            {
                "signal_type": "director_change",
                "points":      15,
                "label":       "New director — likely to seek external advice",
            },
        ],
    },
}

# ─────────────────────────────────────────────────────────────
# MODULE STATE
# ─────────────────────────────────────────────────────────────

_active_supplier_type: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _fetch_all_care_leads_with_signals(conn) -> list:
    """
    Fetch all care leads joined with their opportunity score
    and any stored acquisition signals.

    Returns:
        List of lead dicts enriched with a 'signals' list.
    """
    try:
        cur = conn.execute(
            """
            SELECT  l.id            AS lead_id,
                    l.business_name,
                    l.address,
                    l.website,
                    l.cqc_rating,
                    l.bed_count,
                    l.company_number,
                    l.director_name,
                    l.registration_date,
                    co.opportunity_score,
                    co.opportunity_type,
                    co.urgency_level
            FROM    leads l
            LEFT    JOIN care_home_opportunities co
                    ON  co.lead_id = l.id
            WHERE   l.vertical = 'care'
               OR   l.industry LIKE '%care%'
               OR   l.industry LIKE '%nursing%'
               OR   l.industry LIKE '%residential%'
            ORDER   BY l.business_name ASC
            """
        )
        leads = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        log.error("_fetch_all_care_leads_with_signals error=%s", exc)
        return []

    # Enrich each lead with its acquisition signals
    for lead in leads:
        lead_id = lead["lead_id"]
        try:
            scur = conn.execute(
                """
                SELECT signal_type, detail
                FROM   care_acquisition_signals
                WHERE  lead_id = ?
                """,
                (lead_id,),
            )
            lead["signals"] = [dict(r) for r in scur.fetchall()]
        except Exception as exc:
            log.debug(
                "_fetch_all_care_leads_with_signals signals error lead_id=%s: %s",
                lead_id, exc,
            )
            lead["signals"] = []

        # Derive registration age in years
        reg_date = lead.get("registration_date") or ""
        if reg_date:
            try:
                from datetime import datetime
                rd = datetime.strptime(reg_date[:10], "%Y-%m-%d")
                lead["registration_age_years"] = (
                    datetime.utcnow() - rd
                ).days // 365
            except Exception:
                lead["registration_age_years"] = 0
        else:
            lead["registration_age_years"] = 0

    return leads


def _score_lead_for_supplier(lead: dict, supplier_type: str) -> int:
    """
    Calculate a match score (0-100) for one lead against one supplier type.

    Iterates through the signal config for the supplier type,
    awarding points for each matching condition.
    Caps at 100.

    Args:
        lead:          Lead dict with signals list attached.
        supplier_type: One of the SUPPLIER_* constants.

    Returns:
        Integer score 0-100.
    """
    config = SUPPLIER_SIGNAL_CONFIG.get(supplier_type)
    if not config:
        return 0

    total   = 0
    signals = lead.get("signals") or []
    signal_types = {s.get("signal_type") for s in signals}
    has_any_signal = len(signals) > 0

    for rule in config.get("signals", []):
        points = rule.get("points", 0)

        # ── Rule: has_any_signal ──────────────────────────────
        if "has_any_signal" in rule:
            if has_any_signal:
                total += points
            continue

        # ── Rule: signal_type match ───────────────────────────
        if "signal_type" in rule:
            if rule["signal_type"] in signal_types:
                total += points
            continue

        # ── Rule: field exact/list match ──────────────────────
        if "field" in rule and "matches" in rule:
            field_val = (lead.get(rule["field"]) or "").lower().strip()
            if field_val in [m.lower() for m in rule["matches"]]:
                total += points
            continue

        # ── Rule: field numeric minimum ───────────────────────
        if "field" in rule and "min" in rule:
            field_val = lead.get(rule["field"])
            try:
                if field_val is not None and float(field_val) >= rule["min"]:
                    total += points
            except (TypeError, ValueError):
                pass
            continue

    return min(100, total)


def _get_matched_signals_for_lead(lead: dict, supplier_type: str) -> list:
    """
    Return the list of signal labels that fired for a lead
    against a specific supplier type.

    Args:
        lead:          Lead dict with signals list attached.
        supplier_type: One of the SUPPLIER_* constants.

    Returns:
        List of label strings for matched rules.
    """
    config = SUPPLIER_SIGNAL_CONFIG.get(supplier_type)
    if not config:
        return []

    matched  = []
    signals  = lead.get("signals") or []
    signal_types = {s.get("signal_type") for s in signals}
    has_any_signal = len(signals) > 0

    for rule in config.get("signals", []):

        if "has_any_signal" in rule:
            if has_any_signal:
                matched.append(rule.get("label", "Signal detected"))
            continue

        if "signal_type" in rule:
            if rule["signal_type"] in signal_types:
                matched.append(rule.get("label", rule["signal_type"]))
            continue

        if "field" in rule and "matches" in rule:
            field_val = (lead.get(rule["field"]) or "").lower().strip()
            if field_val in [m.lower() for m in rule["matches"]]:
                matched.append(rule.get("label", rule["field"]))
            continue

        if "field" in rule and "min" in rule:
            field_val = lead.get(rule["field"])
            try:
                if field_val is not None and float(field_val) >= rule["min"]:
                    matched.append(rule.get("label", rule["field"]))
            except (TypeError, ValueError):
                pass
            continue

    return matched


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def set_supplier_type(supplier_type: str) -> None:
    """
    Set the active supplier type for the current session.
    Subsequent calls to get_match_score() will use this type
    if no explicit type is passed.

    Args:
        supplier_type: One of the SUPPLIER_* constants.
    """
    global _active_supplier_type
    if supplier_type not in VALID_SUPPLIER_TYPES:
        log.warning(
            "set_supplier_type — invalid type: %s. Valid: %s",
            supplier_type, VALID_SUPPLIER_TYPES,
        )
        return
    _active_supplier_type = supplier_type
    log.info("set_supplier_type — active type set to: %s", supplier_type)


def get_match_score(lead: dict, supplier_type: str) -> int:
    """
    Calculate a match score for a single lead dict
    against a given supplier type.

    Args:
        lead:          Lead dict. Must include at least lead_id.
                       Should include signals list for best accuracy.
        supplier_type: One of the SUPPLIER_* constants.

    Returns:
        Integer match score 0-100.
        Returns 0 for invalid supplier type.
    """
    if supplier_type not in VALID_SUPPLIER_TYPES:
        log.warning("get_match_score — invalid supplier_type: %s", supplier_type)
        return 0
    return _score_lead_for_supplier(lead, supplier_type)


def get_matched_opportunities(
    supplier_type: str,
    min_score:     int = 20,
    limit:         int = 100,
) -> list:
    """
    Return all care home leads ranked by match score
    for a given supplier type.

    Args:
        supplier_type: One of the SUPPLIER_* constants.
        min_score:     Minimum score to include. Default 20.
        limit:         Maximum rows to return. Default 100.

    Returns:
        List of dicts ordered by match_score descending.
        Each dict contains:
            lead_id, business_name, address, website,
            cqc_rating, bed_count, opportunity_score,
            match_score, matched_signals (list of str),
            supplier_type, supplier_label
        Empty list on error or invalid supplier_type.
    """
    if supplier_type not in VALID_SUPPLIER_TYPES:
        log.warning(
            "get_matched_opportunities — invalid supplier_type: %s",
            supplier_type,
        )
        return []

    conn = db.get_connection()
    try:
        leads = _fetch_all_care_leads_with_signals(conn)
    except Exception as exc:
        log.error("get_matched_opportunities fetch error=%s", exc)
        return []
    finally:
        conn.close()

    results = []
    for lead in leads:
        score   = _score_lead_for_supplier(lead, supplier_type)
        matched = _get_matched_signals_for_lead(lead, supplier_type)

        if score < min_score:
            continue

        results.append({
            "lead_id":          lead["lead_id"],
            "business_name":    lead.get("business_name") or "",
            "address":          lead.get("address") or "",
            "website":          lead.get("website") or "",
            "cqc_rating":       lead.get("cqc_rating") or "Unknown",
            "bed_count":        lead.get("bed_count"),
            "opportunity_score": lead.get("opportunity_score"),
            "match_score":      score,
            "matched_signals":  matched,
            "supplier_type":    supplier_type,
            "supplier_label":   SUPPLIER_LABELS.get(supplier_type, supplier_type),
        })

    # Sort by match_score descending
    results.sort(key=lambda x: x["match_score"], reverse=True)

    log.info(
        "get_matched_opportunities type=%s min=%s returned %s/%s leads",
        supplier_type, min_score, min(len(results), limit), len(results),
    )
    return results[:limit]


def get_supplier_type_signals(supplier_type: str) -> dict:
    """
    Return the full signal configuration for a supplier type.
    Used by the dashboard to explain what signals drive the scoring.

    Args:
        supplier_type: One of the SUPPLIER_* constants.

    Returns:
        dict with keys:
            supplier_type, label, description, signals (list)
        Empty dict for invalid type.
    """
    if supplier_type not in VALID_SUPPLIER_TYPES:
        log.warning(
            "get_supplier_type_signals — invalid type: %s", supplier_type
        )
        return {}

    config = SUPPLIER_SIGNAL_CONFIG.get(supplier_type, {})
    return {
        "supplier_type": supplier_type,
        "label":         SUPPLIER_LABELS.get(supplier_type, supplier_type),
        "description":   config.get("description", ""),
        "signals":       config.get("signals", []),
    }