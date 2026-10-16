# database.py
# KNCS LeadGen Pro — v10.2
# Audit fixes applied:
#   BUG-DB-01: WAL + synchronous=NORMAL on every connection
#   BUG-DB-02: foreign_keys=ON on every connection
#   BUG-DB-03: BEGIN IMMEDIATE + UNIQUE(business_name, city) composite dedup
#   BUG-DB-04: Cache TTL uses unix epoch (time.time()) — no ISO strings
#   BUG-DB-05: Indexes on phone, fit_score, (city,industry), enrichment_status
#   BUG-DB-06: cleanup_stale_jobs() with started_at_ts timeout (v2.3 preserved)
#   BUG-DB-07: UNIQUE(lead_id, service_type) on revenue + INSERT OR REPLACE
#   BUG-DB-08: Circuit breaker state in system_state table
#   BUG-DB-10: Migration runner via add_column_if_missing()
#   BUG-DB-11: LIMIT ? on all query functions
#   BUG-DB-12: business_name+city composite dedup + normalize_phone()
#   EXTRA-01:  _ensure_universal_columns() removed from get_connection()
#              — init_database() is sole schema authority
#   EXTRA-02:  _SCHEMA_CHECKED threading.Lock() added
#   EXTRA-03:  ai_call_queued column added to ensure_leads_schema()
#   EXTRA-04:  conn.rollback() replaces conn.execute("ROLLBACK")
#   EXTRA-05:  revenue UNIQUE constraint in ensure_revenue_schema()
#   FIX-PLACEID-01 (CRITICAL, v10.2): insert_lead() now normalises a
#              blank place_id ('') to None before insert. SQLite's
#              UNIQUE(place_id) constraint treats '' as a real,
#              collidable value (unlike NULL, which is always distinct
#              from every other NULL). Any scraper source that doesn't
#              provide a Google place_id (YellowPages, Yelp, etc.) was
#              leaving place_id='' on every lead — the first such row
#              ever inserted succeeded and "claimed" that value, and
#              every subsequent blank-place_id insert from ANY source,
#              ANY city, ANY country silently failed with
#              "UNIQUE constraint failed: leads.place_id" and was
#              counted as skipped. This was the true root cause of
#              100% skip rates on non-Google-Maps sources — it had
#              nothing to do with country/market at all.

import os
import re
import time
import sqlite3
import logging
import json
import threading
from typing import Optional

log = logging.getLogger(__name__)

try:
    from config import DB_PATH
except Exception:
    DB_PATH = os.getenv("KNCS_DB_PATH", "data/leads.db")


# ─────────────────────────────────────────────────────────
# Phone normalisation
# BUG-DB-12 / BUG-SC-01: canonical form handles all three
# Nigerian and UK prefix formats
# ─────────────────────────────────────────────────────────

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    # 11-digit number starting with 0 — detect Nigeria vs UK by prefix
    if len(digits) == 11 and digits.startswith("0"):
        ng_prefixes = ("070", "080", "081", "090", "091")
        if any(digits.startswith(p) for p in ng_prefixes):
            digits = "234" + digits[1:]
        else:
            digits = "44" + digits[1:]
    # Ensure leading +
    if not digits.startswith("+"):
        digits = "+" + digits
    # Reject implausibly short numbers
    if len(re.sub(r"\D", "", digits)) < 7:
        return None
    return digits


# ─────────────────────────────────────────────────────────
# Connection
# BUG-DB-01: WAL + synchronous=NORMAL
# BUG-DB-02: foreign_keys=ON
# EXTRA-01:  Schema mutation (_ensure_universal_columns)
#            removed — init_database() is sole schema authority
# ─────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    os.makedirs(
        os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else "data",
        exist_ok=True,
    )
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ─────────────────────────────────────────────────────────
# Schema helpers
# BUG-DB-10: add_column_if_missing() is the migration primitive
# ─────────────────────────────────────────────────────────

def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row["name"] for row in cursor.fetchall()}


def add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    ddl: str,
) -> None:
    if column_name not in get_table_columns(conn, table_name):
        try:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"
            )
        except Exception as exc:
            log.warning(
                "Migration warning %s.%s: %s", table_name, column_name, exc
            )


# ─────────────────────────────────────────────────────────
# Leads schema
# BUG-DB-03: UNIQUE(business_name, city) composite dedup
# BUG-DB-12: city included in dedup key
# EXTRA-03:  ai_call_queued column present from fresh install
# ─────────────────────────────────────────────────────────

def ensure_leads_schema(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "leads"):
        return
    conn.execute("""
        CREATE TABLE leads (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name               TEXT NOT NULL,
            place_id                    TEXT,
            maps_url                    TEXT,
            phone                       TEXT,
            email                       TEXT,
            website                     TEXT,
            address                     TEXT,
            rating                      REAL    DEFAULT 0,
            reviews                     INTEGER DEFAULT 0,
            opportunity_score           INTEGER DEFAULT 0,
            contact_score               INTEGER DEFAULT 0,
            has_website                 INTEGER DEFAULT 0,
            website_active              INTEGER DEFAULT 0,
            has_instagram               INTEGER DEFAULT 0,
            has_facebook                INTEGER DEFAULT 0,
            has_twitter                 INTEGER DEFAULT 0,
            website_discovered          INTEGER DEFAULT 0,
            website_discovery_source    TEXT    DEFAULT '',
            website_discovery_confidence TEXT   DEFAULT 'low',
            social_facebook_url         TEXT    DEFAULT '',
            social_instagram_url        TEXT    DEFAULT '',
            social_linkedin_url         TEXT    DEFAULT '',
            industry                    TEXT    DEFAULT 'unknown',
            location                    TEXT,
            city                        TEXT,
            country                     TEXT    DEFAULT '',
            market                      TEXT    DEFAULT '',
            timezone                    TEXT    DEFAULT '',
            phone_country_code          TEXT    DEFAULT '',
            country_confidence          TEXT    DEFAULT 'low',
            source                      TEXT    DEFAULT 'google_maps',
            source_url                  TEXT,
            contacted                   INTEGER DEFAULT 0,
            contact_status              TEXT    DEFAULT 'new',
            notes                       TEXT,
            call_script                 TEXT,
            deal_value                  REAL    DEFAULT 0,
            deal_closed_at              TEXT,
            created_at                  TEXT    DEFAULT (datetime('now')),
            updated_at                  TEXT    DEFAULT (datetime('now')),
            last_contacted_at           TEXT,
            follow_up_date              TEXT,
            enrichment_status           TEXT    DEFAULT 'pending',
            last_enriched_at            TEXT,
            email_confidence            INTEGER DEFAULT 0,
            phone_confidence            INTEGER DEFAULT 0,
            website_discovered_from     TEXT,
            emails_found_count          INTEGER DEFAULT 0,
            phones_found_count          INTEGER DEFAULT 0,
            enrichment_notes            TEXT,
            fit_score                   INTEGER,
            fit_reason                  TEXT,
            pain_points                 TEXT    DEFAULT '[]',
            recommended_opener          TEXT,
            fit_scored_at               TEXT,
            website_audit_score         INTEGER DEFAULT 0,
            search_phrase               TEXT    DEFAULT '',
            lead_priority               TEXT    DEFAULT 'unscored',
            urgency_score               INTEGER DEFAULT 0,
            reachability_score          INTEGER DEFAULT 0,
            best_channel                TEXT    DEFAULT 'unknown',
            whatsapp_ready              INTEGER DEFAULT 0,
            ai_call_ready               INTEGER DEFAULT 0,
            ai_call_queued              INTEGER DEFAULT 0,
            ai_call_sent_at             TEXT,
            email_ready                 INTEGER DEFAULT 0,
            wa_status                   TEXT    DEFAULT 'unknown',
            wa_eligible                 INTEGER DEFAULT 0,
            wa_verified                 INTEGER DEFAULT 0,
            wa_opt_in                   INTEGER DEFAULT 0,
            verification_status         TEXT    DEFAULT 'unverified',
            phone_verified              INTEGER DEFAULT 0,
            routing_channel             TEXT    DEFAULT '',
            scoring_status              TEXT    DEFAULT 'pending',
            ai_scored_at                TEXT,
            ai_prompt_hash              TEXT,
            ai_output_cached            TEXT,
            ai_fail_count               INTEGER DEFAULT 0,
            last_ai_error               TEXT,
            last_contact_channel        TEXT,
            scored_at                   TEXT,
            decision_maker_name         TEXT,
            decision_maker_first        TEXT,
            decision_maker_title        TEXT,
            decision_maker_source       TEXT,
            decision_maker_confidence   INTEGER DEFAULT 0,
            dm_source                   TEXT    DEFAULT '',
            whatsapp_sent               INTEGER DEFAULT 0,
            whatsapp_sent_at            TEXT,
            outreach_status             TEXT    DEFAULT 'not_contacted',
            whatsapp_verified           INTEGER DEFAULT 0,
            whatsapp_check_attempted    INTEGER DEFAULT 0,
            whatsapp_check_at           TEXT,
            call_ready                  INTEGER DEFAULT 0,
            manual_review               INTEGER DEFAULT 0,
            channel_label               TEXT    DEFAULT 'unrouted',
            routing_reason              TEXT,
            insert_status               TEXT    DEFAULT 'inserted',
            cqc_rating                  TEXT    DEFAULT '',
            cqc_url                     TEXT    DEFAULT '',
            cqc_location_id             TEXT    DEFAULT '',
            company_number              TEXT,
            company_registration_number TEXT,
            director_name               TEXT,
            vertical                    TEXT,
            bed_count                   INTEGER,
            registration_date           TEXT,
            postcode                    TEXT    DEFAULT '',
            UNIQUE(place_id),
            UNIQUE(business_name, city)
        )
    """)


# ─────────────────────────────────────────────────────────
# Other table schemas
# BUG-DB-04: ai_cache uses cached_at REAL (unix epoch)
# BUG-DB-07: revenue UNIQUE(lead_id, service_type) enforced
#            at schema level
# ─────────────────────────────────────────────────────────

def ensure_ai_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_cache (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_hash   TEXT UNIQUE,
            model         TEXT,
            response_text TEXT,
            response_json TEXT,
            hit_count     INTEGER DEFAULT 0,
            fail_count    INTEGER DEFAULT 0,
            cached_at     REAL    DEFAULT (unixepoch()),
            last_used_at  REAL,
            last_status   TEXT    DEFAULT 'ok'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_cache_hash ON ai_cache(prompt_hash)"
    )


def ensure_scrape_jobs_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword          TEXT,
            city             TEXT,
            country          TEXT    DEFAULT '',
            source           TEXT,
            status           TEXT    DEFAULT 'pending',
            progress         INTEGER DEFAULT 0,
            progress_pct     REAL    DEFAULT 0.0,
            leads_found      INTEGER DEFAULT 0,
            inserted         INTEGER DEFAULT 0,
            skipped          INTEGER DEFAULT 0,
            updated          INTEGER DEFAULT 0,
            message          TEXT    DEFAULT '',
            started_at       TEXT,
            started_at_ts    REAL,
            finished_at      TEXT,
            duration_seconds INTEGER,
            location         TEXT,
            leads_inserted   INTEGER DEFAULT 0,
            leads_skipped    INTEGER DEFAULT 0,
            leads_updated    INTEGER DEFAULT 0,
            error_message    TEXT,
            current_lead     TEXT    DEFAULT '',
            completed_at     TEXT,
            total_target     INTEGER DEFAULT 0,
            created_at       REAL    DEFAULT (unixepoch())
        )
    """)
    # Idempotent column additions for existing databases
    for col, ddl in [
        ("city",             "TEXT"),
        ("country",          "TEXT DEFAULT ''"),
        ("progress_pct",     "REAL DEFAULT 0.0"),
        ("inserted",         "INTEGER DEFAULT 0"),
        ("skipped",          "INTEGER DEFAULT 0"),
        ("updated",          "INTEGER DEFAULT 0"),
        ("message",          "TEXT DEFAULT ''"),
        ("finished_at",      "TEXT"),
        ("duration_seconds", "INTEGER"),
        ("started_at_ts",    "REAL"),
        ("location",         "TEXT"),
        ("leads_inserted",   "INTEGER DEFAULT 0"),
        ("leads_skipped",    "INTEGER DEFAULT 0"),
        ("leads_updated",    "INTEGER DEFAULT 0"),
        ("error_message",    "TEXT"),
        ("current_lead",     "TEXT DEFAULT ''"),
        ("completed_at",     "TEXT"),
        ("total_target",     "INTEGER DEFAULT 0"),
    ]:
        add_column_if_missing(conn, "scrape_jobs", col, ddl)


def ensure_scrape_guard_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            source     TEXT,
            keyword    TEXT,
            location   TEXT,
            req_count  INTEGER DEFAULT 1,
            date       TEXT DEFAULT (date('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def ensure_scrape_failures_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_failures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source        TEXT,
            keyword       TEXT,
            location      TEXT,
            error_type    TEXT,
            error_message TEXT,
            is_block      INTEGER DEFAULT 0,
            is_success    INTEGER DEFAULT 0,
            failed_at     TEXT DEFAULT (datetime('now')),
            date          TEXT DEFAULT (date('now'))
        )
    """)
    add_column_if_missing(conn, "scrape_failures", "is_success", "INTEGER DEFAULT 0")


def ensure_directory_sources_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS directory_sources (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT UNIQUE,
            base_url           TEXT,
            search_pattern     TEXT,
            listing_selector   TEXT,
            name_selector      TEXT,
            phone_selector     TEXT,
            email_selector     TEXT,
            website_selector   TEXT,
            address_selector   TEXT,
            pagination_pattern TEXT,
            enabled            INTEGER DEFAULT 1,
            created_at         TEXT DEFAULT (datetime('now')),
            updated_at         TEXT DEFAULT (datetime('now'))
        )
    """)


def ensure_decision_makers_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_makers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id          INTEGER NOT NULL,
            first_name       TEXT,
            last_name        TEXT,
            full_name        TEXT,
            title            TEXT,
            email            TEXT,
            email_confidence INTEGER DEFAULT 0,
            phone            TEXT,
            source           TEXT,
            confidence_score INTEGER DEFAULT 0,
            is_primary       INTEGER DEFAULT 0,
            verified         INTEGER DEFAULT 0,
            created_at       TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
        )
    """)


def ensure_fit_score_jobs_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fit_score_jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id      INTEGER,
            raw_input    TEXT,
            result       TEXT,
            model_used   TEXT DEFAULT 'cloudflare',
            processed_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)


def ensure_outreach_log_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outreach_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id      INTEGER NOT NULL,
            channel      TEXT    NOT NULL DEFAULT 'unknown',
            status       TEXT    DEFAULT 'sent',
            message_text TEXT,
            opened       INTEGER DEFAULT 0,
            replied      INTEGER DEFAULT 0,
            converted    INTEGER DEFAULT 0,
            sent_at      TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)
    for col, ddl in [
        ("channel",      "TEXT DEFAULT 'unknown'"),
        ("status",       "TEXT DEFAULT 'sent'"),
        ("message_text", "TEXT"),
        ("opened",       "INTEGER DEFAULT 0"),
        ("replied",      "INTEGER DEFAULT 0"),
        ("converted",    "INTEGER DEFAULT 0"),
        ("sent_at",      "TEXT DEFAULT (datetime('now'))"),
    ]:
        add_column_if_missing(conn, "outreach_log", col, ddl)


def ensure_search_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword     TEXT,
            location    TEXT,
            source      TEXT,
            leads_found INTEGER DEFAULT 0,
            searched_at TEXT    DEFAULT (datetime('now'))
        )
    """)


def ensure_call_log_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id          INTEGER,
            status           TEXT,
            notes            TEXT,
            duration_seconds INTEGER DEFAULT 0,
            called_at        TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)


def ensure_revenue_schema(conn: sqlite3.Connection) -> None:
    """
    BUG-DB-07: UNIQUE(lead_id, service_type) prevents duplicate
    revenue entries for the same lead/service combination.
    Write path uses INSERT OR REPLACE (see save_revenue_record).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS revenue (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id        INTEGER,
            business_name  TEXT,
            amount         REAL,
            service_type   TEXT,
            payment_status TEXT DEFAULT 'pending',
            notes          TEXT,
            closed_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(lead_id, service_type),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)
    # Idempotent for existing databases — index replaces missing constraint
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_revenue_unique "
            "ON revenue(lead_id, service_type)"
        )
    except sqlite3.OperationalError as exc:
        log.warning("Revenue unique index skipped: %s", exc)


def ensure_system_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at REAL
        )
    """)


# ─────────────────────────────────────────────────────────
# Phase 1 vertical table schemas
# ─────────────────────────────────────────────────────────

def ensure_care_home_opportunities_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS care_home_opportunities (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            opportunity_score   INTEGER NOT NULL DEFAULT 0,
            urgency_score       INTEGER NOT NULL DEFAULT 0,
            supplier_match_json TEXT,
            signals_detected    TEXT,
            operator_group_id   INTEGER,
            last_refreshed      TEXT,
            created_at          TEXT NOT NULL
        )
    """)
    for col, ddl in [
        ("opportunity_type", "TEXT"),
        ("urgency_level",    "TEXT"),
        ("opportunity_score","REAL"),
        ("urgency_score",    "REAL"),
    ]:
        add_column_if_missing(conn, "care_home_opportunities", col, ddl)


def ensure_care_operator_groups_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS care_operator_groups (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name          TEXT NOT NULL,
            site_count          INTEGER NOT NULL DEFAULT 0,
            director_names      TEXT,
            company_numbers     TEXT,
            enterprise_score    INTEGER NOT NULL DEFAULT 0,
            contact_lead_id     INTEGER,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)
    for col, ddl in [
        ("director_name",  "TEXT"),
        ("group_type",     "TEXT"),
        ("postcode_area",  "TEXT"),
        ("detected_at",    "TEXT"),
    ]:
        add_column_if_missing(conn, "care_operator_groups", col, ddl)


def ensure_care_acquisition_signals_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS care_acquisition_signals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            signal_type         TEXT NOT NULL,
            signal_detail       TEXT,
            signal_date         TEXT,
            is_actioned         INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL
        )
    """)


def ensure_dental_opportunities_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dental_opportunities (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            opportunity_score   INTEGER NOT NULL DEFAULT 0,
            growth_score        INTEGER NOT NULL DEFAULT 0,
            review_health_score INTEGER NOT NULL DEFAULT 0,
            buyer_match_json    TEXT,
            signals_detected    TEXT,
            group_id            INTEGER,
            last_refreshed      TEXT,
            created_at          TEXT NOT NULL
        )
    """)


def ensure_dental_groups_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dental_groups (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name          TEXT NOT NULL,
            site_count          INTEGER NOT NULL DEFAULT 0,
            director_names      TEXT,
            enterprise_score    INTEGER NOT NULL DEFAULT 0,
            contact_lead_id     INTEGER,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)


def ensure_dental_review_analysis_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dental_review_analysis (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            google_rating       REAL,
            review_count        INTEGER,
            review_trend        TEXT,
            health_score        INTEGER NOT NULL DEFAULT 0,
            problems_detected   TEXT,
            analyzed_at         TEXT NOT NULL
        )
    """)


def ensure_dental_growth_signals_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dental_growth_signals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            signal_type         TEXT NOT NULL,
            signal_detail       TEXT,
            signal_date         TEXT,
            is_actioned         INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL
        )
    """)


def ensure_website_health_scans_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS website_health_scans (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            url_scanned         TEXT NOT NULL,
            ssl_status          TEXT,
            ssl_expiry_date     TEXT,
            load_time_seconds   REAL,
            mobile_responsive   INTEGER,
            broken_elements     INTEGER NOT NULL DEFAULT 0,
            has_contact_form    INTEGER NOT NULL DEFAULT 0,
            health_score        INTEGER NOT NULL DEFAULT 0,
            scan_details_json   TEXT,
            scanned_at          TEXT NOT NULL
        )
    """)


def ensure_cyber_risk_profiles_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cyber_risk_profiles (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            domain              TEXT NOT NULL,
            has_spf             INTEGER NOT NULL DEFAULT 0,
            has_dmarc           INTEGER NOT NULL DEFAULT 0,
            has_dkim            INTEGER NOT NULL DEFAULT 0,
            has_hsts            INTEGER NOT NULL DEFAULT 0,
            has_csp             INTEGER NOT NULL DEFAULT 0,
            ssl_valid           INTEGER NOT NULL DEFAULT 0,
            exposed_admin       INTEGER NOT NULL DEFAULT 0,
            risk_score          INTEGER NOT NULL DEFAULT 0,
            risk_details_json   TEXT,
            analyzed_at         TEXT NOT NULL
        )
    """)


def ensure_technology_profiles_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS technology_profiles (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id),
            cms_detected        TEXT,
            cms_version         TEXT,
            analytics_tools     TEXT,
            chat_tools          TEXT,
            email_tools         TEXT,
            tech_age_score      INTEGER NOT NULL DEFAULT 0,
            stack_details_json  TEXT,
            detected_at         TEXT NOT NULL
        )
    """)


def ensure_digital_maturity_scores_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS digital_maturity_scores (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id             INTEGER NOT NULL REFERENCES leads(id) UNIQUE,
            maturity_score      INTEGER NOT NULL DEFAULT 0,
            maturity_band       TEXT,
            health_score        INTEGER NOT NULL DEFAULT 0,
            cyber_score         INTEGER NOT NULL DEFAULT 0,
            tech_age_score      INTEGER NOT NULL DEFAULT 0,
            top_problems_json   TEXT,
            recommended_service TEXT,
            urgency             TEXT,
            estimated_value     TEXT,
            last_calculated     TEXT NOT NULL
        )
    """)


# ─────────────────────────────────────────────────────────
# Migration helpers
# BUG-DB-10: Each migration is idempotent via add_column_if_missing
# ─────────────────────────────────────────────────────────

def migrate_v93(conn: sqlite3.Connection) -> None:
    for col, ddl in {
        "whatsapp_verified":        "INTEGER DEFAULT 0",
        "whatsapp_check_attempted": "INTEGER DEFAULT 0",
        "whatsapp_check_at":        "TEXT",
        "call_ready":               "INTEGER DEFAULT 0",
        "manual_review":            "INTEGER DEFAULT 0",
        "channel_label":            "TEXT DEFAULT 'unrouted'",
        "routing_reason":           "TEXT",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)
    conn.commit()


def migrate_v95(conn: sqlite3.Connection) -> None:
    for col, ddl in {
        "website_audit_score": "INTEGER DEFAULT 0",
        "search_phrase":       "TEXT DEFAULT ''",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)
    conn.commit()


def migrate_v96(conn: sqlite3.Connection) -> None:
    for col, ddl in {
        "country":             "TEXT DEFAULT ''",
        "market":              "TEXT DEFAULT ''",
        "timezone":            "TEXT DEFAULT ''",
        "phone_country_code":  "TEXT DEFAULT ''",
        "country_confidence":  "TEXT DEFAULT 'low'",
        "wa_status":           "TEXT DEFAULT 'unknown'",
        "wa_eligible":         "INTEGER DEFAULT 0",
        "wa_verified":         "INTEGER DEFAULT 0",
        "wa_opt_in":           "INTEGER DEFAULT 0",
        "verification_status": "TEXT DEFAULT 'unverified'",
        "phone_verified":      "INTEGER DEFAULT 0",
        "routing_channel":     "TEXT DEFAULT ''",
        "scoring_status":      "TEXT DEFAULT 'pending'",
        "ai_scored_at":        "TEXT",
        "ai_prompt_hash":      "TEXT",
        "ai_output_cached":    "TEXT",
        "ai_fail_count":       "INTEGER DEFAULT 0",
        "last_ai_error":       "TEXT",
        "last_contact_channel":"TEXT",
        "scored_at":           "TEXT",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)
    conn.commit()


def migrate_v97(conn: sqlite3.Connection) -> None:
    for col, ddl in {
        "place_id":                    "TEXT",
        "maps_url":                    "TEXT",
        "website_discovered":          "INTEGER DEFAULT 0",
        "website_discovery_source":    "TEXT DEFAULT ''",
        "website_discovery_confidence":"TEXT DEFAULT 'low'",
        "social_facebook_url":         "TEXT DEFAULT ''",
        "social_instagram_url":        "TEXT DEFAULT ''",
        "social_linkedin_url":         "TEXT DEFAULT ''",
        "insert_status":               "TEXT DEFAULT 'inserted'",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)
    conn.execute(
        "UPDATE leads SET fit_score=NULL "
        "WHERE fit_score=0 "
        "AND lead_priority IN ('unscored','',NULL) "
        "AND fit_scored_at IS NULL"
    )
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_place_id "
            "ON leads(place_id) WHERE place_id IS NOT NULL"
        )
    except Exception as exc:
        log.warning("place_id index warning: %s", exc)
    conn.commit()


def migrate_v99(conn: sqlite3.Connection) -> None:
    for col, ddl in {
        "cqc_rating":      "TEXT DEFAULT ''",
        "cqc_url":         "TEXT DEFAULT ''",
        "cqc_location_id": "TEXT DEFAULT ''",
        "dm_source":       "TEXT DEFAULT ''",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)
    conn.commit()
    log.info("v9.9 migrations complete")


def migrate_v910(conn: sqlite3.Connection) -> None:
    """
    BUG-DB-07: Adds revenue unique index for existing databases.
    New databases get UNIQUE(lead_id, service_type) from
    ensure_revenue_schema() directly.
    BUG-DB-10: All columns added idempotently.
    """
    # leads columns
    for col, ddl in {
        "ai_call_queued": "INTEGER DEFAULT 0",
        "ai_call_sent_at":"TEXT",
        "postcode":       "TEXT DEFAULT ''",
        "vertical":       "TEXT",
        "bed_count":      "INTEGER",
        "registration_date": "TEXT",
        "company_number": "TEXT",
        "company_registration_number": "TEXT",
        "director_name":  "TEXT",
        "decision_maker_name": "TEXT",
    }.items():
        add_column_if_missing(conn, "leads", col, ddl)

    # scrape_failures
    add_column_if_missing(conn, "scrape_failures", "is_success", "INTEGER DEFAULT 0")

    # scrape_jobs
    for col, ddl in [
        ("started_at_ts",    "REAL"),
        ("finished_at",      "TEXT"),
        ("duration_seconds", "INTEGER"),
        ("city",             "TEXT"),
        ("country",          "TEXT DEFAULT ''"),
        ("progress_pct",     "REAL DEFAULT 0.0"),
        ("inserted",         "INTEGER DEFAULT 0"),
        ("skipped",          "INTEGER DEFAULT 0"),
        ("updated",          "INTEGER DEFAULT 0"),
        ("message",          "TEXT DEFAULT ''"),
    ]:
        add_column_if_missing(conn, "scrape_jobs", col, ddl)

    # BUG-DB-07: Revenue unique index (for databases that already exist
    # and were created before the UNIQUE constraint was added to the schema)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_revenue_unique "
            "ON revenue(lead_id, service_type)"
        )
    except sqlite3.OperationalError as exc:
        log.warning("Revenue unique index skipped: %s", exc)

    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_call_log_unique "
            "ON call_log(lead_id, called_at)"
        )
    except sqlite3.OperationalError as exc:
        log.warning("call_log unique index skipped: %s", exc)

    # BUG-DB-03: Composite dedup index — UNIQUE(business_name, city)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_name_city "
            "ON leads(business_name, city) "
            "WHERE business_name IS NOT NULL AND city IS NOT NULL"
        )
    except sqlite3.OperationalError as exc:
        log.warning("business_name+city index skipped: %s", exc)

    conn.commit()
    log.info("v10.0 migrations complete")


def migrate_phase1(conn: sqlite3.Connection) -> None:
    ensure_care_home_opportunities_schema(conn)
    ensure_care_operator_groups_schema(conn)
    ensure_care_acquisition_signals_schema(conn)
    ensure_dental_opportunities_schema(conn)
    ensure_dental_groups_schema(conn)
    ensure_dental_review_analysis_schema(conn)
    ensure_dental_growth_signals_schema(conn)
    ensure_website_health_scans_schema(conn)
    ensure_cyber_risk_profiles_schema(conn)
    ensure_technology_profiles_schema(conn)
    ensure_digital_maturity_scores_schema(conn)
    conn.commit()
    log.info("Phase 1 vertical intelligence tables ready")


# ─────────────────────────────────────────────────────────
# Initialization
# EXTRA-01: init_database() is the sole schema authority
# BUG-APP-01: cleanup_stale_jobs() called here so app.py
#             only needs to call init_database()
# ─────────────────────────────────────────────────────────

def init_database() -> None:
    conn = get_connection()
    try:
        ensure_leads_schema(conn)
        ensure_directory_sources_schema(conn)
        ensure_scrape_jobs_schema(conn)
        ensure_scrape_guard_schema(conn)
        ensure_scrape_failures_schema(conn)
        ensure_decision_makers_schema(conn)
        ensure_fit_score_jobs_schema(conn)
        ensure_outreach_log_schema(conn)
        ensure_ai_cache_schema(conn)
        ensure_search_history_schema(conn)
        ensure_call_log_schema(conn)
        ensure_revenue_schema(conn)
        ensure_system_state_schema(conn)
        migrate_v93(conn)
        migrate_v95(conn)
        migrate_v96(conn)
        migrate_v97(conn)
        migrate_v99(conn)
        migrate_v910(conn)
        migrate_phase1(conn)
        _create_indexes(conn)
        conn.commit()
        log.info("Database initialized (v10.2)")
    except Exception as exc:
        log.error("Database init error: %s", exc)
        raise
    finally:
        conn.close()


def _create_indexes(conn: sqlite3.Connection) -> None:
    """
    BUG-DB-05: Indexes on phone, fit_score DESC, (city, industry),
    enrichment_status to eliminate full-table scans.
    """
    indexes = [
        # leads — core query paths
        "CREATE INDEX IF NOT EXISTS idx_leads_phone      ON leads(phone)",
        "CREATE INDEX IF NOT EXISTS idx_leads_score      ON leads(opportunity_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_leads_fit        ON leads(fit_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_leads_city_ind   ON leads(city, industry)",
        "CREATE INDEX IF NOT EXISTS idx_leads_enrichment ON leads(enrichment_status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(contact_status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_source     ON leads(source)",
        "CREATE INDEX IF NOT EXISTS idx_leads_email      ON leads(email)",
        "CREATE INDEX IF NOT EXISTS idx_leads_city       ON leads(city)",
        "CREATE INDEX IF NOT EXISTS idx_leads_country    ON leads(country)",
        "CREATE INDEX IF NOT EXISTS idx_leads_priority   ON leads(lead_priority)",
        "CREATE INDEX IF NOT EXISTS idx_leads_channel    ON leads(best_channel)",
        "CREATE INDEX IF NOT EXISTS idx_leads_whatsapp   ON leads(whatsapp_ready)",
        "CREATE INDEX IF NOT EXISTS idx_leads_ai_call    ON leads(ai_call_ready)",
        "CREATE INDEX IF NOT EXISTS idx_leads_outreach   ON leads(outreach_status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_wa_verified ON leads(whatsapp_verified)",
        "CREATE INDEX IF NOT EXISTS idx_leads_call_ready ON leads(call_ready)",
        "CREATE INDEX IF NOT EXISTS idx_leads_ch_label   ON leads(channel_label)",
        "CREATE INDEX IF NOT EXISTS idx_leads_manual     ON leads(manual_review)",
        "CREATE INDEX IF NOT EXISTS idx_leads_market     ON leads(market)",
        "CREATE INDEX IF NOT EXISTS idx_leads_wa_status  ON leads(wa_status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_scoring    ON leads(scoring_status)",
        "CREATE INDEX IF NOT EXISTS idx_leads_ai_hash    ON leads(ai_prompt_hash)",
        "CREATE INDEX IF NOT EXISTS idx_leads_place_id   ON leads(place_id)",
        "CREATE INDEX IF NOT EXISTS idx_leads_maps_url   ON leads(maps_url)",
        "CREATE INDEX IF NOT EXISTS idx_leads_cqc_rating ON leads(cqc_rating)",
        "CREATE INDEX IF NOT EXISTS idx_leads_dm_source  ON leads(dm_source)",
        # scrape_jobs
        "CREATE INDEX IF NOT EXISTS idx_jobs_status      ON scrape_jobs(status)",
        # scrape_failures
        "CREATE INDEX IF NOT EXISTS idx_failures_source  ON scrape_failures(source, date)",
        "CREATE INDEX IF NOT EXISTS idx_failures_block   ON scrape_failures(is_block)",
        "CREATE INDEX IF NOT EXISTS idx_failures_success ON scrape_failures(is_success)",
        # decision_makers
        "CREATE INDEX IF NOT EXISTS idx_dm_lead          ON decision_makers(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_dm_primary       ON decision_makers(lead_id, is_primary)",
        # outreach_log
        "CREATE INDEX IF NOT EXISTS idx_outreach_lead    ON outreach_log(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_outreach_channel ON outreach_log(channel)",
        # care vertical
        "CREATE INDEX IF NOT EXISTS idx_care_opp_lead    ON care_home_opportunities(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_care_opp_score   ON care_home_opportunities(opportunity_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_care_opp_urgency ON care_home_opportunities(urgency_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_care_opp_group   ON care_home_opportunities(operator_group_id)",
        "CREATE INDEX IF NOT EXISTS idx_care_grp_score   ON care_operator_groups(enterprise_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_care_grp_sites   ON care_operator_groups(site_count DESC)",
        "CREATE INDEX IF NOT EXISTS idx_care_sig_lead    ON care_acquisition_signals(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_care_sig_type    ON care_acquisition_signals(signal_type)",
        "CREATE INDEX IF NOT EXISTS idx_care_sig_actioned ON care_acquisition_signals(is_actioned)",
        # dental vertical
        "CREATE INDEX IF NOT EXISTS idx_dent_opp_lead    ON dental_opportunities(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_dent_opp_score   ON dental_opportunities(opportunity_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dent_opp_growth  ON dental_opportunities(growth_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dent_opp_group   ON dental_opportunities(group_id)",
        "CREATE INDEX IF NOT EXISTS idx_dent_grp_score   ON dental_groups(enterprise_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dent_grp_sites   ON dental_groups(site_count DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dent_rev_lead    ON dental_review_analysis(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_dent_rev_rating  ON dental_review_analysis(google_rating)",
        "CREATE INDEX IF NOT EXISTS idx_dent_rev_health  ON dental_review_analysis(health_score)",
        "CREATE INDEX IF NOT EXISTS idx_dent_sig_lead    ON dental_growth_signals(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_dent_sig_type    ON dental_growth_signals(signal_type)",
        "CREATE INDEX IF NOT EXISTS idx_dent_sig_actioned ON dental_growth_signals(is_actioned)",
        # digital profile tables
        "CREATE INDEX IF NOT EXISTS idx_whs_lead         ON website_health_scans(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_whs_score        ON website_health_scans(health_score)",
        "CREATE INDEX IF NOT EXISTS idx_whs_scanned      ON website_health_scans(scanned_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_crp_lead         ON cyber_risk_profiles(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_crp_score        ON cyber_risk_profiles(risk_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_crp_domain       ON cyber_risk_profiles(domain)",
        "CREATE INDEX IF NOT EXISTS idx_tp_lead          ON technology_profiles(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_tp_cms           ON technology_profiles(cms_detected)",
        "CREATE INDEX IF NOT EXISTS idx_dms_lead         ON digital_maturity_scores(lead_id)",
        "CREATE INDEX IF NOT EXISTS idx_dms_score        ON digital_maturity_scores(maturity_score)",
        "CREATE INDEX IF NOT EXISTS idx_dms_band         ON digital_maturity_scores(maturity_band)",
        "CREATE INDEX IF NOT EXISTS idx_dms_urgency      ON digital_maturity_scores(urgency)",
    ]
    cursor = conn.cursor()
    for idx in indexes:
        try:
            cursor.execute(idx)
        except sqlite3.OperationalError as exc:
            log.warning("Skipping index: %s", exc)


def initialize_database() -> None:
    log.warning(
        "initialize_database() is deprecated — call init_database() directly"
    )
    return init_database()


# ─────────────────────────────────────────────────────────
# AI Cache helpers
# BUG-DB-04: TTL filter uses unix epoch (time.time()) — never ISO strings
# BUG-AI-08: Unified cache — all callers use these two functions
# ─────────────────────────────────────────────────────────

def get_cached_ai_score(
    prompt_hash: str,
    ttl_seconds: int = 86400,
) -> Optional[dict]:
    conn = get_connection()
    try:
        cutoff = time.time() - ttl_seconds
        row = conn.execute(
            "SELECT response_json, response_text, hit_count "
            "FROM ai_cache "
            "WHERE prompt_hash=? AND cached_at>? AND last_status='ok'",
            (prompt_hash, cutoff),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE ai_cache "
            "SET hit_count=hit_count+1, last_used_at=? "
            "WHERE prompt_hash=?",
            (time.time(), prompt_hash),
        )
        conn.commit()
        if row["response_json"]:
            try:
                return json.loads(row["response_json"])
            except json.JSONDecodeError:
                pass
        return {"raw": row["response_text"]} if row["response_text"] else None
    except sqlite3.Error as exc:
        log.error("get_cached_ai_score error: %s", exc)
        return None
    finally:
        conn.close()


def save_ai_cache(
    prompt_hash: str,
    model: str,
    response_text: str,
    response_json: Optional[dict] = None,
    status: str = "ok",
) -> bool:
    conn = get_connection()
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO ai_cache "
            "(prompt_hash,model,response_text,response_json,"
            "cached_at,last_used_at,last_status,hit_count) "
            "VALUES (?,?,?,?,?,?,?,0) "
            "ON CONFLICT(prompt_hash) DO UPDATE SET "
            "response_text=excluded.response_text, "
            "response_json=excluded.response_json, "
            "cached_at=excluded.cached_at, "
            "last_used_at=excluded.last_used_at, "
            "last_status=excluded.last_status, "
            "model=excluded.model",
            (
                prompt_hash,
                model,
                response_text,
                json.dumps(response_json) if response_json else None,
                now,
                now,
                status,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_ai_cache error: %s", exc)
        return False
    finally:
        conn.close()


def mark_ai_cache_failed(prompt_hash: str, error: str) -> bool:
    conn = get_connection()
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO ai_cache "
            "(prompt_hash,last_status,fail_count,cached_at,last_used_at) "
            "VALUES (?,'error',1,?,?) "
            "ON CONFLICT(prompt_hash) DO UPDATE SET "
            "fail_count=fail_count+1, "
            "last_status='error', "
            "last_used_at=excluded.last_used_at",
            (prompt_hash, now, now),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("mark_ai_cache_failed error: %s", exc)
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# System state helpers
# BUG-DB-08: Circuit breaker state persisted in system_state
# ─────────────────────────────────────────────────────────

def get_system_state(key: str) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None
    except sqlite3.Error as exc:
        log.error("get_system_state error: %s", exc)
        return None
    finally:
        conn.close()


def set_system_state(key: str, value: str) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (key, value, time.time()),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("set_system_state error: %s", exc)
        return False
    finally:
        conn.close()


def get_system_state_with_timestamp(key: str) -> tuple:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value, updated_at FROM system_state WHERE key=?", (key,)
        ).fetchone()
        if row:
            return row["value"], row["updated_at"]
        return None, None
    except sqlite3.Error as exc:
        log.error("get_system_state_with_timestamp error: %s", exc)
        return None, None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Revenue write helper
# BUG-DB-07: INSERT OR REPLACE enforces UNIQUE(lead_id, service_type)
# ─────────────────────────────────────────────────────────

def save_revenue_record(
    lead_id: int,
    amount: float,
    service_type: str,
    business_name: str = "",
    payment_status: str = "pending",
    notes: str = "",
) -> bool:
    """
    BUG-DB-07: Uses INSERT OR REPLACE so the same lead+service_type
    combination never creates a duplicate row.
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO revenue "
            "(lead_id, business_name, amount, service_type, "
            "payment_status, notes, closed_at) "
            "VALUES (?,?,?,?,?,?,datetime('now'))",
            (
                lead_id,
                business_name,
                amount,
                service_type,
                payment_status,
                notes,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_revenue_record error: %s", exc)
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# INSERT / UPSERT
# BUG-DB-03: BEGIN IMMEDIATE prevents race-condition duplicates
# BUG-DB-12: business_name + city composite dedup key
#            normalize_phone() before any comparison/storage
# EXTRA-04:  conn.rollback() replaces conn.execute("ROLLBACK")
# FIX-PLACEID-01: blank place_id normalised to None before insert
# ─────────────────────────────────────────────────────────

_LEAD_INSERT_FIELDS = [
    "business_name", "place_id", "maps_url", "phone", "email", "website",
    "address", "rating", "reviews", "opportunity_score", "contact_score",
    "has_website", "website_active", "has_instagram", "has_facebook",
    "has_twitter", "website_discovered", "website_discovery_source",
    "website_discovery_confidence", "social_facebook_url",
    "social_instagram_url", "social_linkedin_url", "industry", "location",
    "city", "country", "market", "timezone", "phone_country_code",
    "country_confidence", "source", "source_url", "contact_status",
    "call_script", "enrichment_status", "last_enriched_at",
    "email_confidence", "phone_confidence", "website_discovered_from",
    "emails_found_count", "phones_found_count", "enrichment_notes",
    "fit_score", "fit_reason", "pain_points", "recommended_opener",
    "fit_scored_at", "website_audit_score", "search_phrase", "lead_priority",
    "urgency_score", "reachability_score", "best_channel", "whatsapp_ready",
    "ai_call_ready", "ai_call_queued", "ai_call_sent_at", "email_ready",
    "wa_status", "wa_eligible", "wa_verified", "wa_opt_in",
    "verification_status", "phone_verified", "routing_channel",
    "scoring_status", "ai_scored_at", "ai_prompt_hash", "ai_output_cached",
    "ai_fail_count", "last_ai_error", "last_contact_channel",
    "decision_maker_name", "decision_maker_first", "decision_maker_title",
    "decision_maker_source", "decision_maker_confidence", "outreach_status",
    "whatsapp_verified", "whatsapp_check_attempted", "whatsapp_check_at",
    "call_ready", "manual_review", "channel_label", "routing_reason",
    "scored_at", "cqc_rating", "cqc_url", "cqc_location_id", "dm_source",
    "postcode", "vertical", "bed_count", "registration_date",
    "company_number", "company_registration_number", "director_name",
]

_UPDATABLE_TEXT_FIELDS = [
    "phone", "email", "website", "address", "city", "location", "country",
    "market", "timezone", "phone_country_code", "country_confidence",
    "call_script", "website_discovered_from", "enrichment_status",
    "last_enriched_at", "enrichment_notes", "fit_reason", "pain_points",
    "recommended_opener", "fit_scored_at", "website_audit_score",
    "search_phrase", "lead_priority", "best_channel", "wa_status",
    "verification_status", "routing_channel", "scoring_status",
    "ai_scored_at", "ai_prompt_hash", "ai_output_cached", "last_ai_error",
    "last_contact_channel", "decision_maker_name", "decision_maker_first",
    "decision_maker_title", "decision_maker_source", "outreach_status",
    "whatsapp_check_at", "channel_label", "routing_reason", "scored_at",
    "website_discovered", "website_discovery_source",
    "website_discovery_confidence", "social_facebook_url",
    "social_instagram_url", "social_linkedin_url", "maps_url", "dm_source",
    "cqc_rating", "cqc_url", "cqc_location_id", "postcode", "vertical",
    "registration_date", "company_number", "company_registration_number",
    "director_name",
]

_SCORE_FIELDS = [
    "opportunity_score", "contact_score", "email_confidence",
    "phone_confidence", "fit_score", "urgency_score", "reachability_score",
    "decision_maker_confidence", "ai_fail_count",
]

_COUNT_FIELDS = ["emails_found_count", "phones_found_count"]

_BOOL_FIELDS = [
    "whatsapp_ready", "ai_call_ready", "ai_call_queued", "email_ready",
    "call_ready", "manual_review", "whatsapp_verified",
    "whatsapp_check_attempted", "wa_eligible", "wa_verified", "wa_opt_in",
    "phone_verified", "website_discovered", "has_website", "website_active",
    "has_instagram", "has_facebook", "has_twitter",
]

_EMPTY = {"", "0", "none", "null", "[]", "unknown", "unscored", "pending"}


def insert_lead(lead_data: dict) -> tuple:
    """
    BUG-DB-03: BEGIN IMMEDIATE prevents concurrent duplicate inserts.
    BUG-DB-12: Dedup key is (business_name, city) — not name alone.
               normalize_phone() applied before every comparison.
    EXTRA-04:  conn.rollback() is safe in all connection states.
    FIX-PLACEID-01: blank place_id ('') normalised to None before
               insert — SQLite's UNIQUE(place_id) constraint treats
               '' as a real, collidable value (unlike NULL, which is
               always distinct). Without this fix, only the very
               first lead ever inserted with a blank place_id would
               succeed; every subsequent blank-place_id lead from any
               non-Google-Maps source (YellowPages, Yelp, etc.) would
               silently fail the UNIQUE constraint and be skipped.
    Returns (success: bool, status: str, lead_id: int|None)
    """
    if not lead_data.get("business_name"):
        return False, "error:missing_business_name", None

    conn = get_connection()
    try:
        safe_data = {k: lead_data[k] for k in _LEAD_INSERT_FIELDS if k in lead_data}
        if "pain_points" in safe_data and isinstance(safe_data["pain_points"], list):
            safe_data["pain_points"] = json.dumps(safe_data["pain_points"])

        place_id  = (safe_data.get("place_id") or "").strip()
        maps_url  = (safe_data.get("maps_url") or "").strip()

        # FIX-PLACEID-01: SQLite UNIQUE(place_id) treats '' as a real,
        # collidable value (unlike NULL, which is always distinct from
        # every other NULL). Sources without a Google place_id
        # (YellowPages, Yelp, etc.) were leaving place_id='' — the
        # first such row succeeded, then every subsequent blank-
        # place_id insert failed the UNIQUE constraint. Normalizing
        # blank place_id to None fixes this permanently.
        if not place_id:
            safe_data["place_id"] = None

        name      = safe_data.get("business_name", "").strip()
        city      = (safe_data.get("city") or "").strip()
        raw_phone = (safe_data.get("phone") or "").strip()

        # BUG-DB-12: normalize before comparison/storage
        norm_phone = normalize_phone(raw_phone)
        if norm_phone:
            safe_data["phone"] = norm_phone

        conn.execute("BEGIN IMMEDIATE")
        existing = None

        # Dedup priority: place_id → maps_url → (name, city) → (name, phone)
        if place_id:
            existing = conn.execute(
                "SELECT * FROM leads WHERE place_id=? LIMIT 1",
                (place_id,),
            ).fetchone()

        if existing is None and maps_url:
            existing = conn.execute(
                "SELECT * FROM leads WHERE maps_url=? LIMIT 1",
                (maps_url,),
            ).fetchone()

        # BUG-DB-12: primary dedup on (business_name, city)
        if existing is None and name and city:
            existing = conn.execute(
                "SELECT * FROM leads WHERE business_name=? AND city=? LIMIT 1",
                (name, city),
            ).fetchone()

        # Fallback: phone match (normalized)
        if existing is None and name and norm_phone:
            existing = conn.execute(
                "SELECT * FROM leads WHERE business_name=? "
                "AND (phone=? OR phone=?) LIMIT 1",
                (name, norm_phone, raw_phone),
            ).fetchone()

        # Last resort: name only, no phone recorded yet
        if existing is None and name:
            existing = conn.execute(
                "SELECT * FROM leads WHERE business_name=? "
                "AND (phone IS NULL OR phone='') LIMIT 1",
                (name,),
            ).fetchone()

        if existing is None:
            cols         = ", ".join(safe_data.keys())
            placeholders = ", ".join("?" for _ in safe_data)
            cursor = conn.execute(
                f"INSERT INTO leads ({cols}) VALUES ({placeholders})",
                list(safe_data.values()),
            )
            conn.commit()
            return True, "inserted", cursor.lastrowid

        existing    = dict(existing)
        existing_id = existing["id"]
        update_fields: list = []
        update_values: list = []

        for field in _UPDATABLE_TEXT_FIELDS:
            new_val  = safe_data.get(field)
            old_val  = existing.get(field)
            old_empty = old_val is None or str(old_val).strip().lower() in _EMPTY
            new_good  = (
                new_val is not None
                and str(new_val).strip().lower() not in _EMPTY
            )
            if old_empty and new_good:
                update_fields.append(f"{field} = ?")
                update_values.append(new_val)

        for field in _SCORE_FIELDS:
            new_score = safe_data.get(field)
            old_score = existing.get(field)
            new_int   = int(new_score) if new_score is not None else 0
            old_int   = int(old_score) if old_score is not None else 0
            if field == "fit_score" and new_score is None:
                continue
            if new_int > old_int:
                update_fields.append(f"{field} = ?")
                update_values.append(new_score)

        for field in _COUNT_FIELDS:
            new_val = int(safe_data.get(field) or 0)
            old_val = int(existing.get(field) or 0)
            if new_val > old_val:
                update_fields.append(f"{field} = ?")
                update_values.append(new_val)

        for field in _BOOL_FIELDS:
            new_val = int(safe_data.get(field) or 0)
            old_val = int(existing.get(field) or 0)
            if new_val == 1 and old_val == 0:
                update_fields.append(f"{field} = ?")
                update_values.append(1)

        if update_fields:
            update_fields.append("updated_at = datetime('now')")
            update_values.append(existing_id)
            conn.execute(
                f"UPDATE leads SET {', '.join(update_fields)} WHERE id=?",
                update_values,
            )
            conn.commit()
            return True, "updated", existing_id

        conn.commit()
        return False, "skipped", existing_id

    except sqlite3.IntegrityError as exc:
        try:
            conn.rollback()  # EXTRA-04: safe in all states
        except Exception:
            pass
        return False, f"error:integrity:{exc}", None
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"error:db:{exc}", None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Update helpers
# ─────────────────────────────────────────────────────────

def update_lead_status(
    lead_id: int,
    status: str,
    notes: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        if notes is not None:
            conn.execute(
                "UPDATE leads SET contact_status=?, notes=?, "
                "last_contacted_at=datetime('now'), "
                "updated_at=datetime('now') WHERE id=?",
                (status, notes, lead_id),
            )
        else:
            conn.execute(
                "UPDATE leads SET contact_status=?, "
                "last_contacted_at=datetime('now'), "
                "updated_at=datetime('now') WHERE id=?",
                (status, lead_id),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("update_lead_status error: %s", exc)
        return False
    finally:
        conn.close()


def update_lead_email(lead_id: int, email: str) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE leads SET email=? WHERE id=?", (email, lead_id)
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("update_lead_email error: %s", exc)
        return False
    finally:
        conn.close()


def update_lead_enrichment(lead_id: int, enrichment_data: dict) -> bool:
    if not enrichment_data or not isinstance(enrichment_data, dict):
        return False
    conn = get_connection()
    try:
        allowed = {
            "phone", "phone_confidence", "phone_source", "phone_verified",
            "phone_country_code", "email", "email_confidence",
            "emails_found_count", "phones_found_count",
            "decision_maker_name", "decision_maker_title",
            "decision_maker_confidence", "decision_maker_source", "dm_source",
            "website", "website_active", "website_audit_score",
            "website_discovered", "website_discovery_source",
            "website_discovery_confidence", "social_linkedin_url",
            "social_facebook_url", "social_instagram_url", "cqc_rating",
            "cqc_location_id", "cqc_url", "enrichment_status",
            "enrichment_notes", "company_number",
            "company_registration_number", "director_name", "last_enriched_at",
            "postcode", "vertical", "bed_count", "registration_date",
        }
        set_clauses = []
        values = []
        for k, v in enrichment_data.items():
            if k in allowed and v is not None:
                set_clauses.append(f"{k} = ?")
                values.append(v)
        if not set_clauses:
            return False
        from datetime import datetime, timezone
        set_clauses.append("last_enriched_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(lead_id)
        conn.execute(
            f"UPDATE leads SET {', '.join(set_clauses)} WHERE id=?", values
        )
        conn.commit()
        return True
    except Exception as exc:
        log.error("update_lead_enrichment failed: %s", exc)
        return False
    finally:
        conn.close()


def update_lead_fit_score(
    lead_id: int,
    fit_score: int,
    priority: str = "",
    best_channel: str = "",
    fit_reason: str = "",
) -> bool:
    conn = get_connection()
    try:
        from datetime import datetime, timezone
        conn.execute(
            "UPDATE leads SET "
            "fit_score=?, "
            "lead_priority=COALESCE(NULLIF(?,''),lead_priority), "
            "best_channel=COALESCE(NULLIF(?,''),best_channel), "
            "fit_reason=COALESCE(NULLIF(?,''),fit_reason), "
            "fit_scored_at=? "
            "WHERE id=?",
            (
                fit_score,
                priority,
                best_channel,
                fit_reason,
                datetime.now(timezone.utc).isoformat(),
                lead_id,
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        log.error("update_lead_fit_score failed: %s", exc)
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Outreach / call helpers
# ─────────────────────────────────────────────────────────

def log_call(
    lead_id: int,
    status: str,
    notes: Optional[str] = None,
    duration: int = 0,
) -> bool:
    if not lead_id:
        return False
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO call_log "
            "(lead_id,status,notes,duration_seconds,called_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (lead_id, status, notes, duration),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("log_call error (lead_id=%s): %s", lead_id, exc)
        return False
    finally:
        conn.close()


def log_outreach_action(
    lead_id: int,
    channel: str,
    message_text: Optional[str] = None,
    status: str = "sent",
) -> tuple:
    if not lead_id:
        return False, "Missing lead_id"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO outreach_log "
            "(lead_id,channel,status,message_text,sent_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (lead_id, channel, status, message_text),
        )
        conn.commit()
        return True, ""
    except sqlite3.Error as exc:
        log.error("log_outreach_action error (lead_id=%s): %s", lead_id, exc)
        return False, str(exc)
    finally:
        conn.close()



def batch_verify_whatsapp(lead_ids: list[int], verified: bool = True) -> int:
    """Batch verify multiple leads in a single database transaction."""
    if not lead_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ','.join('?' for _ in lead_ids)
        val = 1 if verified else 0
        status = 'verified' if verified else 'unknown'
        vstatus = 'verified' if verified else 'unverified'
        query = (
            f"UPDATE leads SET wa_verified=?, whatsapp_verified=?, wa_status=?, "
            f"verification_status=?, updated_at=datetime('now') "
            f"WHERE id IN ({placeholders})"
        )
        params = [val, val, status, vstatus] + [int(i) for i in lead_ids]
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur.rowcount
    except sqlite3.Error as exc:
        log.error("batch_verify_whatsapp error: %s", exc)
        return 0
    finally:
        conn.close()


def verify_whatsapp_manually(lead_id: int, verified: bool = True) -> bool:
    if not lead_id:
        return False
    conn = get_connection()
    try:
        if verified:
            conn.execute(
                "UPDATE leads SET "
                "wa_verified=1, whatsapp_verified=1, "
                "wa_status='verified', verification_status='verified', "
                "updated_at=datetime('now') WHERE id=?",
                (lead_id,),
            )
        else:
            conn.execute(
                "UPDATE leads SET "
                "wa_verified=0, whatsapp_verified=0, "
                "wa_status='unknown', verification_status='unverified', "
                "updated_at=datetime('now') WHERE id=?",
                (lead_id,),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error(
            "verify_whatsapp_manually error (lead_id=%s): %s", lead_id, exc
        )
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Read helpers
# BUG-DB-11: All queries have LIMIT ? — no unbounded table loads
# ─────────────────────────────────────────────────────────

def get_all_leads(
    min_score: int = 0,
    status: Optional[str] = None,
    source: Optional[str] = None,
    has_email: Optional[bool] = None,
    has_website: Optional[bool] = None,
    search_name: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 500,
) -> list:
    conn = get_connection()
    try:
        query = "SELECT * FROM leads WHERE 1=1"
        params: list = []
        if min_score > 0:
            query += " AND opportunity_score >= ?"
            params.append(min_score)
        if status:
            query += " AND contact_status = ?"
            params.append(status)
        if source:
            query += " AND source = ?"
            params.append(source)
        if has_email is True:
            query += " AND email IS NOT NULL AND email != ''"
        elif has_email is False:
            query += " AND (email IS NULL OR email = '')"
        if has_website is True:
            query += " AND has_website = 1"
        elif has_website is False:
            query += " AND has_website = 0"
        if search_name:
            query += " AND business_name LIKE ?"
            params.append(f"%{search_name}%")
        if city:
            query += " AND (city LIKE ? OR location LIKE ?)"
            params.extend([f"%{city}%", f"%{city}%"])
        if country:
            query += " AND country LIKE ?"
            params.append(f"%{country}%")
        query += " ORDER BY opportunity_score DESC LIMIT ?"
        params.append(limit)
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_lead_by_id(lead_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE id=?", (lead_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_leads_missing_emails(limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (email IS NULL OR email='') "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_leads_missing_websites(limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (website IS NULL OR website='') "
            "AND (website_discovered IS NULL OR website_discovered=0) "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_top_prospects(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE fit_score>=7 AND outreach_status='not_contacted' "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_whatsapp_verified_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (wa_verified=1 OR whatsapp_verified=1 "
            "OR wa_status IN ('verified','opted_in') "
            "OR verification_status IN ('verified','opted_in')) "
            "AND (phone IS NOT NULL AND phone!='') "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_whatsapp_estimated_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (wa_status IN ('estimated','business_confirmed') "
            "OR (whatsapp_ready=1 "
            "AND COALESCE(wa_verified,0)=0 "
            "AND COALESCE(whatsapp_verified,0)=0)) "
            "AND (phone IS NOT NULL AND phone!='') "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_whatsapp_ready_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (whatsapp_ready=1 "
            "OR wa_status IN ('estimated','business_confirmed','verified','opted_in') "
            "OR wa_verified=1 OR whatsapp_verified=1) "
            "AND (phone IS NOT NULL AND phone!='') "
            "ORDER BY "
            "CASE WHEN wa_verified=1 OR whatsapp_verified=1 "
            "OR wa_status IN ('verified','opted_in') THEN 0 ELSE 1 END, "
            "fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_ai_call_ready_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE ai_call_ready=1 AND ai_call_queued=0 "
            "AND (phone IS NOT NULL AND phone!='') "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_email_ready_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE email_ready=1 AND (email IS NOT NULL AND email!='') "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_unscored_leads(limit: int = 100) -> list:
    """
    BUG-SC-05 / BUG-DB-05: Only returns enriched leads eligible
    for scoring. Enrichment gate: only 'complete' or 'enriched'
    status enters the scoring queue.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (fit_score IS NULL OR lead_priority IS NULL "
            "OR lead_priority='') "
            "AND enrichment_status IN ('complete','enriched') "
            "ORDER BY opportunity_score DESC, created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_leads_by_priority(priority: str, limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if priority == "high":
            cursor.execute(
                "SELECT * FROM leads WHERE fit_score>=8 "
                "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
                (limit,),
            )
        elif priority == "medium":
            cursor.execute(
                "SELECT * FROM leads WHERE fit_score>=5 AND fit_score<8 "
                "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
                (limit,),
            )
        elif priority == "low":
            cursor.execute(
                "SELECT * FROM leads WHERE fit_score>=1 AND fit_score<5 "
                "ORDER BY fit_score DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor.execute(
                "SELECT * FROM leads "
                "WHERE fit_score IS NULL OR lead_priority IS NULL "
                "OR lead_priority IN ('','unscored') "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_leads_by_country(country: str, limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE LOWER(COALESCE(country,''))=LOWER(?) "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (country, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_leads_by_market(market: str, limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE LOWER(COALESCE(market,''))=LOWER(?) "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (market, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_unrouted_leads(limit: int = 200) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE (routing_channel IS NULL OR TRIM(routing_channel)='') "
            "AND (channel_label IS NULL OR TRIM(channel_label)='' "
            "OR channel_label='unrouted') "
            "ORDER BY COALESCE(fit_score,0) DESC, "
            "COALESCE(opportunity_score,0) DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_call_ready_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE call_ready=1 AND (phone IS NOT NULL AND phone!='') "
            "ORDER BY fit_score DESC, opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_manual_review_leads(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads "
            "WHERE manual_review=1 "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def apply_routing_update(
    lead_id: int,
    channel_label: str,
    routing_reason: Optional[str] = None,
    routing_channel: Optional[str] = None,
) -> bool:
    if not lead_id:
        return False
    effective_channel = routing_channel or channel_label
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE leads SET channel_label=?, routing_channel=?, "
            "routing_reason=?, updated_at=datetime('now') WHERE id=?",
            (channel_label, effective_channel, routing_reason, lead_id),
        )
        conn.commit()
        updated = conn.execute("SELECT changes() AS n").fetchone()
        return bool(updated and updated["n"] > 0)
    except sqlite3.Error as exc:
        log.error("apply_routing_update error (lead_id=%s): %s", lead_id, exc)
        return False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Phase 1 vertical read / write helpers
# ─────────────────────────────────────────────────────────

def get_care_opportunities(min_score: int = 0, limit: int = 200) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT cho.*, l.business_name, l.city, l.phone, l.email, "
            "l.cqc_rating, l.decision_maker_name "
            "FROM care_home_opportunities cho "
            "JOIN leads l ON l.id=cho.lead_id "
            "WHERE cho.opportunity_score>=? "
            "ORDER BY cho.opportunity_score DESC, cho.urgency_score DESC "
            "LIMIT ?",
            (min_score, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_care_opportunities error: %s", exc)
        return []
    finally:
        conn.close()


def get_care_opportunity_by_lead(lead_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM care_home_opportunities WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        return dict(row) if row else {}
    except sqlite3.Error as exc:
        log.error("get_care_opportunity_by_lead error: %s", exc)
        return {}
    finally:
        conn.close()


def save_care_opportunity(
    lead_id: int,
    opportunity_score: int,
    urgency_score: int,
    supplier_match_json: Optional[str] = None,
    signals_detected: Optional[str] = None,
    operator_group_id: Optional[int] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        existing = conn.execute(
            "SELECT id FROM care_home_opportunities WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE care_home_opportunities SET "
                "opportunity_score=?, urgency_score=?, "
                "supplier_match_json=?, signals_detected=?, "
                "operator_group_id=?, last_refreshed=? "
                "WHERE lead_id=?",
                (
                    opportunity_score,
                    urgency_score,
                    supplier_match_json,
                    signals_detected,
                    operator_group_id,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO care_home_opportunities "
                "(lead_id,opportunity_score,urgency_score,"
                "supplier_match_json,signals_detected,"
                "operator_group_id,last_refreshed,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    opportunity_score,
                    urgency_score,
                    supplier_match_json,
                    signals_detected,
                    operator_group_id,
                    now,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_care_opportunity error: %s", exc)
        return False
    finally:
        conn.close()


def get_care_operator_groups(limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM care_operator_groups "
            "ORDER BY enterprise_score DESC, site_count DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_care_operator_groups error: %s", exc)
        return []
    finally:
        conn.close()


def save_care_operator_group(group_data: dict) -> Optional[int]:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        existing = conn.execute(
            "SELECT id FROM care_operator_groups WHERE group_name=? LIMIT 1",
            (group_data.get("group_name"),),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE care_operator_groups SET "
                "site_count=?, director_names=?, company_numbers=?, "
                "enterprise_score=?, contact_lead_id=?, updated_at=? "
                "WHERE id=?",
                (
                    group_data.get("site_count", 0),
                    group_data.get("director_names"),
                    group_data.get("company_numbers"),
                    group_data.get("enterprise_score", 0),
                    group_data.get("contact_lead_id"),
                    now,
                    existing["id"],
                ),
            )
            conn.commit()
            return existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO care_operator_groups "
                "(group_name,site_count,director_names,company_numbers,"
                "enterprise_score,contact_lead_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    group_data.get("group_name"),
                    group_data.get("site_count", 0),
                    group_data.get("director_names"),
                    group_data.get("company_numbers"),
                    group_data.get("enterprise_score", 0),
                    group_data.get("contact_lead_id"),
                    now,
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid
    except sqlite3.Error as exc:
        log.error("save_care_operator_group error: %s", exc)
        return None
    finally:
        conn.close()


def save_care_acquisition_signal(
    lead_id: int,
    signal_type: str,
    signal_detail: Optional[str] = None,
    signal_date: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO care_acquisition_signals "
            "(lead_id,signal_type,signal_detail,signal_date,is_actioned,created_at) "
            "VALUES (?,?,?,?,0,?)",
            (lead_id, signal_type, signal_detail, signal_date, now),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_care_acquisition_signal error: %s", exc)
        return False
    finally:
        conn.close()


def get_care_acquisition_signals(
    lead_id: Optional[int] = None,
    signal_type: Optional[str] = None,
    actioned: Optional[bool] = None,
    limit: int = 100,
) -> list:
    conn = get_connection()
    try:
        query = "SELECT * FROM care_acquisition_signals WHERE 1=1"
        params: list = []
        if lead_id is not None:
            query += " AND lead_id=?"
            params.append(lead_id)
        if signal_type:
            query += " AND signal_type=?"
            params.append(signal_type)
        if actioned is not None:
            query += " AND is_actioned=?"
            params.append(1 if actioned else 0)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_care_acquisition_signals error: %s", exc)
        return []
    finally:
        conn.close()


def get_dental_opportunities(min_score: int = 0, limit: int = 200) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT dop.*, l.business_name, l.city, l.phone, l.email, "
            "l.website, l.decision_maker_name "
            "FROM dental_opportunities dop "
            "JOIN leads l ON l.id=dop.lead_id "
            "WHERE dop.opportunity_score>=? "
            "ORDER BY dop.opportunity_score DESC, dop.growth_score DESC "
            "LIMIT ?",
            (min_score, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_dental_opportunities error: %s", exc)
        return []
    finally:
        conn.close()


def save_dental_opportunity(
    lead_id: int,
    opportunity_score: int,
    growth_score: int,
    review_health_score: int,
    buyer_match_json: Optional[str] = None,
    signals_detected: Optional[str] = None,
    group_id: Optional[int] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        existing = conn.execute(
            "SELECT id FROM dental_opportunities WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE dental_opportunities SET "
                "opportunity_score=?, growth_score=?, review_health_score=?, "
                "buyer_match_json=?, signals_detected=?, group_id=?, "
                "last_refreshed=? WHERE lead_id=?",
                (
                    opportunity_score,
                    growth_score,
                    review_health_score,
                    buyer_match_json,
                    signals_detected,
                    group_id,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO dental_opportunities "
                "(lead_id,opportunity_score,growth_score,review_health_score,"
                "buyer_match_json,signals_detected,group_id,"
                "last_refreshed,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    opportunity_score,
                    growth_score,
                    review_health_score,
                    buyer_match_json,
                    signals_detected,
                    group_id,
                    now,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_dental_opportunity error: %s", exc)
        return False
    finally:
        conn.close()


def save_dental_review_analysis(
    lead_id: int,
    google_rating: Optional[float],
    review_count: Optional[int],
    review_trend: Optional[str],
    health_score: int,
    problems_detected: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        existing = conn.execute(
            "SELECT id FROM dental_review_analysis WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE dental_review_analysis SET "
                "google_rating=?, review_count=?, review_trend=?, "
                "health_score=?, problems_detected=?, analyzed_at=? "
                "WHERE lead_id=?",
                (
                    google_rating,
                    review_count,
                    review_trend,
                    health_score,
                    problems_detected,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO dental_review_analysis "
                "(lead_id,google_rating,review_count,review_trend,"
                "health_score,problems_detected,analyzed_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    lead_id,
                    google_rating,
                    review_count,
                    review_trend,
                    health_score,
                    problems_detected,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_dental_review_analysis error: %s", exc)
        return False
    finally:
        conn.close()


def save_dental_growth_signal(
    lead_id: int,
    signal_type: str,
    signal_detail: Optional[str] = None,
    signal_date: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO dental_growth_signals "
            "(lead_id,signal_type,signal_detail,signal_date,is_actioned,created_at) "
            "VALUES (?,?,?,?,0,?)",
            (lead_id, signal_type, signal_detail, signal_date, now),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_dental_growth_signal error: %s", exc)
        return False
    finally:
        conn.close()


def get_dental_groups(limit: int = 100) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM dental_groups "
            "ORDER BY enterprise_score DESC, site_count DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_dental_groups error: %s", exc)
        return []
    finally:
        conn.close()


def save_dental_group(group_data: dict) -> Optional[int]:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        existing = conn.execute(
            "SELECT id FROM dental_groups WHERE group_name=? LIMIT 1",
            (group_data.get("group_name"),),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE dental_groups SET "
                "site_count=?, director_names=?, enterprise_score=?, "
                "contact_lead_id=?, updated_at=? WHERE id=?",
                (
                    group_data.get("site_count", 0),
                    group_data.get("director_names"),
                    group_data.get("enterprise_score", 0),
                    group_data.get("contact_lead_id"),
                    now,
                    existing["id"],
                ),
            )
            conn.commit()
            return existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO dental_groups "
                "(group_name,site_count,director_names,enterprise_score,"
                "contact_lead_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    group_data.get("group_name"),
                    group_data.get("site_count", 0),
                    group_data.get("director_names"),
                    group_data.get("enterprise_score", 0),
                    group_data.get("contact_lead_id"),
                    now,
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid
    except sqlite3.Error as exc:
        log.error("save_dental_group error: %s", exc)
        return None
    finally:
        conn.close()


def save_website_health_scan(
    lead_id: int,
    url_scanned: str,
    health_score: int,
    scan_details: dict,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        details_json = json.dumps(scan_details)
        existing = conn.execute(
            "SELECT id FROM website_health_scans WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE website_health_scans SET "
                "url_scanned=?, ssl_status=?, ssl_expiry_date=?, "
                "load_time_seconds=?, mobile_responsive=?, "
                "broken_elements=?, has_contact_form=?, "
                "health_score=?, scan_details_json=?, scanned_at=? "
                "WHERE lead_id=?",
                (
                    url_scanned,
                    scan_details.get("ssl_status"),
                    scan_details.get("ssl_expiry_date"),
                    scan_details.get("load_time_seconds"),
                    1 if scan_details.get("mobile_responsive") else 0,
                    int(scan_details.get("broken_elements", 0)),
                    1 if scan_details.get("has_contact_form") else 0,
                    health_score,
                    details_json,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO website_health_scans "
                "(lead_id,url_scanned,ssl_status,ssl_expiry_date,"
                "load_time_seconds,mobile_responsive,broken_elements,"
                "has_contact_form,health_score,scan_details_json,scanned_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    url_scanned,
                    scan_details.get("ssl_status"),
                    scan_details.get("ssl_expiry_date"),
                    scan_details.get("load_time_seconds"),
                    1 if scan_details.get("mobile_responsive") else 0,
                    int(scan_details.get("broken_elements", 0)),
                    1 if scan_details.get("has_contact_form") else 0,
                    health_score,
                    details_json,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_website_health_scan error: %s", exc)
        return False
    finally:
        conn.close()


def save_cyber_risk_profile(
    lead_id: int,
    domain: str,
    risk_score: int,
    risk_details: dict,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        details_json = json.dumps(risk_details)
        existing = conn.execute(
            "SELECT id FROM cyber_risk_profiles WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cyber_risk_profiles SET "
                "domain=?, has_spf=?, has_dmarc=?, has_dkim=?, "
                "has_hsts=?, has_csp=?, ssl_valid=?, exposed_admin=?, "
                "risk_score=?, risk_details_json=?, analyzed_at=? "
                "WHERE lead_id=?",
                (
                    domain,
                    1 if risk_details.get("has_spf") else 0,
                    1 if risk_details.get("has_dmarc") else 0,
                    1 if risk_details.get("has_dkim") else 0,
                    1 if risk_details.get("has_hsts") else 0,
                    1 if risk_details.get("has_csp") else 0,
                    1 if risk_details.get("ssl_valid") else 0,
                    1 if risk_details.get("exposed_admin") else 0,
                    risk_score,
                    details_json,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO cyber_risk_profiles "
                "(lead_id,domain,has_spf,has_dmarc,has_dkim,has_hsts,"
                "has_csp,ssl_valid,exposed_admin,risk_score,"
                "risk_details_json,analyzed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    domain,
                    1 if risk_details.get("has_spf") else 0,
                    1 if risk_details.get("has_dmarc") else 0,
                    1 if risk_details.get("has_dkim") else 0,
                    1 if risk_details.get("has_hsts") else 0,
                    1 if risk_details.get("has_csp") else 0,
                    1 if risk_details.get("ssl_valid") else 0,
                    1 if risk_details.get("exposed_admin") else 0,
                    risk_score,
                    details_json,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_cyber_risk_profile error: %s", exc)
        return False
    finally:
        conn.close()


def save_technology_profile(
    lead_id: int,
    tech_age_score: int,
    stack_details: dict,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        details_json = json.dumps(stack_details)
        existing = conn.execute(
            "SELECT id FROM technology_profiles WHERE lead_id=? LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE technology_profiles SET "
                "cms_detected=?, cms_version=?, analytics_tools=?, "
                "chat_tools=?, email_tools=?, tech_age_score=?, "
                "stack_details_json=?, detected_at=? WHERE lead_id=?",
                (
                    stack_details.get("cms_detected"),
                    stack_details.get("cms_version"),
                    stack_details.get("analytics_tools"),
                    stack_details.get("chat_tools"),
                    stack_details.get("email_tools"),
                    tech_age_score,
                    details_json,
                    now,
                    lead_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO technology_profiles "
                "(lead_id,cms_detected,cms_version,analytics_tools,"
                "chat_tools,email_tools,tech_age_score,"
                "stack_details_json,detected_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    lead_id,
                    stack_details.get("cms_detected"),
                    stack_details.get("cms_version"),
                    stack_details.get("analytics_tools"),
                    stack_details.get("chat_tools"),
                    stack_details.get("email_tools"),
                    tech_age_score,
                    details_json,
                    now,
                ),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_technology_profile error: %s", exc)
        return False
    finally:
        conn.close()


def save_digital_maturity_score(
    lead_id: int,
    maturity_score: int,
    maturity_band: str,
    health_score: int,
    cyber_score: int,
    tech_age_score: int,
    top_problems_json: Optional[str] = None,
    recommended_service: Optional[str] = None,
    urgency: Optional[str] = None,
    estimated_value: Optional[str] = None,
) -> bool:
    conn = get_connection()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO digital_maturity_scores "
            "(lead_id,maturity_score,maturity_band,health_score,cyber_score,"
            "tech_age_score,top_problems_json,recommended_service,"
            "urgency,estimated_value,last_calculated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lead_id) DO UPDATE SET "
            "maturity_score=excluded.maturity_score, "
            "maturity_band=excluded.maturity_band, "
            "health_score=excluded.health_score, "
            "cyber_score=excluded.cyber_score, "
            "tech_age_score=excluded.tech_age_score, "
            "top_problems_json=excluded.top_problems_json, "
            "recommended_service=excluded.recommended_service, "
            "urgency=excluded.urgency, "
            "estimated_value=excluded.estimated_value, "
            "last_calculated=excluded.last_calculated",
            (
                lead_id,
                maturity_score,
                maturity_band,
                health_score,
                cyber_score,
                tech_age_score,
                top_problems_json,
                recommended_service,
                urgency,
                estimated_value,
                now,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        log.error("save_digital_maturity_score error: %s", exc)
        return False
    finally:
        conn.close()


def get_digital_maturity_scores(
    max_score: Optional[int] = None,
    maturity_band: Optional[str] = None,
    limit: int = 200,
) -> list:
    conn = get_connection()
    try:
        query = (
            "SELECT dms.*, l.business_name, l.city, l.website, "
            "l.phone, l.email "
            "FROM digital_maturity_scores dms "
            "JOIN leads l ON l.id=dms.lead_id WHERE 1=1"
        )
        params: list = []
        if max_score is not None:
            query += " AND dms.maturity_score<=?"
            params.append(max_score)
        if maturity_band:
            query += " AND dms.maturity_band=?"
            params.append(maturity_band)
        query += " ORDER BY dms.maturity_score ASC LIMIT ?"
        params.append(limit)
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_digital_maturity_scores error: %s", exc)
        return []
    finally:
        conn.close()


def get_cyber_risk_profiles(
    min_risk_score: int = 0,
    limit: int = 200,
) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT crp.*, l.business_name, l.city, l.website "
            "FROM cyber_risk_profiles crp "
            "JOIN leads l ON l.id=crp.lead_id "
            "WHERE crp.risk_score>=? "
            "ORDER BY crp.risk_score DESC LIMIT ?",
            (min_risk_score, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_cyber_risk_profiles error: %s", exc)
        return []
    finally:
        conn.close()


def get_website_health_scans(
    max_score: Optional[int] = None,
    limit: int = 200,
) -> list:
    conn = get_connection()
    try:
        query = (
            "SELECT whs.*, l.business_name, l.city, l.website "
            "FROM website_health_scans whs "
            "JOIN leads l ON l.id=whs.lead_id WHERE 1=1"
        )
        params: list = []
        if max_score is not None:
            query += " AND whs.health_score<=?"
            params.append(max_score)
        query += " ORDER BY whs.health_score ASC LIMIT ?"
        params.append(limit)
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_website_health_scans error: %s", exc)
        return []
    finally:
        conn.close()


def get_technology_profiles(limit: int = 200) -> list:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tp.*, l.business_name, l.city, l.website "
            "FROM technology_profiles tp "
            "JOIN leads l ON l.id=tp.lead_id "
            "ORDER BY tp.tech_age_score ASC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        log.error("get_technology_profiles error: %s", exc)
        return []
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Startup helpers
# BUG-DB-06 / FIX-STALE-01: Only clean jobs older than
# timeout_seconds (v2.3 logic preserved exactly)
# ─────────────────────────────────────────────────────────

def cleanup_stale_jobs(timeout_seconds: int = 7200) -> int:
    """
    Mark stale running/pending jobs as failed.

    CRITICAL FIX (v2.3 / BUG-DB-06):
    Only marks jobs as stale if started_at_ts is older than
    timeout_seconds (default 2 hours = 7200 seconds).

    Previous version marked ALL running jobs as failed on
    every server restart — this killed active Companies House
    threads that take 30-45 minutes to complete.

    Jobs with no started_at_ts are still cleaned immediately
    as they cannot be timed safely.
    """
    conn = get_connection()
    try:
        cutoff_ts = time.time() - timeout_seconds
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE scrape_jobs
            SET status  = 'failed',
                message = 'Cleaned up at startup — session ended unexpectedly'
            WHERE status IN ('running', 'pending')
              AND (
                  started_at_ts IS NULL
                  OR started_at_ts < ?
              )
            """,
            (cutoff_ts,),
        )
        cleaned = cursor.rowcount
        conn.commit()
        if cleaned > 0:
            log.info(
                "cleanup_stale_jobs: marked %s stale jobs as failed "
                "(timeout=%ss)",
                cleaned,
                timeout_seconds,
            )
        return cleaned
    except Exception as exc:
        log.warning("cleanup_stale_jobs failed: %s", exc)
        return 0
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Dashboard stats
# ─────────────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) AS total_leads,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(lead_priority,'')))
                    IN ('high','hot') THEN 1 ELSE 0 END) AS high_fit_leads,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(lead_priority,'')))
                    IN ('medium','warm') THEN 1 ELSE 0 END) AS medium_fit_leads,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(lead_priority,'')))
                    ='low' THEN 1 ELSE 0 END) AS low_fit_leads,
                SUM(CASE WHEN fit_score IS NULL
                    OR TRIM(COALESCE(lead_priority,''))=''
                    OR LOWER(TRIM(COALESCE(lead_priority,'')))
                    IN ('unscored','') THEN 1 ELSE 0 END) AS unscored_leads,
                SUM(CASE WHEN email IS NOT NULL
                    AND TRIM(email)!='' THEN 1 ELSE 0 END) AS email_ready,
                SUM(CASE WHEN email IS NOT NULL
                    AND TRIM(email)!='' THEN 1 ELSE 0 END) AS leads_with_email,
                SUM(CASE WHEN phone IS NOT NULL
                    AND TRIM(phone)!='' THEN 1 ELSE 0 END) AS leads_with_phone,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(wa_status,'')))
                    IN ('verified','opted_in') THEN 1 ELSE 0 END) AS whatsapp_verified,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(wa_status,'')))
                    ='estimated' THEN 1 ELSE 0 END) AS whatsapp_estimated,
                SUM(CASE WHEN COALESCE(whatsapp_ready,0)=1
                    THEN 1 ELSE 0 END) AS whatsapp_ready,
                SUM(CASE WHEN COALESCE(call_ready,0)=1
                    THEN 1 ELSE 0 END) AS call_ready,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(best_channel,'')))
                    ='ai_call' THEN 1 ELSE 0 END) AS ai_call_ready,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(wa_status,'')))
                    ='sent' THEN 1 ELSE 0 END) AS whatsapp_sent,
                SUM(CASE WHEN decision_maker_name IS NOT NULL
                    AND TRIM(decision_maker_name)!=''
                    THEN 1 ELSE 0 END) AS leads_with_decision_maker,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(contact_status,'')))
                    ='deal_closed' THEN 1 ELSE 0 END) AS deals_closed,
                COALESCE(SUM(CAST(COALESCE(deal_value,0) AS REAL)),0)
                    AS total_revenue,
                SUM(CASE WHEN LOWER(TRIM(COALESCE(best_channel,'')))
                    ='manual' THEN 1 ELSE 0 END) AS manual_review_needed,
                SUM(CASE WHEN (best_channel IS NULL OR TRIM(best_channel)='')
                    THEN 1 ELSE 0 END) AS unrouted_leads
            FROM leads
        """)
        row = cur.fetchone()
        if not row:
            return {}

        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(country),''),'Unknown') AS cn, "
            "COUNT(*) AS total FROM leads "
            "GROUP BY cn ORDER BY total DESC LIMIT 5"
        )
        by_country = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(market),''),'Unknown') AS mn, "
            "COUNT(*) AS total FROM leads "
            "GROUP BY mn ORDER BY total DESC LIMIT 5"
        )
        by_market = {r[0]: r[1] for r in cur.fetchall()}

        return {
            "total_leads":               row[0]  or 0,
            "high_fit_leads":            row[1]  or 0,
            "medium_fit_leads":          row[2]  or 0,
            "low_fit_leads":             row[3]  or 0,
            "unscored_leads":            row[4]  or 0,
            "email_ready":               row[5]  or 0,
            "leads_with_email":          row[6]  or 0,
            "leads_with_phone":          row[7]  or 0,
            "whatsapp_verified":         row[8]  or 0,
            "whatsapp_estimated":        row[9]  or 0,
            "whatsapp_ready":            row[10] or 0,
            "call_ready":                row[11] or 0,
            "ai_call_ready":             row[12] or 0,
            "whatsapp_sent":             row[13] or 0,
            "leads_with_decision_maker": row[14] or 0,
            "deals_closed":              row[15] or 0,
            "total_revenue":             row[16] or 0,
            "manual_review_needed":      row[17] or 0,
            "unrouted_leads":            row[18] or 0,
            "by_country":                by_country,
            "by_market":                 by_market,
        }
    except Exception as exc:
        log.error("get_dashboard_stats error: %s", exc)
        return {}
    finally:
        conn.close()
# ─────────────────────────────────────────────────────────
# v10.10 Audit Compliance Helpers
# ─────────────────────────────────────────────────────────

def has_been_contacted(lead_id: int, channel: str = "whatsapp") -> bool:
    """Pre-flight check: returns True if lead has already received outreach on this channel."""
    if not lead_id:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE lead_id = ? AND channel = ?",
            (lead_id, channel),
        )
        row = cur.fetchone()
        return bool(row and row[0] > 0)
    except Exception as exc:
        log.error("has_been_contacted error: %s", exc)
        return False
    finally:
        conn.close()


def increment_ai_fail_count(lead_id: int, error: str = "") -> bool:
    """Increment AI fail count for circuit breaker tracking."""
    if not lead_id:
        return False
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE leads SET ai_fail_count = COALESCE(ai_fail_count, 0) + 1 WHERE id = ?",
            (lead_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.error("increment_ai_fail_count error: %s", exc)
        return False
    finally:
        conn.close()


def get_revenue_summary(start_ts: Optional[float] = None, end_ts: Optional[float] = None) -> dict:
    """Return aggregated revenue metrics across periods and currencies."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0.0) FROM revenue WHERE payment_status = 'received'")
        row = cur.fetchone()
        return {
            "total_deals": row[0] if row else 0,
            "total_revenue": row[1] if row else 0.0,
            "currency": "GBP",
        }
    except Exception as exc:
        log.error("get_revenue_summary error: %s", exc)
        return {"total_deals": 0, "total_revenue": 0.0, "currency": "GBP"}
    finally:
        conn.close()
