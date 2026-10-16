"""
KNCS LeadGen Pro — engine/job_manager.py — v10.3-DEBUG

Audit fixes applied (v10.2 → v10.3):
  FIX-MARKET-01 (CRITICAL) run_scrape_job(): market field is now
            derived from country for every lead that doesn't already
            have one set. Previously only b2bhint_scraper.py and
            companies_house_scraper.py set market internally (hardcoded
            "Nigeria" / "United Kingdom" respectively). Generic scrapers
            (google_maps, yelp, yellowpages) left market="" for every
            lead, which caused all US leads (and any other country) to
            show as "Unknown" in dashboard Top Markets and to be
            invisible to any market-based filtering.

            New _derive_market(country) helper normalises free-text
            country values into canonical market labels: "United
            States", "United Kingdom", "Nigeria", or a title-cased
            fallback for any other country. Applied in the same loop
            that already normalises city/country before dedup, so it
            covers every source uniformly. Existing market values set
            by individual scrapers are preserved (not overwritten).

Audit fixes applied (v10.1 → v10.2):
  BUG-036  (CRITICAL) run_scrape_job(): complete_job() wrapped in its own
            try/except. except BaseException covers all fatal paths.

  BUG-037  (HIGH) start_scrape_job(): get_running_job() called under
            jobs_lock before any thread is spawned.

  BUG-038  (HIGH) create_job(): initial status STATUS_RUNNING — no gap.

  NEW-JM-01 (HIGH) Running-job gate under jobs_lock.
  NEW-JM-02 (HIGH) Bare-SQL fallback in BaseException handler.
  NEW-JM-03–06 (MEDIUM) All print() → logger.
  NEW-JM-07 (HIGH) City normalised to effective_city before dedup.
  NEW-JM-08 (LOW) do_scripts flag logged at DEBUG.
  NEW-JM-09 (MEDIUM) Duration-fetch cursor explicitly closed.
  NEW-JM-10 (CRITICAL) Duplicate gate resolved by NEW-JM-01.

  FIX-ASYNC-01 (CRITICAL) google_maps dispatch calls scrape_sync()
            not the async scrape_google_maps() directly.

  FIX-ENRICH-01 (HIGH) Enrichment block now logs exactly what fields
            were gained per lead — email, phone, decision_maker_name,
            website — visible in terminal and log file.

  FIX-ENRICH-02 (HIGH) run_full_enrichment_pipeline() added —
            orchestrates enricher.py → decision_maker_finder →
            companies_house in the correct order with full logging.

  FIX-COMPLETE-01 (retained): complete_job() UPDATE unconditional.
  FIX-SCHEMA-01 (retained): column names match database.py v2.3 schema.

  TEMP-DEBUG-01 (TEMPORARY — remove after diagnosis): plain print()
            added in the per-lead insert block to show the exact
            insert_lead() result for every lead, bypassing logging
            configuration entirely. Search for "INSERT-DEBUG" to find
            and remove this line once the skip-cause is confirmed.
"""

import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from database import get_connection, insert_lead

logger = logging.getLogger(__name__)

__all__ = [
    "create_job", "get_job", "get_running_job", "get_recent_jobs",
    "get_active_job_id", "any_job_running", "stop_job", "is_job_stopped",
    "start_scrape_job", "update_job_progress", "complete_job",
    "get_job_status_summary",
    "STATUS_PENDING", "STATUS_RUNNING", "STATUS_DONE",
    "STATUS_FAILED", "STATUS_STOPPED",
]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE    = "done"
STATUS_FAILED  = "failed"
STATUS_STOPPED = "stopped"

active_jobs: Dict[int, threading.Thread] = {}
jobs_lock = threading.Lock()


# ─────────────────────────────────────────────────────────
# Job CRUD
# ─────────────────────────────────────────────────────────

def create_job(
    keyword: str,
    city: str,
    source: str,
    country: str = "United Kingdom",
) -> Optional[int]:
    """
    Insert a new scrape_jobs row with STATUS_RUNNING so there is never a
    pending→running gap visible to other threads (BUG-038).
    """
    conn = get_connection()
    if not conn:
        return None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ts  = time.time()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO scrape_jobs (
                keyword, city, country, source,
                status, progress, progress_pct,
                leads_found, inserted, skipped, updated,
                message, started_at, started_at_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                keyword, city, country, source,
                STATUS_RUNNING, 0, 0.0,
                0, 0, 0, 0,
                "Starting...", now_iso, now_ts,
            ),
        )
        job_id = cur.lastrowid
        conn.commit()
        return job_id
    except Exception as exc:
        logger.error("create_job error: %s", exc, exc_info=True)
        return None
    finally:
        conn.close()


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    if not job_id:
        return None
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM scrape_jobs WHERE id = ?",
            (job_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_running_job() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM scrape_jobs
            WHERE  status IN ('pending', 'running')
            ORDER  BY id DESC
            LIMIT  1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_recent_jobs(limit: int = 12) -> List[Dict[str, Any]]:
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM scrape_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_active_job_id() -> Optional[int]:
    job = get_running_job()
    return job["id"] if job else None


def any_job_running() -> bool:
    return get_running_job() is not None


def stop_job(job_id: int) -> bool:
    if not job_id:
        return False
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE scrape_jobs SET status = ? WHERE id = ?",
            (STATUS_STOPPED, job_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def is_job_stopped(job_id: int) -> bool:
    job = get_job(job_id)
    return (
        job.get("status", "").lower() == STATUS_STOPPED
    ) if job else True


# ─────────────────────────────────────────────────────────
# Progress + Completion
# ─────────────────────────────────────────────────────────

def update_job_progress(
    job_id: int,
    progress: int,
    leads_found: int,
    message: str = "",
    status: str = STATUS_RUNNING,
    inserted: int = 0,
    skipped: int = 0,
    updated: int = 0,
) -> None:
    conn = get_connection()
    if not conn:
        return
    try:
        pct = min(max(float(progress), 0.0), 100.0)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE scrape_jobs SET
                status       = ?,
                progress     = ?,
                progress_pct = ?,
                leads_found  = ?,
                inserted     = ?,
                skipped      = ?,
                updated      = ?,
                message      = ?
            WHERE id = ?
            """,
            (
                status, int(pct), pct, leads_found,
                inserted, skipped, updated,
                (message or "")[:200], job_id,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "update_job_progress error job=%s: %s", job_id, exc,
        )
    finally:
        conn.close()


def complete_job(
    job_id: int,
    leads_found: int,
    error: str = None,
    status: str = None,
    inserted: int = 0,
    skipped: int = 0,
    updated: int = 0,
) -> None:
    """
    Mark a job as terminal.
    FIX-COMPLETE-01: UPDATE has no WHERE status condition — unconditional.
    NEW-JM-09: duration-fetch cursor explicitly closed.
    """
    conn = get_connection()
    if not conn:
        with jobs_lock:
            active_jobs.pop(job_id, None)
        return
    try:
        # Duration fetch
        duration: Optional[int] = None
        try:
            dur_cur = conn.cursor()
            dur_cur.execute(
                "SELECT started_at_ts FROM scrape_jobs WHERE id = ?",
                (job_id,),
            )
            row = dur_cur.fetchone()
            if row and row[0]:
                duration = int(time.time() - float(row[0]))
            dur_cur.close()
        except Exception:
            pass

        # Final status
        if not status:
            if (
                error
                and leads_found == 0
                and inserted == 0
                and skipped == 0
            ):
                final_status = STATUS_FAILED
            else:
                final_status = STATUS_DONE
        else:
            final_status = status

        now_iso      = datetime.now(timezone.utc).isoformat()
        message_text = (error or "")[:500]

        cur = conn.cursor()
        cur.execute(
            """
            UPDATE scrape_jobs SET
                status           = ?,
                progress         = 100,
                progress_pct     = 100.0,
                leads_found      = ?,
                inserted         = ?,
                skipped          = ?,
                updated          = ?,
                message          = ?,
                finished_at      = ?,
                duration_seconds = ?
            WHERE id = ?
            """,
            (
                final_status, leads_found,
                inserted, skipped, updated,
                message_text, now_iso, duration, job_id,
            ),
        )
        conn.commit()

        logger.info(
            "Job %s complete | status=%s found=%d "
            "inserted=%d skipped=%d updated=%d duration=%ss",
            job_id, final_status, leads_found,
            inserted, skipped, updated, duration,
        )
    except Exception as exc:
        logger.error(
            "complete_job error job=%s: %s",
            job_id, exc, exc_info=True,
        )
    finally:
        conn.close()
        with jobs_lock:
            active_jobs.pop(job_id, None)


def get_job_status_summary(job_id: Optional[int]) -> dict:
    empty = {
        "found":          False,
        "status":         "unknown",
        "progress":       0,
        "progress_pct":   0.0,
        "leads":          0,
        "leads_inserted": 0,
        "leads_skipped":  0,
        "leads_updated":  0,
        "message":        "No job running",
        "is_done":        False,
        "is_running":     False,
    }
    if not job_id:
        return empty
    job = get_job(job_id)
    if not job:
        empty["message"] = "Job not found"
        return empty
    status = job.get("status", "unknown")
    prog   = min(max(int(job.get("progress", 0) or 0), 0), 100)
    return {
        "found":          True,
        "status":         status,
        "progress":       prog,
        "progress_pct":   prog / 100.0,
        "leads":          int(job.get("leads_found",  0) or 0),
        "leads_inserted": int(job.get("inserted",     0) or 0),
        "leads_skipped":  int(job.get("skipped",      0) or 0),
        "leads_updated":  int(job.get("updated",      0) or 0),
        "message":        job.get("message", "") or "",
        "is_done":        status in ("done", "failed", "stopped"),
        "is_running":     status in ("pending", "running"),
    }


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def run_full_scoring(lead: dict, lead_id: int) -> None:
    """Fire-and-forget scoring — failure is non-fatal."""
    try:
        from engine.scorer import score_lead
        score_lead(lead_id)
    except Exception as exc:
        logger.debug(
            "Scoring skipped for lead %s: %s", lead_id, exc,
        )


def get_lead_id_by_name(
    business_name: str, city: str = ""
) -> Optional[int]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM leads
            WHERE  LOWER(business_name) = ?
            AND    LOWER(city)          = ?
            LIMIT  1
            """,
            (
                business_name.strip().lower(),
                city.strip().lower(),
            ),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _derive_market(country: str) -> str:
    """
    FIX-MARKET-01: Normalise a free-text country string into a
    canonical market label.

    This ensures every lead gets a non-blank market value regardless
    of which scraper produced it. Previously only b2bhint_scraper.py
    (hardcoded "Nigeria") and companies_house_scraper.py (hardcoded
    "United Kingdom") set market internally — every other source
    (google_maps, yelp, yellowpages) left market="" for every lead,
    which is why US leads (and any other country) showed as "Unknown"
    in the dashboard's Top Markets panel.

    Called from run_scrape_job() only when a lead does not already
    have a market value set — existing scraper-set values are always
    preserved, never overwritten.
    """
    if not country:
        return "Unknown"
    c = country.strip().lower()
    if c in ("us", "usa", "united states", "united states of america"):
        return "United States"
    if c in (
        "uk", "gb", "united kingdom", "great britain",
        "england", "scotland", "wales", "northern ireland",
    ):
        return "United Kingdom"
    if c in ("nigeria", "ng"):
        return "Nigeria"
    # Fallback: preserve whatever country was given, title-cased,
    # rather than losing it as blank/Unknown.
    return country.strip().title()


# ─────────────────────────────────────────────────────────
# FIX-ENRICH-02: Full enrichment pipeline orchestrator
# ─────────────────────────────────────────────────────────

def run_full_enrichment_pipeline(
    lead: dict,
    job_id: int,
    find_email: bool = True,
    find_phone: bool = True,
) -> dict:
    """
    FIX-ENRICH-02: Orchestrates the complete enrichment sequence:

      Stage 1 — enricher.py (website crawler)
        Finds: email, phone, decision maker from website pages,
               social links, JSON-LD structured data

      Stage 2 — decision_maker_finder.py
        Finds: owner/director name via Google + LinkedIn search
        Only runs if Stage 1 did not find a decision maker

      Stage 3 — companies_house.py direct API
        Finds: official director name from Companies House register
        Only runs for UK businesses when Stage 1+2 found no DM

      Stage 4 — data quality pass
        Ensures decision_maker and decision_maker_name are consistent

    All stages are wrapped in try/except — a failure in any stage
    does not prevent later stages or DB insertion.

    Logs exactly which fields were gained at each stage so you can
    see in the terminal what the enrichment is finding.
    """
    name    = lead.get("business_name", "unknown")
    country = (lead.get("country") or "").lower()
    is_uk   = any(
        x in country
        for x in [
            "uk", "united kingdom", "england",
            "wales", "scotland", "great britain", "gb",
        ]
    )

    # Snapshot BEFORE enrichment so we can log what was gained
    def _snapshot(d: dict) -> dict:
        return {
            "email":              d.get("email", ""),
            "phone":              d.get("phone", ""),
            "website":            d.get("website", ""),
            "decision_maker":     d.get("decision_maker_name", ""),
            "social_instagram":   d.get("social_instagram_url", ""),
            "social_facebook":    d.get("social_facebook_url", ""),
            "social_linkedin":    d.get("social_linkedin_url", ""),
        }

    def _log_gains(stage: str, before: dict, after: dict) -> None:
        gained = {
            k: after[k]
            for k in before
            if after.get(k) and not before.get(k)
        }
        if gained:
            logger.info(
                "Job %s | %s | '%s' gained: %s",
                job_id, stage, name,
                {k: v[:60] if isinstance(v, str) else v
                 for k, v in gained.items()},
            )
        else:
            logger.debug(
                "Job %s | %s | '%s' — nothing new found",
                job_id, stage, name,
            )

    # ── Stage 1: enricher.py (website crawler + search) ─────────────
    before_s1 = _snapshot(lead)
    try:
        from engine.enricher import enrich_lead
        lead = enrich_lead(
            lead,
            find_email=find_email,
            find_phone=find_phone,
        )
        _log_gains("Stage1-WebCrawler", before_s1, _snapshot(lead))
    except Exception as exc:
        logger.warning(
            "Job %s Stage1 enrichment failed for '%s': %s",
            job_id, name, exc,
        )

    # ── Stage 2: decision_maker_finder (Google/LinkedIn search) ─────
    if not lead.get("decision_maker_name", "").strip():
        before_s2 = _snapshot(lead)
        try:
            from engine.decision_maker_finder import (
                DecisionMakerFinder,
            )
            finder    = DecisionMakerFinder()
            dm_result = finder.find(
                business_name=lead.get("business_name", ""),
                website=lead.get("website", ""),
                city=lead.get("city", ""),
            )
            if dm_result:
                dm_name = (
                    dm_result.get("decision_maker_name")
                    or dm_result.get("decision_maker")
                    or ""
                )
                if dm_name:
                    lead["decision_maker_name"] = dm_name
                    lead["decision_maker"]       = dm_name
                    lead["dm_source"]            = "dm_finder"
            _log_gains("Stage2-DMFinder", before_s2, _snapshot(lead))
        except ImportError:
            logger.debug(
                "Job %s Stage2: decision_maker_finder not available",
                job_id,
            )
        except Exception as exc:
            logger.warning(
                "Job %s Stage2 DM finder failed for '%s': %s",
                job_id, name, exc,
            )

    # ── Stage 3: Companies House API (UK only) ───────────────────────
    if is_uk and not lead.get("decision_maker_name", "").strip():
        before_s3 = _snapshot(lead)
        try:
            ch_key = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
            if ch_key:
                import requests as _req

                # Search for the company
                search_resp = _req.get(
                    "https://api.company-information.service.gov.uk"
                    "/search/companies",
                    params={
                        "q":             lead.get("business_name", ""),
                        "items_per_page": 5,
                    },
                    auth=(ch_key, ""),
                    timeout=10,
                )
                if search_resp.status_code == 200:
                    companies = (
                        search_resp.json().get("items", [])
                    )
                    company_number = ""
                    for co in companies:
                        if co.get("company_status", "") == "active":
                            company_number = co.get(
                                "company_number", ""
                            )
                            break
                    if not company_number and companies:
                        company_number = companies[0].get(
                            "company_number", ""
                        )

                    if company_number:
                        # Get officers
                        off_resp = _req.get(
                            f"https://api.company-information"
                            f".service.gov.uk/company/"
                            f"{company_number}/officers",
                            auth=(ch_key, ""),
                            timeout=10,
                        )
                        if off_resp.status_code == 200:
                            officers = (
                                off_resp.json().get("items", [])
                            )
                            director_roles = {
                                "director",
                                "managing director",
                                "chief executive officer",
                                "proprietor",
                                "nominated individual",
                                "owner",
                            }
                            for officer in officers:
                                # Skip resigned officers
                                if officer.get("resigned_on"):
                                    continue
                                role = officer.get(
                                    "officer_role", ""
                                ).lower().replace("-", " ")
                                if any(
                                    r in role
                                    for r in director_roles
                                ):
                                    raw_name = officer.get(
                                        "name", ""
                                    ).strip()
                                    if raw_name:
                                        # CH stores "SURNAME, Forename"
                                        if "," in raw_name:
                                            parts = raw_name.split(
                                                ",", 1
                                            )
                                            dm_name = (
                                                parts[1].strip().title()
                                                + " "
                                                + parts[0].strip().title()
                                            )
                                        else:
                                            dm_name = raw_name.title()
                                        lead["decision_maker_name"] = (
                                            dm_name
                                        )
                                        lead["decision_maker"]       = (
                                            dm_name
                                        )
                                        lead["company_number"]       = (
                                            company_number
                                        )
                                        lead["dm_source"] = (
                                            "companies_house_api"
                                        )
                                        break

            _log_gains(
                "Stage3-CompaniesHouse", before_s3, _snapshot(lead),
            )
        except Exception as exc:
            logger.warning(
                "Job %s Stage3 Companies House failed for '%s': %s",
                job_id, name, exc,
            )

    # ── Stage 4: Data quality — ensure both DM keys consistent ──────
    dm = (
        lead.get("decision_maker_name")
        or lead.get("decision_maker")
        or ""
    )
    if dm:
        lead["decision_maker_name"] = dm
        lead["decision_maker"]      = dm

    # ── Final summary log ────────────────────────────────────────────
    logger.info(
        "Job %s | Enrichment complete | '%s' | "
        "email=%s | phone=%s | dm=%s | website=%s",
        job_id,
        name,
        "✓" if lead.get("email") else "✗",
        "✓" if lead.get("phone") else "✗",
        "✓" if lead.get("decision_maker_name") else "✗",
        "✓" if lead.get("website") else "✗",
    )

    return lead


# ─────────────────────────────────────────────────────────
# Background scrape thread
# ─────────────────────────────────────────────────────────

def run_scrape_job(
    job_id: int,
    keyword: str,
    location: str,
    source: str,
    max_results: int,
    max_pages: int,
    do_enrich: bool,
    do_email: bool,
    do_scripts: bool,
    force_restart: bool,
    city: str,
    country: str,
) -> None:
    """
    Worker function executed inside the daemon scrape thread.

    FIX-ASYNC-01: google_maps calls scrape_sync() — the correct sync
    entry point. Never calls async scrape_google_maps() directly.

    FIX-ENRICH-01/02: Enrichment now uses run_full_enrichment_pipeline()
    which runs website crawler → DM finder → Companies House in sequence,
    logging exactly what each stage finds per lead.

    FIX-MARKET-01: market field is derived from country for every lead
    that doesn't already have one set (see city/country/market
    normalisation block below, before dedup).
    """
    if do_scripts:
        logger.debug(
            "Job %s: do_scripts=True — not yet implemented, ignored.",
            job_id,
        )

    leads_found = 0
    inserted    = 0
    skipped     = 0
    updated     = 0
    all_leads: list = []

    try:
        update_job_progress(job_id, 0, 0, "Starting...")

        # ── Source dispatch ──────────────────────────────────────────
        if source == "google_maps":
            update_job_progress(
                job_id, 5, 0, "Starting Google Maps...",
            )
            if is_job_stopped(job_id):
                complete_job(job_id, 0, status=STATUS_STOPPED)
                return
            try:
                # FIX-ASYNC-01: use sync wrapper, not async function
                from engine.scraper import scrape_sync
                raw = scrape_sync(
                    keyword=keyword,
                    location=location,
                    max_results=max_results,
                ) or []
                all_leads.extend(raw)
                leads_found = len(all_leads)
                update_job_progress(
                    job_id, 20, leads_found,
                    f"Google Maps: {len(raw)} found",
                )
            except Exception as exc:
                logger.error(
                    "Job %s Google Maps error: %s",
                    job_id, exc, exc_info=True,
                )

        elif source == "b2bhint":
            update_job_progress(
                job_id, 25, 0, "Starting B2BHint...",
            )
            if is_job_stopped(job_id):
                complete_job(job_id, 0, status=STATUS_STOPPED)
                return
            try:
                from engine.b2bhint_scraper import scrape_b2bhint
                raw = scrape_b2bhint(
                    keyword=keyword,
                    location=location,
                    max_results=max_results,
                    max_pages=max_pages,
                    force_restart=force_restart,
                    country=country,
                ) or []
                all_leads.extend(raw)
                leads_found = len(all_leads)
                update_job_progress(
                    job_id, 40, leads_found,
                    f"B2BHint: {len(raw)} found",
                )
            except Exception as exc:
                logger.error(
                    "Job %s B2BHint error: %s",
                    job_id, exc, exc_info=True,
                )

        elif source == "companies_house":
            update_job_progress(
                job_id, 20, 0,
                "Connecting to Companies House UK...",
            )
            if is_job_stopped(job_id):
                complete_job(job_id, 0, status=STATUS_STOPPED)
                return
            try:
                from engine.companies_house_scraper import (
                    scrape_companies_house,
                )
                raw = scrape_companies_house(
                    keyword=keyword,
                    location=location,
                    max_results=max_results,
                    get_details=True,
                    get_officers=True,
                    active_only=True,
                ) or []
                all_leads.extend(raw)
                leads_found = len(all_leads)
                update_job_progress(
                    job_id, 60, leads_found,
                    f"Companies House: {len(raw)} found",
                )
            except Exception as exc:
                logger.error(
                    "Job %s Companies House error: %s",
                    job_id, exc, exc_info=True,
                )

        elif source == "yelp":
            update_job_progress(
                job_id, 30, 0, "Starting Yelp...",
            )
            if is_job_stopped(job_id):
                complete_job(job_id, 0, status=STATUS_STOPPED)
                return
            try:
                from engine.yelp_scraper import scrape_yelp
                raw = scrape_yelp(
                    keyword=keyword,
                    location=location,
                    max_results=max_results,
                ) or []
                all_leads.extend(raw)
                leads_found = len(all_leads)
                update_job_progress(
                    job_id, 50, leads_found,
                    f"Yelp: {len(raw)} found",
                )
            except Exception as exc:
                logger.error(
                    "Job %s Yelp error: %s",
                    job_id, exc, exc_info=True,
                )

        elif source == "yellowpages":
            update_job_progress(
                job_id, 30, 0, "Starting YellowPages...",
            )
            if is_job_stopped(job_id):
                complete_job(job_id, 0, status=STATUS_STOPPED)
                return
            try:
                from engine.yellowpages_scraper import (
                    scrape_yellowpages,
                )
                raw = scrape_yellowpages(
                    keyword=keyword,
                    location=location,
                    max_results=max_results,
                ) or []
                all_leads.extend(raw)
                leads_found = len(all_leads)
                update_job_progress(
                    job_id, 50, leads_found,
                    f"YellowPages: {len(raw)} found",
                )
            except Exception as exc:
                logger.error(
                    "Job %s YellowPages error: %s",
                    job_id, exc, exc_info=True,
                )

        else:
            logger.warning(
                "Job %s: unknown source '%s' — no leads collected.",
                job_id, source,
            )

        # ── Early exit on empty ──────────────────────────────────────
        if not all_leads:
            complete_job(job_id, 0, status=STATUS_DONE)
            return

        # ── City / country / market normalisation BEFORE dedup ──────
        # NEW-JM-07 (city fallback) + FIX-MARKET-01 (market derived
        # from country for any lead that doesn't already have one set
        # — preserves scraper-set values like b2bhint's "Nigeria" or
        # companies_house's "United Kingdom", only fills in blanks).
        for lead in all_leads:
            if not lead.get("city"):
                lead["city"] = city
            if not lead.get("country"):
                lead["country"] = country
            if not lead.get("market") or not str(lead.get("market")).strip():
                lead["market"] = _derive_market(
                    lead.get("country", country)
                )

        # ── In-memory dedup ──────────────────────────────────────────
        unique: list = []
        seen:   set  = set()
        for lead in all_leads:
            k = (
                lead.get("business_name", "").strip().lower(),
                lead.get("city", "").strip().lower(),
            )
            if k[0] and k not in seen:
                seen.add(k)
                unique.append(lead)

        total       = len(unique)
        leads_found = total
        update_job_progress(
            job_id, 45, leads_found,
            f"Processing {total} leads...",
            inserted=inserted, skipped=skipped, updated=updated,
        )

        # ── Per-lead processing ──────────────────────────────────────
        for i, lead in enumerate(unique):
            if is_job_stopped(job_id):
                complete_job(
                    job_id, leads_found, status=STATUS_STOPPED,
                    inserted=inserted, skipped=skipped,
                    updated=updated,
                )
                return

            name     = lead.get("business_name", "...")
            progress = 45 + int((i / max(total, 1)) * 50)

            # Industry fallback
            if (
                not lead.get("industry")
                or lead["industry"] in ("", "unknown")
            ):
                lead["industry"] = keyword

            # ── FIX-ENRICH-01/02: Full enrichment pipeline ───────────
            if do_enrich:
                update_job_progress(
                    job_id, progress, leads_found,
                    f"Enriching: {name[:35]}...",
                    status=STATUS_RUNNING,
                    inserted=inserted, skipped=skipped,
                    updated=updated,
                )
                lead = run_full_enrichment_pipeline(
                    lead,
                    job_id=job_id,
                    find_email=do_email,
                    find_phone=True,
                )

            # ── DB insert ────────────────────────────────────────────
            ok            = False
            saved_id      = None
            insert_result = "skipped"
            try:
                res = insert_lead(lead)
                if isinstance(res, tuple) and len(res) == 3:
                    ok, insert_result, saved_id = res
                elif isinstance(res, tuple) and len(res) == 2:
                    ok, insert_result = res
                else:
                    ok            = bool(res)
                    insert_result = "inserted" if ok else "skipped"

                # TEMP-DEBUG-01: plain print() bypasses logging config
                # entirely — remove this line once skip-cause confirmed
                print(f"[INSERT-DEBUG] name={name!r} city={lead.get('city')!r} country={lead.get('country')!r} phone={lead.get('phone')!r} place_id={lead.get('place_id')!r} maps_url={lead.get('maps_url')!r} -> ok={ok} result={insert_result}")

                if ok:
                    if str(insert_result).lower() == "updated":
                        updated  += 1
                    else:
                        inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.warning(
                    "Job %s insert_lead failed for '%s': %s",
                    job_id, name, exc,
                )
                print(f"[INSERT-DEBUG] EXCEPTION for name={name!r}: {exc}")
                skipped += 1

            # ── Scoring ──────────────────────────────────────────────
            if ok:
                if saved_id is None:
                    saved_id = get_lead_id_by_name(
                        name, lead.get("city", city),
                    )
                if saved_id:
                    run_full_scoring(lead, saved_id)

            update_job_progress(
                job_id, progress, leads_found,
                f"{'Saved' if ok else 'Skipped'}: {name[:40]}",
                status=STATUS_RUNNING,
                inserted=inserted, skipped=skipped,
                updated=updated,
            )

        # ── Normal completion ────────────────────────────────────────
        complete_job(
            job_id, leads_found, status=STATUS_DONE,
            inserted=inserted, skipped=skipped, updated=updated,
        )

    except BaseException as exc:
        # BUG-036 / NEW-JM-02: complete_job() wrapped — active_jobs.pop()
        # always fires even if DB is unreachable.
        logger.error(
            "Job %s fatal exception: %s",
            job_id, exc, exc_info=True,
        )
        final = STATUS_DONE if leads_found > 0 else STATUS_FAILED
        try:
            complete_job(
                job_id, leads_found,
                error=str(exc)[:500], status=final,
                inserted=inserted, skipped=skipped,
                updated=updated,
            )
        except Exception as inner:
            logger.error(
                "Job %s complete_job() also failed (%s); "
                "bare-SQL fallback.",
                job_id, inner,
            )
            try:
                conn = get_connection()
                if conn:
                    try:
                        conn.execute(
                            "UPDATE scrape_jobs "
                            "SET status=?, message=? "
                            "WHERE id=?",
                            (final, str(exc)[:200], job_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                pass
            with jobs_lock:
                active_jobs.pop(job_id, None)


# ─────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────

def start_scrape_job(
    keyword: str,
    location: str,
    source: str,
    max_results: int    = 20,
    max_pages: int      = 5,
    do_enrich: bool     = True,
    do_email: bool      = True,
    do_scripts: bool    = False,
    force_restart: bool = False,
    city: str           = "",
    country: str        = "",
) -> Optional[int]:
    """
    Create and launch a background scrape thread.
    get_running_job() checked under jobs_lock — no duplicate threads.
    """
    with jobs_lock:
        existing = get_running_job()
        if existing:
            logger.warning(
                "start_scrape_job: job %s already running "
                "(status=%s) — refusing duplicate.",
                existing.get("id"), existing.get("status"),
            )
            return None

        effective_city = city or location.split(",")[0].strip()

        job_id = create_job(
            keyword=keyword,
            city=effective_city,
            source=source,
            country=country or "United Kingdom",
        )
        if not job_id:
            return None

        t = threading.Thread(
            target=run_scrape_job,
            args=(
                job_id, keyword, location, source,
                max_results, max_pages, do_enrich,
                do_email, do_scripts, force_restart,
                effective_city, country,
            ),
            daemon=True,
            name=f"scrape_job_{job_id}",
        )
        active_jobs[job_id] = t
        t.start()

        logger.info(
            "Launched scrape job %s | source=%s "
            "keyword=%s city=%s enrich=%s email=%s",
            job_id, source, keyword,
            effective_city, do_enrich, do_email,
        )
        return job_id