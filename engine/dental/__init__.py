# engine/dental/__init__.py
# KNCS LeadGen Pro — Dental Intelligence Engine v2.1
# Delegates to real dental modules.
# Fallback uses industry field (confirmed = 'Dental Clinic')
# category field is NULL in DB — do not filter on category.
# get_growing_practices(days) routes to dental_growth_signals
# get_growing_practices(threshold) routes to dental_review_analyzer

import logging

log = logging.getLogger(__name__)

# ── Import real dental modules ────────────────────────────────

try:
    import engine.dental.dental_intelligence as _di
    _DI_AVAILABLE = True
    log.info("engine.dental: dental_intelligence loaded ✅")
except Exception as _exc:
    _DI_AVAILABLE = False
    log.warning(
        "engine.dental: dental_intelligence unavailable: %s", _exc
    )

try:
    import engine.dental.dental_group_detector as _dgd
    _DGD_AVAILABLE = True
    log.info("engine.dental: dental_group_detector loaded ✅")
except Exception as _exc:
    _DGD_AVAILABLE = False
    log.warning(
        "engine.dental: dental_group_detector unavailable: %s", _exc
    )

try:
    import engine.dental.dental_review_analyzer as _dra
    _DRA_AVAILABLE = True
    log.info("engine.dental: dental_review_analyzer loaded ✅")
except Exception as _exc:
    _DRA_AVAILABLE = False
    log.warning(
        "engine.dental: dental_review_analyzer unavailable: %s", _exc
    )

try:
    import engine.dental.dental_growth_signals as _dgs
    _DGS_AVAILABLE = True
    log.info("engine.dental: dental_growth_signals loaded ✅")
except Exception as _exc:
    _DGS_AVAILABLE = False
    log.warning(
        "engine.dental: dental_growth_signals unavailable: %s", _exc
    )

# ── Fallback: pull dental leads from get_all_leads() ─────────
# industry value confirmed: 'Dental Clinic'
# category is NULL in DB — filter on industry only

def _get_dental_leads() -> list:
    """
    Return all dental leads from the DB.
    Filters on industry field (not category — category is NULL).
    """
    try:
        from database import get_all_leads
        all_leads = get_all_leads() or []
        return [
            l for l in all_leads
            if 'dental' in str(l.get('industry', '')).lower()
        ]
    except Exception as exc:
        log.error("engine.dental._get_dental_leads failed: %s", exc)
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
        "reviews_analyzed":       0,
        "poor_reputation_count":  0,
        "growing_practices":      0,
    }


# ═══════════════════════════════════════════════════════════
# dental_intelligence functions
# ═══════════════════════════════════════════════════════════

def get_intelligence_summary() -> dict:
    """
    Called by dental dashboard _load_summary().

    Returns dict with keys:
        total_leads, high_opportunity_count,
        avg_opportunity_score, reviews_analyzed,
        poor_reputation_count, growing_practices
    """
    if _DI_AVAILABLE:
        try:
            raw = _di.get_intelligence_summary()
            if raw and raw.get("total_scored", 0) > 0:
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
                    "reviews_analyzed": raw.get(
                        "reviews_analyzed", 0
                    ),
                    "poor_reputation_count": raw.get(
                        "poor_reputation_count", 0
                    ),
                    "growing_practices": raw.get(
                        "growing_practices", 0
                    ),
                }
        except Exception as exc:
            log.warning(
                "engine.dental.get_intelligence_summary — "
                "falling back: %s", exc
            )

    # Fallback
    leads = _get_dental_leads()
    return _fallback_summary(leads)


def get_top_opportunities(limit: int = 25) -> list:
    """Called by dental dashboard _load_top_opportunities(limit)."""
    if _DI_AVAILABLE:
        try:
            rows = _di.get_top_opportunities(limit=limit)
            if rows:
                # Patch name → business_name if needed
                for r in rows:
                    if 'business_name' not in r and 'name' in r:
                        r['business_name'] = r['name']
                return rows
        except Exception as exc:
            log.warning(
                "engine.dental.get_top_opportunities — "
                "falling back: %s", exc
            )

    # Fallback
    leads = _get_dental_leads()
    leads.sort(
        key=lambda x: int(x.get('opportunity_score', 0) or 0),
        reverse=True,
    )
    return leads[:limit]


def get_poor_reputation_practices(threshold: int = 50) -> list:
    """
    Called by dental dashboard reputation section.
    Routes to dental_review_analyzer.
    """
    if _DRA_AVAILABLE:
        try:
            rows = _dra.get_poor_reputation_practices(
                threshold=threshold
            )
            if rows:
                return rows
        except Exception as exc:
            log.warning(
                "engine.dental.get_poor_reputation_practices — "
                "falling back: %s", exc
            )

    # Fallback — low rated leads
    leads = _get_dental_leads()
    poor  = [
        l for l in leads
        if float(l.get('rating', 5.0) or 5.0) < 4.0
    ]
    poor.sort(key=lambda x: float(x.get('rating', 5.0) or 5.0))
    return poor[:25]


def get_growing_practices(days: int = 90) -> list:
    """
    Called by dental dashboard growth section.
    Routes to dental_growth_signals (days parameter = growth signals).
    dental_review_analyzer version uses growth_threshold — different call.
    """
    if _DGS_AVAILABLE:
        try:
            rows = _dgs.get_growing_practices(days=days)
            if rows:
                return rows
        except Exception as exc:
            log.warning(
                "engine.dental.get_growing_practices — "
                "falling back: %s", exc
            )

    # Fallback — high rated leads as proxy for growing
    leads = _get_dental_leads()
    growing = [
        l for l in leads
        if float(l.get('rating', 0.0) or 0.0) >= 4.5
    ]
    growing.sort(
        key=lambda x: float(x.get('rating', 0.0) or 0.0),
        reverse=True,
    )
    return growing[:25]


def get_expanding_groups() -> list:
    """
    Called by dental dashboard expanding groups section.
    Routes to dental_growth_signals.
    """
    if _DGS_AVAILABLE:
        try:
            rows = _dgs.get_expanding_groups()
            if rows:
                return rows
        except Exception as exc:
            log.warning(
                "engine.dental.get_expanding_groups — "
                "falling back: %s", exc
            )
    return []


def get_opportunities_by_buyer_type(
    buyer_type: str = "group",
) -> list:
    """Called by dental dashboard buyer type filter."""
    if _DI_AVAILABLE:
        try:
            rows = _di.get_opportunities_by_buyer_type(
                buyer_type=buyer_type
            )
            if rows:
                return rows
        except Exception as exc:
            log.warning(
                "engine.dental.get_opportunities_by_buyer_type — "
                "falling back: %s", exc
            )

    # Fallback
    leads = _get_dental_leads()
    return leads[:25]


# ═══════════════════════════════════════════════════════════
# dental_group_detector functions
# ═══════════════════════════════════════════════════════════

def identify_dental_groups() -> list:
    """Called by dental dashboard groups section."""
    if _DGD_AVAILABLE:
        try:
            return _dgd.identify_dental_groups() or []
        except Exception as exc:
            log.warning(
                "engine.dental.identify_dental_groups — "
                "falling back: %s", exc
            )
    return []


def get_enterprise_opportunities(min_sites: int = 2) -> list:
    """Called by dental dashboard enterprise section."""
    if _DGD_AVAILABLE:
        try:
            return _dgd.get_enterprise_opportunities(
                min_sites=min_sites
            ) or []
        except Exception as exc:
            log.warning(
                "engine.dental.get_enterprise_opportunities — "
                "falling back: %s", exc
            )
    return []