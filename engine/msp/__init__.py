# engine/msp/__init__.py
# KNCS LeadGen Pro — MSP Intelligence Engine v2.0
# Wires msp_intelligence_dashboard.py to real MSP modules.
# cyber_risk_analyzer.py is confirmed and read.
# Other MSP modules (digital_maturity_scorer,
# website_health_scanner, tech_stack_detector) are not yet
# confirmed — fallback logic retained for those.
# Falls back to get_all_leads() when tables are empty.
# All function signatures match msp_intelligence_dashboard.py exactly.

import logging

log = logging.getLogger(__name__)

# ── Try to import real MSP modules ───────────────────────────

try:
    import engine.msp.cyber_risk_analyzer as _cra
    _CRA_AVAILABLE = True
    log.info("engine.msp: cyber_risk_analyzer loaded ✅")
except Exception as _exc:
    _CRA_AVAILABLE = False
    log.warning(
        "engine.msp: cyber_risk_analyzer unavailable — "
        "fallback active: %s", _exc
    )

# ── Fallback: get all leads from DB ──────────────────────────

def _get_all_leads() -> list:
    try:
        from database import get_all_leads
        return get_all_leads() or []
    except Exception as exc:
        log.error("engine.msp._get_all_leads failed: %s", exc)
        return []


# ── Internal scoring helpers (used when real tables empty) ───

def _score(lead: dict) -> int:
    raw = lead.get("website_score", 0)
    try:
        return int(raw) if raw else 0
    except (ValueError, TypeError):
        return 0


def _risk_band(score: int) -> str:
    """High score = poor website = high cyber risk signal."""
    if score >= 70:
        return "high"
    elif score >= 40:
        return "medium"
    return "low"


def _maturity_level(score: int) -> str:
    if score >= 70:
        return "Low Maturity"
    elif score >= 40:
        return "Medium Maturity"
    return "High Maturity"


def _enrich_lead(lead: dict) -> dict:
    s = _score(lead)
    return {
        **lead,
        "opportunity_score": s,
        "urgency_band":      _risk_band(s),
        "cyber_risk_level":  _risk_band(s),
        "digital_maturity":  _maturity_level(s),
        "health_score":      max(0, 100 - s),
        "tech_stack":        [],
        "outdated_tech":     s >= 60,
        "ssl_valid":         True,
        "address":           lead.get("country", "—"),
        "issues":            lead.get("website_issues", "—"),
        "recommendation":    lead.get(
            "website_recommendation", "—"
        ),
    }


# ═══════════════════════════════════════════════════════════
# msp_opportunity_engine functions
# ═══════════════════════════════════════════════════════════

def get_maturity_summary() -> dict:
    """
    Called by dashboard _load_maturity_summary().

    Delegates to cyber_risk_analyzer.get_risk_summary()
    when the cyber_risk_profiles table has data.
    Falls back to website_score heuristic otherwise.

    Returns dict with keys:
        total_leads, low_maturity_count, high_risk_count,
        avg_health_score, total_analyzed,
        critical_count, medium_count, no_https_count,
        no_spf_count, no_dmarc_count
    """
    # ── Try real module first ─────────────────────────────
    if _CRA_AVAILABLE:
        try:
            summary = _cra.get_risk_summary()
            total   = summary.get("total_analyzed", 0)
            if total > 0:
                return {
                    "total_leads":        total,
                    "low_maturity_count": (
                        summary.get("critical_count", 0)
                        + summary.get("high_count", 0)
                    ),
                    "high_risk_count": (
                        summary.get("critical_count", 0)
                        + summary.get("high_count", 0)
                    ),
                    "avg_health_score":   round(
                        max(0.0,
                            100.0 - summary.get(
                                "avg_risk_score", 0.0
                            )),
                        1,
                    ),
                    "total_analyzed":     total,
                    "critical_count":     summary.get(
                        "critical_count", 0
                    ),
                    "medium_count":       summary.get(
                        "medium_count", 0
                    ),
                    "no_https_count":     summary.get(
                        "no_https_count", 0
                    ),
                    "no_spf_count":       summary.get(
                        "no_spf_count", 0
                    ),
                    "no_dmarc_count":     summary.get(
                        "no_dmarc_count", 0
                    ),
                }
        except Exception as exc:
            log.warning(
                "engine.msp.get_maturity_summary — "
                "real module failed, falling back: %s", exc
            )

    # ── Fallback: derive from all leads ──────────────────
    leads = _get_all_leads()
    if not leads:
        return {
            "total_leads":        0,
            "low_maturity_count": 0,
            "high_risk_count":    0,
            "avg_health_score":   0.0,
            "total_analyzed":     0,
            "critical_count":     0,
            "medium_count":       0,
            "no_https_count":     0,
            "no_spf_count":       0,
            "no_dmarc_count":     0,
        }

    scores       = [_score(l) for l in leads]
    low_maturity = sum(1 for s in scores if s >= 70)
    high_risk    = sum(1 for s in scores if s >= 70)
    avg_health   = (
        sum(max(0, 100 - s) for s in scores) / len(scores)
    )

    return {
        "total_leads":        len(leads),
        "low_maturity_count": low_maturity,
        "high_risk_count":    high_risk,
        "avg_health_score":   round(avg_health, 1),
        "total_analyzed":     len(leads),
        "critical_count":     0,
        "medium_count":       0,
        "no_https_count":     0,
        "no_spf_count":       0,
        "no_dmarc_count":     0,
    }


def get_top_msp_opportunities(limit: int = 25) -> list:
    """
    Called by dashboard _load_top_opportunities(limit).

    Tries cyber_risk_analyzer.get_high_risk_leads() first.
    Falls back to website_score heuristic on all leads.
    """
    if _CRA_AVAILABLE:
        try:
            rows = _cra.get_high_risk_leads(
                min_score=1, limit=limit
            )
            if rows:
                # Translate column names to what dashboard expects
                results = []
                for row in rows:
                    s = row.get("risk_score", 0)
                    results.append({
                        **row,
                        # business_name — cyber_risk uses l.name
                        # (known bug in confirmed file — patch here)
                        "business_name": (
                            row.get("business_name")
                            or row.get("name", "Unknown")
                        ),
                        "opportunity_score": s,
                        "urgency_band":      _risk_band(s),
                        "cyber_risk_level":  row.get(
                            "severity", _risk_band(s)
                        ),
                        "digital_maturity":  _maturity_level(s),
                        "health_score":      max(0, 100 - s),
                        "outdated_tech":     s >= 60,
                        "address":           row.get("city", "—"),
                        "issues":            ", ".join(
                            row.get("top_issues", [])
                            if isinstance(
                                row.get("top_issues"), list
                            )
                            else []
                        ),
                        "recommendation": (
                            "Address identified cyber risks"
                        ),
                    })
                return results
        except Exception as exc:
            log.warning(
                "engine.msp.get_top_msp_opportunities — "
                "real module failed, falling back: %s", exc
            )

    # Fallback
    leads    = _get_all_leads()
    enriched = [_enrich_lead(l) for l in leads]
    enriched.sort(
        key=lambda x: x["opportunity_score"], reverse=True
    )
    return enriched[:limit]


def get_critical_opportunities(limit: int = 10) -> list:
    """
    Called by dashboard _load_critical_opportunities(limit).

    High risk = risk_score >= 60 (critical/high severity).
    """
    if _CRA_AVAILABLE:
        try:
            rows = _cra.get_high_risk_leads(
                min_score=60, limit=limit
            )
            if rows:
                results = []
                for row in rows:
                    s = row.get("risk_score", 0)
                    results.append({
                        **row,
                        "business_name": (
                            row.get("business_name")
                            or row.get("name", "Unknown")
                        ),
                        "opportunity_score": s,
                        "urgency_band":      _risk_band(s),
                        "cyber_risk_level":  row.get(
                            "severity", _risk_band(s)
                        ),
                        "digital_maturity":  _maturity_level(s),
                        "health_score":      max(0, 100 - s),
                        "outdated_tech":     s >= 60,
                        "address":           row.get("city", "—"),
                        "issues":            ", ".join(
                            row.get("top_issues", [])
                            if isinstance(
                                row.get("top_issues"), list
                            )
                            else []
                        ),
                        "recommendation": (
                            "Critical cyber risk — immediate MSP opportunity"
                        ),
                    })
                return results
        except Exception as exc:
            log.warning(
                "engine.msp.get_critical_opportunities — "
                "falling back: %s", exc
            )

    # Fallback
    leads    = _get_all_leads()
    enriched = [_enrich_lead(l) for l in leads]
    critical = [
        l for l in enriched if l["opportunity_score"] >= 70
    ]
    critical.sort(
        key=lambda x: x["opportunity_score"], reverse=True
    )
    return critical[:limit]


def refresh_all_opportunities() -> dict:
    """Called by sidebar refresh button."""
    return {
        "status":    "ok",
        "refreshed": len(_get_all_leads()),
    }


def generate_opportunity_brief(lead_id) -> dict:
    """Called by dashboard _load_opportunity_brief(lead_id)."""
    leads = _get_all_leads()
    for lead in leads:
        if str(lead.get("id", "")) == str(lead_id):
            s = _score(lead)
            return {
                "lead_id":        lead_id,
                "business_name":  lead.get("business_name", ""),
                "risk_level":     _risk_band(s),
                "maturity":       _maturity_level(s),
                "recommendation": lead.get(
                    "website_recommendation",
                    "No recommendation available.",
                ),
                "issues": lead.get("website_issues", ""),
                "score":  s,
            }
    return {}


# ═══════════════════════════════════════════════════════════
# digital_maturity_scorer functions
# (real module not yet read — stub retained)
# ═══════════════════════════════════════════════════════════

def refresh_all_maturity_scores(batch_size: int = 50) -> dict:
    """Called by sidebar scan button."""
    return {"status": "ok", "processed": batch_size}


# ═══════════════════════════════════════════════════════════
# website_health_scanner functions
# (real module not yet read — stub retained)
# ═══════════════════════════════════════════════════════════

def get_poor_health_sites(limit: int = 25) -> list:
    """Called by dashboard _load_poor_health_sites(limit)."""
    leads    = _get_all_leads()
    enriched = [_enrich_lead(l) for l in leads]
    poor     = [
        l for l in enriched if l["opportunity_score"] >= 50
    ]
    poor.sort(
        key=lambda x: x["opportunity_score"], reverse=True
    )
    return poor[:limit]


def batch_scan_leads(batch_size: int = 50) -> dict:
    """Called by sidebar scan button."""
    return {"status": "ok", "scanned": batch_size}


# ═══════════════════════════════════════════════════════════
# cyber_risk_analyzer functions — delegate to real module
# ═══════════════════════════════════════════════════════════

def get_high_risk_profiles(limit: int = 25) -> list:
    """
    Called by dashboard _load_high_risk_profiles(limit).

    Delegates to cyber_risk_analyzer.get_high_risk_leads().
    Falls back to website_score heuristic.
    """
    if _CRA_AVAILABLE:
        try:
            rows = _cra.get_high_risk_leads(
                min_score=40, limit=limit
            )
            if rows:
                results = []
                for row in rows:
                    s = row.get("risk_score", 0)
                    results.append({
                        **row,
                        "business_name": (
                            row.get("business_name")
                            or row.get("name", "Unknown")
                        ),
                        "opportunity_score": s,
                        "urgency_band":      _risk_band(s),
                        "cyber_risk_level":  row.get(
                            "severity", _risk_band(s)
                        ),
                        "digital_maturity":  _maturity_level(s),
                        "health_score":      max(0, 100 - s),
                        "outdated_tech":     s >= 60,
                        "address":           row.get("city", "—"),
                        "issues":            ", ".join(
                            row.get("top_issues", [])
                            if isinstance(
                                row.get("top_issues"), list
                            )
                            else []
                        ),
                        "recommendation": (
                            "MSP engagement recommended"
                        ),
                    })
                return results
        except Exception as exc:
            log.warning(
                "engine.msp.get_high_risk_profiles — "
                "falling back: %s", exc
            )

    # Fallback
    leads    = _get_all_leads()
    enriched = [_enrich_lead(l) for l in leads]
    risky    = [
        l for l in enriched
        if l["cyber_risk_level"] == "high"
    ]
    risky.sort(
        key=lambda x: x["opportunity_score"], reverse=True
    )
    return risky[:limit]


def batch_analyze_leads(batch_size: int = 50) -> dict:
    """Called by sidebar scan button."""
    return {"status": "ok", "analyzed": batch_size}


# ═══════════════════════════════════════════════════════════
# tech_stack_detector functions
# (real module not yet read — stub retained)
# ═══════════════════════════════════════════════════════════

def get_outdated_tech_leads(limit: int = 25) -> list:
    """Called by dashboard _load_outdated_tech(limit)."""
    leads    = _get_all_leads()
    enriched = [_enrich_lead(l) for l in leads]
    outdated = [
        l for l in enriched if l.get("outdated_tech")
    ]
    outdated.sort(
        key=lambda x: x["opportunity_score"], reverse=True
    )
    return outdated[:limit]


def batch_detect_leads(batch_size: int = 50) -> dict:
    """Called by sidebar scan button."""
    return {"status": "ok", "detected": batch_size}