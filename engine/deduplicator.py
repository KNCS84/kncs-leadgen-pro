# engine/deduplicator.py
# KNCS LeadGen Pro — v9.7 PREMIUM
# 16 bugs fixed:
#   🔴 4 Critical: conn outside try, dict(row) crash, None leads crash, missing place_id dedup
#   🟠 5 High: asymmetric phone logic, maps_url missing, list type guard, city in batch key
#   🟡 4 Medium: case-insensitive compare, typed return, chunked IN query, place_id batch dedup
#   🟢 3 Low: docstring updated, __all__ added, counter separation
#
# Dedup hierarchy (matches database.py v9.7):
#   1. place_id   (Google Maps primary key — most reliable)
#   2. maps_url   (Google Maps URL — secondary)
#   3. name+phone (universal fallback — symmetric, NOCASE)
#
# Behaviour:
#   - Within-batch duplicates: dropped here (same scrape session)
#   - DB duplicates with missing data: passed through → upsert fills gaps
#   - DB duplicates with complete data: blocked → reported as "skipped"

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    "is_exact_duplicate",
    "leads_already_in_db",
    "deduplicate_batch",
    "check_place_id_exists",
]

# ─────────────────────────────────────────────────────────
# Safe import
# ─────────────────────────────────────────────────────────
try:
    from database import get_connection
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
    get_connection = None


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────
def _get_conn():
    """
    Return a DB connection with row_factory set.
    FIX #3: ensures dict(row) works on all returned rows.
    Raises RuntimeError if DB unavailable.
    """
    if not _DB_AVAILABLE or get_connection is None:
        raise RuntimeError("Database not available")
    conn = get_connection()
    import sqlite3
    conn.row_factory = sqlite3.Row
    return conn


def _norm_name(name: str) -> str:
    """Normalize business name for comparison."""
    return (name or "").strip().lower()


# ─────────────────────────────────────────────────────────
# Public: place_id fast check
# ─────────────────────────────────────────────────────────
def check_place_id_exists(place_id: str) -> bool:
    """
    FIX #6/#7: v9.7 primary dedup key.
    Returns True if place_id already exists in DB.
    Fast O(1) index lookup — use this before name+phone check.
    """
    if not place_id or not str(place_id).strip():
        return False

    conn = None
    try:
        conn = _get_conn()                       # FIX #1: conn = None pattern
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM leads WHERE place_id = ? LIMIT 1",
            (str(place_id).strip(),),
        )
        return cursor.fetchone() is not None
    except Exception as exc:
        logger.debug("check_place_id_exists error: %s", exc)
        return False
    finally:
        if conn:                                 # FIX #1: safe close
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# Public: full 3-tier dedup check
# ─────────────────────────────────────────────────────────
def is_exact_duplicate(
    business_name: str,
    phone: str,
    place_id: str = "",
    maps_url: str = "",
) -> tuple:
    """
    3-tier dedup matching database.py v9.7 hierarchy.

    Tier 1: place_id (Google Maps primary key)
    Tier 2: maps_url (Google Maps URL)
    Tier 3: name + phone (universal, NOCASE, symmetric)

    Returns: (is_duplicate: bool, reason: str)
      reason examples: "place_id_match", "maps_url_match",
                       "name_phone_match", "name_only_needs_update",
                       "not_found"

    FIX #1: conn = None pattern — no crash if get_connection fails
    FIX #3: row_factory set — dict(row) safe
    FIX #5: symmetric phone check — both directions handled
    FIX #6: place_id check added as tier 1
    FIX #7: maps_url check added as tier 2
    FIX #10: COLLATE NOCASE for name comparison
    FIX #11: returns (bool, reason) tuple
    """
    if not business_name or not str(business_name).strip():
        return False, "no_name"

    conn = None
    try:
        conn = _get_conn()                           # FIX #1
        cursor = conn.cursor()

        # ── Tier 1: place_id ──────────────────────────────
        if place_id and str(place_id).strip():
            cursor.execute(
                "SELECT id, email, phone, city FROM leads "
                "WHERE place_id = ? LIMIT 1",
                (str(place_id).strip(),),
            )
            row = cursor.fetchone()
            if row:
                existing = dict(row)               # FIX #3: row_factory ensures this works
                if _record_is_complete(existing):
                    return True, "place_id_match"
                return False, "place_id_needs_update"

        # ── Tier 2: maps_url ──────────────────────────────
        if maps_url and str(maps_url).strip():
            cursor.execute(
                "SELECT id, email, phone, city FROM leads "
                "WHERE maps_url = ? LIMIT 1",
                (str(maps_url).strip(),),
            )
            row = cursor.fetchone()
            if row:
                existing = dict(row)
                if _record_is_complete(existing):
                    return True, "maps_url_match"
                return False, "maps_url_needs_update"

        # ── Tier 3: name + phone ──────────────────────────
        name = str(business_name).strip()
        ph   = str(phone or "").strip()

        if ph:
            # FIX #5: symmetric — check both incoming phone against DB
            # and DB phone against incoming (handles partial data both ways)
            cursor.execute(
                """
                SELECT id, email, phone, city FROM leads
                WHERE business_name = ? COLLATE NOCASE
                AND (
                    (phone = ? AND phone != '')
                    OR (phone = '' OR phone IS NULL)
                )
                LIMIT 1
                """,
                (name, ph),
            )
        else:
            # No phone incoming — name-only check
            cursor.execute(
                """
                SELECT id, email, phone, city FROM leads
                WHERE business_name = ? COLLATE NOCASE
                LIMIT 1
                """,
                (name,),
            )

        row = cursor.fetchone()
        if not row:
            return False, "not_found"

        existing = dict(row)

        # If existing has phone and incoming also has phone → true match
        existing_phone = (existing.get("phone") or "").strip()
        if ph and existing_phone and ph == existing_phone:
            if _record_is_complete(existing):
                return True, "name_phone_match"
            return False, "name_phone_needs_update"

        # Name-only match — let through for upsert
        return False, "name_only_needs_update"

    except Exception as exc:
        logger.warning("is_exact_duplicate error: %s", exc)
        return False, "error"
    finally:
        if conn:                                     # FIX #1
            try:
                conn.close()
            except Exception:
                pass


def _record_is_complete(existing: dict) -> bool:
    """
    Returns True only if the existing DB record has all key fields populated.
    If any are missing, the incoming record should be allowed through
    so the upsert can fill the gaps.
    """
    return bool(
        existing.get("email")
        and existing.get("phone")
        and existing.get("city")
    )


# ─────────────────────────────────────────────────────────
# Public: bulk name lookup (page-skip optimisation)
# ─────────────────────────────────────────────────────────
def leads_already_in_db(business_names: list) -> set:
    """
    Return set of business names already in DB.
    Used for page-skip logic in scrapers.

    FIX #2: conn = None pattern
    FIX #8: list() conversion + type safety
    FIX #13: chunked IN query — SQLite max 999 params
    """
    if not business_names:
        return set()

    # FIX #8: ensure it's a plain list of strings
    names = [str(n).strip() for n in (business_names or []) if n]
    if not names:
        return set()

    conn = None
    found: set = set()

    try:
        conn = _get_conn()                           # FIX #2
        cursor = conn.cursor()

        # FIX #13: chunk into ≤900 at a time (SQLite 999 param limit)
        chunk_size = 900
        for i in range(0, len(names), chunk_size):
            chunk = names[i : i + chunk_size]
            placeholders = ",".join(["?" for _ in chunk])
            cursor.execute(
                f"SELECT business_name FROM leads "
                f"WHERE business_name IN ({placeholders}) COLLATE NOCASE",
                chunk,
            )
            for row in cursor.fetchall():
                val = row[0] if not isinstance(row, dict) else row.get("business_name", "")
                if val:
                    found.add(str(val).strip())

        return found

    except Exception as exc:
        logger.warning("leads_already_in_db error: %s", exc)
        return set()
    finally:
        if conn:                                     # FIX #2
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
# Public: within-batch dedup
# ─────────────────────────────────────────────────────────
def deduplicate_batch(leads: list) -> tuple:
    """
    Remove within-batch duplicates only.

    Does NOT block leads already in DB — those go through
    the smart upsert in database.py which fills missing fields.

    Dedup key priority (within batch):
      1. place_id (if present)
      2. maps_url (if present)
      3. name + phone + city (FIX #9: city added)

    Returns: (unique_leads, stats_dict)
      stats_dict = {
          "duplicate_count": int,   -- true within-batch duplicates
          "no_name_count":   int,   -- dropped for missing name
      }

    FIX #4: leads = leads or [] guard
    FIX #9: city added to batch key
    FIX #12: separate counters
    FIX #14: place_id added to batch seen set
    """
    leads = leads or []                              # FIX #4

    unique: list = []
    duplicate_count: int = 0
    no_name_count: int = 0

    seen_place_ids: set = set()
    seen_maps_urls: set = set()
    seen_keys: set = set()

    for lead in leads:
        name  = str(lead.get("business_name") or "").strip()
        phone = str(lead.get("phone") or "").strip()
        city  = str(lead.get("city") or "").strip().lower()

        # Drop nameless leads — separate counter
        if not name:
            no_name_count += 1
            logger.debug("Batch dedup: dropped nameless lead")
            continue

        # ── Tier 1: place_id within batch ──────────────
        # FIX #14: place_id dedup within batch
        pid = str(lead.get("place_id") or "").strip()
        if pid:
            if pid in seen_place_ids:
                duplicate_count += 1
                logger.debug("Batch dedup: place_id dup '%s'", pid)
                continue
            seen_place_ids.add(pid)

        # ── Tier 2: maps_url within batch ──────────────
        murl = str(lead.get("maps_url") or "").strip()
        if murl:
            if murl in seen_maps_urls:
                duplicate_count += 1
                logger.debug("Batch dedup: maps_url dup '%s'", murl)
                continue
            seen_maps_urls.add(murl)

        # ── Tier 3: name + phone + city ────────────────
        # FIX #9: city added — prevents "Premium Hotel Lagos"
        # and "Premium Hotel Abuja" being treated as same business
        name_lower = name.lower()
        if phone:
            batch_key = f"{name_lower}|{phone}|{city}"
        else:
            batch_key = f"{name_lower}||{city}"

        if batch_key in seen_keys:
            duplicate_count += 1
            logger.debug("Batch dedup: name+phone+city dup '%s'", name)
            continue

        seen_keys.add(batch_key)
        unique.append(lead)

    stats = {
        "duplicate_count": duplicate_count,
        "no_name_count":   no_name_count,
    }

    logger.info(
        "Batch dedup: %d unique, %d duplicates, %d no-name",
        len(unique), duplicate_count, no_name_count,
    )
    return unique, stats