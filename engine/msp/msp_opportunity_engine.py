# msp_opportunity_engine.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Master MSP orchestration engine.
#            Combines all MSP analysis into ranked opportunity list.
#            Runs AI evaluation per lead and stores opportunity briefs.
# Dependencies: database.py, engine/ai_adapter.py,
#               engine/msp/digital_maturity_scorer.py,
#               engine/msp/website_health_scanner.py,
#               engine/msp/cyber_risk_analyzer.py,
#               engine/msp/tech_stack_detector.py
# Critical rules observed:
#   - AI via engine/ai_adapter.py only — never ai_client.py directly
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — AI failure does not block opportunity output
#   - No exceptions raised to caller

import json
import logging
from datetime import datetime
from typing import Optional

import database as db
from engine.ai_adapter import ai_is_available, call_ai_json
from engine.msp.digital_maturity_scorer import (
    calculate_maturity_score,
    get_maturity_band,
    get_urgency_band,
    refresh_maturity_score,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Fallback service recommendations when AI is unavailable.
# Keyed by maturity_band.
FALLBACK_SERVICE_MAP = {
    "Critical":  "Full Managed IT + Cyber Essentials Remediation",
    "Poor":      "Managed Security + Infrastructure Audit",
    "Fair":      "Cyber Security Assessment + Website Modernisation",
    "Good":      "Proactive Monitoring + Annual Security Review",
    "Excellent": "Strategic IT Consultancy",
}

# Estimated deal value ranges keyed by maturity_band.
DEAL_VALUE_MAP = {
    "Critical":  "£2,500 – £6,000/month",
    "Poor":      "£1,500 – £3,500/month",
    "Fair":      "£800 – £2,000/month",
    "Good":      "£400 – £1,000/month",
    "Excellent": "£250 – £600/month",
}

# ─────────────────────────────────────────────────────────────
# INTERNAL DATA FETCHERS
# ─────────────────────────────────────────────────────────────

def _fetch_lead_core(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch business name and URL from the leads table.
    Returns dict or None if lead not found.
    """
    try:
        cur = conn.execute(
            """
            SELECT id, business_name, website
            FROM   leads
            WHERE  id = ?
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        log.warning("_fetch_lead_core — lead_id=%s not found", lead_id)
        return None
    except Exception as exc:
        log.error("_fetch_lead_core lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_maturity_record(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch latest maturity record from digital_maturity_scores.
    Returns dict or None.
    """
    try:
        cur = conn.execute(
            """
            SELECT maturity_score, health_score, cyber_score,
                   tech_age_score, maturity_band, urgency_band, scored_at
            FROM   digital_maturity_scores
            WHERE  lead_id = ?
            ORDER  BY scored_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error("_fetch_maturity_record lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_health_detail(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch latest website health scan detail for prompt context.
    Returns dict or None.
    """
    try:
        cur = conn.execute(
            """
            SELECT health_score, has_ssl, ssl_is_expiring,
                   is_mobile_responsive, has_contact_form,
                   is_broken, load_time_seconds
            FROM   website_health_scans
            WHERE  lead_id = ?
            ORDER  BY scanned_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error("_fetch_health_detail lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_cyber_detail(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch latest cyber risk detail for prompt context.
    Returns dict or None.
    """
    try:
        cur = conn.execute(
            """
            SELECT risk_score, severity, has_https, hsts_present,
                   csp_present, spf_present, dmarc_present,
                   exposed_admin, top_issues
            FROM   cyber_risk_profiles
            WHERE  lead_id = ?
            ORDER  BY analyzed_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error("_fetch_cyber_detail lead_id=%s error=%s", lead_id, exc)
        return None


def _fetch_tech_detail(lead_id: int, conn) -> Optional[dict]:
    """
    Fetch latest tech stack detail for prompt context.
    Returns dict or None.
    """
    try:
        cur = conn.execute(
            """
            SELECT cms, analytics_tools, chat_tools,
                   email_tools, jquery_version, php_version,
                   tech_age_score, tech_age_label
            FROM   technology_profiles
            WHERE  lead_id = ?
            ORDER  BY detected_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error("_fetch_tech_detail lead_id=%s error=%s", lead_id, exc)
        return None


# ─────────────────────────────────────────────────────────────
# AI EVALUATION
# ─────────────────────────────────────────────────────────────

def _build_ai_prompt(
    name:           str,
    health_score:   Optional[float],
    cyber_score:    Optional[float],
    tech_stack:     Optional[str],
    maturity_score: int,
    health_detail:  Optional[dict],
    cyber_detail:   Optional[dict],
    tech_detail:    Optional[dict],
) -> str:
    """
    Construct the AI evaluation prompt with all available context.
    Missing sections are labelled 'Not scanned' so AI can still respond.
    """
    # Build human-readable health summary
    if health_detail:
        health_lines = [
            f"  Health Score:        {health_score}",
            f"  SSL Present:         {health_detail.get('has_ssl', 'unknown')}",
            f"  SSL Expiring Soon:   {health_detail.get('ssl_is_expiring', 'unknown')}",
            f"  Mobile Responsive:   {health_detail.get('is_mobile_responsive', 'unknown')}",
            f"  Has Contact Form:    {health_detail.get('has_contact_form', 'unknown')}",
            f"  Site Broken:         {health_detail.get('is_broken', 'unknown')}",
            f"  Load Time (s):       {health_detail.get('load_time_seconds', 'unknown')}",
        ]
        health_block = "\n".join(health_lines)
    else:
        health_block = f"  Health Score: {health_score or 'Not scanned'}"

    # Build human-readable cyber summary
    if cyber_detail:
        top_issues_raw = cyber_detail.get("top_issues") or "[]"
        try:
            top_issues = json.loads(top_issues_raw) if isinstance(top_issues_raw, str) else top_issues_raw
            top_issues_str = ", ".join(top_issues) if top_issues else "None detected"
        except Exception:
            top_issues_str = str(top_issues_raw)

        cyber_lines = [
            f"  Risk Score:          {cyber_detail.get('risk_score', 'unknown')}",
            f"  Severity:            {cyber_detail.get('severity', 'unknown')}",
            f"  HTTPS:               {cyber_detail.get('has_https', 'unknown')}",
            f"  HSTS:                {cyber_detail.get('hsts_present', 'unknown')}",
            f"  CSP Header:          {cyber_detail.get('csp_present', 'unknown')}",
            f"  SPF Record:          {cyber_detail.get('spf_present', 'unknown')}",
            f"  DMARC Record:        {cyber_detail.get('dmarc_present', 'unknown')}",
            f"  Exposed Admin Panel: {cyber_detail.get('exposed_admin', 'unknown')}",
            f"  Top Issues:          {top_issues_str}",
        ]
        cyber_block = "\n".join(cyber_lines)
    else:
        cyber_block = f"  Cyber Score (inverted): {cyber_score or 'Not scanned'}"

    # Build human-readable tech summary
    if tech_detail:
        tech_lines = [
            f"  CMS:                 {tech_detail.get('cms') or 'Unknown'}",
            f"  Analytics Tools:     {tech_detail.get('analytics_tools') or 'None'}",
            f"  Chat Tools:          {tech_detail.get('chat_tools') or 'None'}",
            f"  Email Tools:         {tech_detail.get('email_tools') or 'None'}",
            f"  jQuery Version:      {tech_detail.get('jquery_version') or 'Unknown'}",
            f"  PHP Version:         {tech_detail.get('php_version') or 'Unknown'}",
            f"  Tech Age Score:      {tech_detail.get('tech_age_score', 'unknown')}",
            f"  Tech Age Label:      {tech_detail.get('tech_age_label') or 'Unknown'}",
        ]
        tech_block = "\n".join(tech_lines)
    else:
        tech_block = f"  Tech Stack: {tech_stack or 'Not scanned'}"

    prompt = f"""You are evaluating a business as a prospect for a Managed IT Service Provider (MSP).

Business: {name}
Digital Maturity Score: {maturity_score} / 100

WEBSITE HEALTH:
{health_block}

CYBER SECURITY:
{cyber_block}

TECHNOLOGY STACK:
{tech_block}

Based on all the above data, identify the top 3 IT problems this business has right now
and recommend the most appropriate MSP service to address them.

Return ONLY valid JSON in this exact format with no additional text:
{{
  "problems": ["problem one", "problem two", "problem three"],
  "recommended_service": "name of the MSP service",
  "urgency": "high or medium or low",
  "estimated_value": "monthly value range e.g. £1,000 - £2,500/month"
}}"""

    return prompt


def _run_ai_evaluation(
    lead_id:        int,
    name:           str,
    health_score:   Optional[float],
    cyber_score:    Optional[float],
    maturity_score: int,
    maturity_band:  str,
    health_detail:  Optional[dict],
    cyber_detail:   Optional[dict],
    tech_detail:    Optional[dict],
) -> dict:
    """
    Call AI to evaluate MSP opportunity.
    Falls back to rule-based output if AI is unavailable or fails.

    Returns dict with keys:
        problems (list of str)
        recommended_service (str)
        urgency (str)
        estimated_value (str)
        ai_generated (bool)
    """
    fallback = {
        "problems": _derive_fallback_problems(
            health_score, cyber_score, health_detail, cyber_detail, tech_detail
        ),
        "recommended_service": FALLBACK_SERVICE_MAP.get(
            maturity_band, "Managed IT Services"
        ),
        "urgency": get_urgency_band(maturity_band),
        "estimated_value": DEAL_VALUE_MAP.get(maturity_band, "£500 – £2,000/month"),
        "ai_generated": False,
    }

    if not ai_is_available():
        log.info("_run_ai_evaluation lead_id=%s — AI unavailable, using fallback", lead_id)
        return fallback

    tech_stack_summary = None
    if tech_detail:
        parts = []
        if tech_detail.get("cms"):
            parts.append(tech_detail["cms"])
        if tech_detail.get("tech_age_label"):
            parts.append(tech_detail["tech_age_label"])
        tech_stack_summary = ", ".join(parts) if parts else None

    prompt = _build_ai_prompt(
        name           = name,
        health_score   = health_score,
        cyber_score    = cyber_score,
        tech_stack     = tech_stack_summary,
        maturity_score = maturity_score,
        health_detail  = health_detail,
        cyber_detail   = cyber_detail,
        tech_detail    = tech_detail,
    )

    try:
        result = call_ai_json(prompt, context=f"msp_opportunity_lead_{lead_id}")
        if not result:
            log.warning("_run_ai_evaluation lead_id=%s — AI returned None", lead_id)
            return fallback

        # Validate required keys are present
        problems = result.get("problems")
        service  = result.get("recommended_service")

        if not problems or not service:
            log.warning(
                "_run_ai_evaluation lead_id=%s — AI response missing required keys",
                lead_id,
            )
            return fallback

        # Ensure problems is a list with at least 1 item
        if not isinstance(problems, list) or len(problems) == 0:
            log.warning(
                "_run_ai_evaluation lead_id=%s — AI problems not a valid list",
                lead_id,
            )
            return fallback

        return {
            "problems":             problems[:3],   # Cap at 3
            "recommended_service":  str(service),
            "urgency":              str(result.get("urgency", get_urgency_band(maturity_band))),
            "estimated_value":      str(result.get("estimated_value", DEAL_VALUE_MAP.get(maturity_band, ""))),
            "ai_generated":         True,
        }

    except Exception as exc:
        log.error("_run_ai_evaluation lead_id=%s error=%s", lead_id, exc)
        return fallback


def _derive_fallback_problems(
    health_score:  Optional[float],
    cyber_score:   Optional[float],
    health_detail: Optional[dict],
    cyber_detail:  Optional[dict],
    tech_detail:   Optional[dict],
) -> list:
    """
    Rule-based problem detection when AI is unavailable.
    Returns up to 3 problem strings derived from scan data.
    """
    problems = []

    # Website health problems
    if health_detail:
        if not health_detail.get("has_ssl"):
            problems.append("No SSL certificate — site is insecure and penalised by search engines")
        elif health_detail.get("ssl_is_expiring"):
            problems.append("SSL certificate expiring soon — risk of browser warnings")
        if health_detail.get("is_broken"):
            problems.append("Website is broken or unreachable — losing customers")
        if not health_detail.get("is_mobile_responsive"):
            problems.append("Website is not mobile-responsive — poor user experience")
        load = health_detail.get("load_time_seconds")
        if load and isinstance(load, (int, float)) and load > 4:
            problems.append(f"Slow website load time ({load}s) — high bounce rate risk")
    elif health_score is not None and health_score < 40:
        problems.append(f"Poor website health score ({int(health_score)}/100) — multiple issues detected")

    # Cyber problems
    if cyber_detail:
        if not cyber_detail.get("has_https"):
            problems.append("No HTTPS — data transmitted insecurely")
        if not cyber_detail.get("spf_present"):
            problems.append("No SPF email record — vulnerable to email spoofing and phishing")
        if not cyber_detail.get("dmarc_present"):
            problems.append("No DMARC policy — domain can be impersonated")
        if cyber_detail.get("exposed_admin"):
            problems.append("Admin panel exposed to public internet — critical security risk")
    elif cyber_score is not None and cyber_score < 40:
        problems.append(f"High cyber risk score — multiple security vulnerabilities detected")

    # Tech stack problems
    if tech_detail:
        age_label = tech_detail.get("tech_age_label", "")
        if age_label in ("Legacy", "Very Old", "Outdated"):
            cms = tech_detail.get("cms") or "Website platform"
            problems.append(f"{cms} is running outdated technology — security and performance risk")
        php = tech_detail.get("php_version")
        if php and php.startswith(("5.", "7.0", "7.1", "7.2")):
            problems.append(f"PHP {php} is end-of-life — no security patches available")

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    # Return top 3, pad with generic if fewer than 3
    if len(unique) < 1:
        unique.append("IT infrastructure requires assessment")
    if len(unique) < 2:
        unique.append("No cyber security monitoring detected")
    if len(unique) < 3:
        unique.append("Technology stack may be limiting business growth")

    return unique[:3]


# ─────────────────────────────────────────────────────────────
# OPPORTUNITY STORAGE
# ─────────────────────────────────────────────────────────────

def _save_opportunity_brief(
    lead_id:              int,
    maturity_score:       int,
    maturity_band:        str,
    urgency_band:         str,
    problems:             list,
    recommended_service:  str,
    estimated_value:      str,
    ai_generated:         bool,
    conn,
) -> bool:
    """
    Persist the opportunity brief back to digital_maturity_scores.
    We store serialised problems and the recommended service
    in existing columns via ALTER or in a new table if needed.

    Since the handover spec stores results in digital_maturity_scores,
    we use an UPDATE to add the opportunity fields to the existing row.
    If the columns do not yet exist the UPDATE silently no-ops,
    which is safe — the score is still stored from the scorer.

    Returns True on success.
    """
    try:
        problems_json = json.dumps(problems)
        conn.execute(
            """
            UPDATE digital_maturity_scores
            SET    top_problems         = ?,
                   recommended_service  = ?,
                   estimated_value      = ?,
                   ai_generated         = ?,
                   scored_at            = ?
            WHERE  lead_id = ?
            """,
            (
                problems_json,
                recommended_service,
                estimated_value,
                1 if ai_generated else 0,
                datetime.utcnow().isoformat(),
                lead_id,
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        # Columns may not exist yet — non-fatal, core maturity score is safe
        log.warning(
            "_save_opportunity_brief lead_id=%s — could not save brief fields: %s",
            lead_id, exc,
        )
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def run_full_msp_analysis(lead_id: int) -> dict:
    """
    Run the complete MSP analysis pipeline for one lead.

    Steps:
        1. Fetch lead core data (name, URL)
        2. Ensure maturity score exists — calculate if missing
        3. Fetch all component scan details
        4. Run AI evaluation (falls back gracefully)
        5. Persist opportunity brief
        6. Return structured result dict

    Args:
        lead_id: Primary key of the lead.

    Returns:
        dict with keys:
            lead_id, business_name, website,
            maturity_score, maturity_band, urgency_band,
            health_score, cyber_score, tech_age_score,
            problems (list), recommended_service,
            estimated_value, ai_generated,
            scored_at
        Returns empty dict on fatal error.
    """
    conn = db.get_connection()
    try:
        # Step 1 — Lead core
        lead = _fetch_lead_core(lead_id, conn)
        if not lead:
            return {}

        name = lead.get("business_name") or f"Lead {lead_id}"

        # Step 2 — Maturity score (calculate if not yet scored)
        maturity = _fetch_maturity_record(lead_id, conn)
        if not maturity:
            log.info("run_full_msp_analysis — no maturity record, calculating now lead_id=%s", lead_id)
            conn.close()
            calculate_maturity_score(lead_id)
            conn = db.get_connection()
            maturity = _fetch_maturity_record(lead_id, conn)

        if not maturity:
            log.error("run_full_msp_analysis — could not obtain maturity for lead_id=%s", lead_id)
            return {}

        maturity_score = maturity["maturity_score"]
        maturity_band  = maturity["maturity_band"]
        urgency_band   = maturity["urgency_band"]
        health_score   = maturity.get("health_score")
        cyber_score    = maturity.get("cyber_score")
        tech_score     = maturity.get("tech_age_score")

        # Step 3 — Fetch detail for AI context
        health_detail = _fetch_health_detail(lead_id, conn)
        cyber_detail  = _fetch_cyber_detail(lead_id, conn)
        tech_detail   = _fetch_tech_detail(lead_id, conn)

        # Step 4 — AI evaluation
        evaluation = _run_ai_evaluation(
            lead_id        = lead_id,
            name           = name,
            health_score   = health_score,
            cyber_score    = cyber_score,
            maturity_score = maturity_score,
            maturity_band  = maturity_band,
            health_detail  = health_detail,
            cyber_detail   = cyber_detail,
            tech_detail    = tech_detail,
        )

        # Step 5 — Persist
        _save_opportunity_brief(
            lead_id             = lead_id,
            maturity_score      = maturity_score,
            maturity_band       = maturity_band,
            urgency_band        = urgency_band,
            problems            = evaluation["problems"],
            recommended_service = evaluation["recommended_service"],
            estimated_value     = evaluation["estimated_value"],
            ai_generated        = evaluation["ai_generated"],
            conn                = conn,
        )

        # Step 6 — Return full result
        result = {
            "lead_id":              lead_id,
            "business_name":        name,
            "website":              lead.get("website") or "",
            "maturity_score":       maturity_score,
            "maturity_band":        maturity_band,
            "urgency_band":         urgency_band,
            "health_score":         health_score,
            "cyber_score":          cyber_score,
            "tech_age_score":       tech_score,
            "problems":             evaluation["problems"],
            "recommended_service":  evaluation["recommended_service"],
            "estimated_value":      evaluation["estimated_value"],
            "ai_generated":         evaluation["ai_generated"],
            "scored_at":            maturity.get("scored_at") or datetime.utcnow().isoformat(),
        }

        log.info(
            "run_full_msp_analysis complete lead_id=%s band=%s urgency=%s",
            lead_id, maturity_band, urgency_band,
        )
        return result

    except Exception as exc:
        log.error("run_full_msp_analysis lead_id=%s error=%s", lead_id, exc)
        return {}
    finally:
        conn.close()


def get_top_msp_opportunities(limit: int = 25) -> list:
    """
    Return the highest-priority MSP opportunities.

    Ordered by maturity_score ascending (worst digital maturity first),
    then urgency_band (high before medium before low).

    Args:
        limit: Maximum number of results to return.

    Returns:
        List of dicts. Each dict contains lead summary fields
        from digital_maturity_scores joined to leads.
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  l.id                    AS lead_id,
                    l.business_name,
                    l.website,
                    dms.maturity_score,
                    dms.maturity_band,
                    dms.urgency_band,
                    dms.health_score,
                    dms.cyber_score,
                    dms.tech_age_score,
                    dms.scored_at
            FROM    digital_maturity_scores dms
            JOIN    leads l ON l.id = dms.lead_id
            ORDER   BY
                CASE dms.urgency_band
                    WHEN 'high'   THEN 1
                    WHEN 'medium' THEN 2
                    ELSE               3
                END ASC,
                dms.maturity_score ASC
            LIMIT   ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        log.info("get_top_msp_opportunities returned %s rows", len(rows))
        return rows
    except Exception as exc:
        log.error("get_top_msp_opportunities error=%s", exc)
        return []
    finally:
        conn.close()


def generate_opportunity_brief(lead_id: int) -> dict:
    """
    Generate or retrieve the opportunity brief for one lead.

    If a maturity record with opportunity fields already exists,
    returns it directly without re-running AI.
    If not, runs full analysis.

    Args:
        lead_id: Primary key of the lead.

    Returns:
        Full opportunity dict (same shape as run_full_msp_analysis).
        Empty dict on error.
    """
    conn = db.get_connection()
    try:
        # Check for cached brief
        cur = conn.execute(
            """
            SELECT  dms.maturity_score, dms.maturity_band,
                    dms.urgency_band, dms.health_score,
                    dms.cyber_score, dms.tech_age_score,
                    dms.scored_at,
                    l.business_name, l.website
            FROM    digital_maturity_scores dms
            JOIN    leads l ON l.id = dms.lead_id
            WHERE   dms.lead_id = ?
            ORDER   BY dms.scored_at DESC
            LIMIT   1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
    except Exception as exc:
        log.error("generate_opportunity_brief fetch error lead_id=%s: %s", lead_id, exc)
        row = None
    finally:
        conn.close()

    if row:
        log.info("generate_opportunity_brief — returning cached brief lead_id=%s", lead_id)
        # Return a minimal dict; dashboard can call run_full_msp_analysis
        # for deeper re-analysis if needed
        return dict(row)

    log.info("generate_opportunity_brief — no cached brief, running full analysis lead_id=%s", lead_id)
    return run_full_msp_analysis(lead_id)


def get_recommended_offer(lead_id: int) -> str:
    """
    Return a single-sentence recommended MSP offer for a lead.

    Args:
        lead_id: Primary key of the lead.

    Returns:
        Recommendation string, or empty string on failure.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT maturity_band
            FROM   digital_maturity_scores
            WHERE  lead_id = ?
            ORDER  BY scored_at DESC
            LIMIT  1
            """,
            (lead_id,),
        )
        row = cur.fetchone()
        if not row:
            log.warning("get_recommended_offer — no maturity record lead_id=%s", lead_id)
            return ""
        band = row["maturity_band"]
        return FALLBACK_SERVICE_MAP.get(band, "Managed IT Services")
    except Exception as exc:
        log.error("get_recommended_offer lead_id=%s error=%s", lead_id, exc)
        return ""
    finally:
        conn.close()


def refresh_all_opportunities(batch_size: int = 25) -> dict:
    """
    Re-run full MSP analysis for every lead that has a maturity score.

    This refreshes the maturity score AND regenerates the AI opportunity
    brief for each lead. Long-running — intended for scheduled/background use.

    Args:
        batch_size: Number of leads to process per batch log line.

    Returns:
        dict with keys:
            processed (int)
            succeeded (int)
            failed    (int)
    """
    results = {"processed": 0, "succeeded": 0, "failed": 0}

    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT DISTINCT lead_id FROM digital_maturity_scores"
        )
        lead_ids = [row["lead_id"] for row in cur.fetchall()]
    except Exception as exc:
        log.error("refresh_all_opportunities — fetch lead_ids error=%s", exc)
        conn.close()
        return results
    finally:
        conn.close()

    log.info("refresh_all_opportunities — %s leads to process", len(lead_ids))

    for i, lead_id in enumerate(lead_ids, start=1):
        results["processed"] += 1
        try:
            # Refresh maturity score first
            refresh_maturity_score(lead_id)
            # Then re-run full AI analysis
            result = run_full_msp_analysis(lead_id)
            if result:
                results["succeeded"] += 1
            else:
                results["failed"] += 1
        except Exception as exc:
            log.error("refresh_all_opportunities lead_id=%s error=%s", lead_id, exc)
            results["failed"] += 1

        if i % batch_size == 0:
            log.info(
                "refresh_all_opportunities — %s/%s processed",
                i, len(lead_ids),
            )

    log.info("refresh_all_opportunities complete — %s", results)
    return results