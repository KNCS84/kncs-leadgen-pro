# engine/care/__init__.py
# KNCS LeadGen Pro — Care Intelligence Engine v2.1
# Delegates to real care modules.
# Fallback uses industry field (confirmed = 'Care Home' / 'care home')
# category field is NULL in DB — do not filter on category.

import logging

log = logging.getLogger(__name__)

# ── Import real care modules ──────────────────────────────────

try:
    import engine.care.care_intelligence as _ci
    _CI_AVAILABLE = True
    log.info("engine.care: care_intelligence loaded ✅")
except Exception as _exc:
    _CI_AVAILABLE = False
    log.warning("engine.care: care_intelligence unavailable: %s", _exc)

try:
    import engine.care.care_operator_groups as _og
    _OG_AVAILABLE = True
    log.info("engine.care: care_operator_groups loaded ✅")
except Exception as _exc:
    _OG_AVAILABLE = False
    log.warning("engine.care: care_operator_groups unavailable: %s", _exc)

try:
    import engine.care.care_acquisition_signals as _acs
    _ACS_AVAILABLE = True
    log.info("engine.care: care_acquisition_signals loaded ✅")
except Exception as _exc:
    _ACS_AVAILABLE = False
    log.warning(
        "engine.care: care_acquisition_signals unavailable: %s", _exc
    )

try:
    import engine.care.care_supplier_matcher as _sm
    _SM_AVAILABLE = True
    log.info("engine.care: care_supplier_matcher loaded ✅")
except Exception as _exc:
    _SM_AVAILABLE = False
    log.warning(
        "engine.care: care_supplier_matcher unavailable: %s", _exc
    )

# ── Fallback: pull care leads from get_all_leads() ───────────
# industry values confirmed: 'Care Home', 'care home'
# category is NULL in DB — filter on industry only

def _get_care_leads() -> list:
    """
    Return all care home leads from the DB.
    Filters on industry field (not category — category is NULL).
    """
    try:
        from database import get_all_leads
        all_leads = get_all_leads() or []
        return [
            l for l in all_leads
            if 'care' in str(l.get('industry', '')).lower()
        ]
    except Exception as exc:
        log.error("engine.care._get_care_leads failed: %s", exc)
        return []


def _fallback_summary(leads: list) -> dict:
    """Build summary dict from raw leads when real module unavailable."""
    scores = [
        int(l.get('opportunity_score', 0) or 0)
        for l in leads
    ]
    high = [s for s in scores if s >= 70]
    avg  = round(sum(scores) / len(scores), 1) if scores else 0.0
    return {
        "total_leads":            len(leads),
        "high_opportunity_count": len(high),
        "avg_opportunity_score":  avg,
        "operator_groups":        0,
        "acquisition_signals":    0,
    }


# ═══════════════════════════════════════════════════════════
# care_intelligence functions
# ═══════════════════════════════════════════════════════════

def get_intelligence_summary() -> dict:
    """
    Called by care dashboard _load_summary().

    Returns dict with keys:
        total_leads, high_opportunity_count,
        avg_opportunity_score, operator_groups,
        acquisition_signals
    """
    # ── Try real module ───────────────────────────────────
    if _CI_AVAILABLE:
        try:
            raw = _ci.get_intelligence_summary()
            if raw and raw.get("total_scored", 0) > 0:
                # Translate key names to what dashboard expects
                return {
                    "total_leads": raw.get(
                        "total_leads",
                        raw.get("total_scored", 0)
                    ),
                    "high_opportunity_count": raw.get(
                        "high_opportunity_count",
                        raw.get("high_urgency", 0)
                    ),
                    "avg_opportunity_score": raw.get(
                        "avg_opportunity_score",
                        raw.get("avg_opp_score", 0.0)
                    ),
                    "operator_groups": raw.get(
                        "operator_groups", 0
                    ),
                    "acquisition_signals": raw.get(
                        "acquisition_signals", 0
                    ),
                }
        except Exception as exc:
            log.warning(
                "engine.care.get_intelligence_summary — "
                "real module failed, falling back: %s", exc
            )

    # ── Fallback ──────────────────────────────────────────
    leads = _get_care_leads()
    return _fallback_summary(leads)


def get_top_opportunities(limit: int = 25) -> list:
    """
    Called by care dashboard _load_top_opportunities(limit).
    """
    if _CI_AVAILABLE:
        try:
            rows = _ci.get_top_opportunities(limit=limit)
            if rows:
                return rows
        except Exception as exc:
            log.warning(
                "engine.care.get_top_opportunities — "
                "falling back: %s", exc
            )

    # Fallback — return care leads sorted by opportunity_score
    leads = _get_care_leads()
    leads.sort(
        key=lambda x: int(x.get('opportunity_score', 0) or 0),
        reverse=True,
    )
    return leads[:limit]


# ═══════════════════════════════════════════════════════════
# care_operator_groups functions
# ═══════════════════════════════════════════════════════════

def identify_operator_groups() -> list:
    """Called by care dashboard operator groups section."""
    if _OG_AVAILABLE:
        try:
            return _og.identify_operator_groups() or []
        except Exception as exc:
            log.warning(
                "engine.care.identify_operator_groups — "
                "falling back: %s", exc
            )
    return []


def get_enterprise_opportunities(min_sites: int = 3) -> list:
    """Called by care dashboard enterprise section."""
    if _OG_AVAILABLE:
        try:
            return _og.get_enterprise_opportunities(
                min_sites=min_sites
            ) or []
        except Exception as exc:
            log.warning(
                "engine.care.get_enterprise_opportunities — "
                "falling back: %s", exc
            )
    return []


# ═══════════════════════════════════════════════════════════
# care_acquisition_signals functions
# ═══════════════════════════════════════════════════════════

def get_recent_director_changes(days: int = 30) -> list:
    """Called by care dashboard acquisition signals section."""
    if _ACS_AVAILABLE:
        try:
            return _acs.get_recent_director_changes(
                days=days
            ) or []
        except Exception as exc:
            log.warning(
                "engine.care.get_recent_director_changes — "
                "falling back: %s", exc
            )
    return []


def get_rating_changes(days: int = 30) -> list:
    """Called by care dashboard rating changes section."""
    if _ACS_AVAILABLE:
        try:
            return _acs.get_rating_changes(days=days) or []
        except Exception as exc:
            log.warning(
                "engine.care.get_rating_changes — "
                "falling back: %s", exc
            )
    return []


def get_new_registrations(days: int = 60) -> list:
    """Called by care dashboard new registrations section."""
    if _ACS_AVAILABLE:
        try:
            return _acs.get_new_registrations(days=days) or []
        except Exception as exc:
            log.warning(
                "engine.care.get_new_registrations — "
                "falling back: %s", exc
            )
    return []


def scan_for_acquisition_signals() -> dict:
    """Called by care dashboard acquisition scan."""
    if _ACS_AVAILABLE:
        try:
            return _acs.scan_for_acquisition_signals() or {}
        except Exception as exc:
            log.warning(
                "engine.care.scan_for_acquisition_signals — "
                "falling back: %s", exc
            )
    return {}


# ═══════════════════════════════════════════════════════════
# care_supplier_matcher functions
# ═══════════════════════════════════════════════════════════

def get_matched_opportunities(
    supplier_type: str = "staffing",
) -> list:
    """Called by care dashboard supplier matcher section."""
    if _SM_AVAILABLE:
        try:
            return _sm.get_matched_opportunities(
                supplier_type=supplier_type
            ) or []
        except Exception as exc:
            log.warning(
                "engine.care.get_matched_opportunities — "
                "falling back: %s", exc
            )
    return []