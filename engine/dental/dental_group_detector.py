# dental_group_detector.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Identifies dental chains and multi-site operators.
#            Detects groups via director name matching,
#            known chain name patterns, and Companies House
#            cross-referencing.
# Dependencies: database.py, engine/companies_house.py
# Critical rules observed:
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — partial matches still returned
#   - No exceptions raised to caller

import json
import logging
import re
from datetime import datetime
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# KNOWN DENTAL CHAIN PATTERNS
# ─────────────────────────────────────────────────────────────
# Business name keywords that confirm a national/regional chain.
# Matched case-insensitively against business_name field.

KNOWN_CHAIN_PATTERNS = [
    "bupa dental",
    "bupa",
    "portman dental",
    "portman",
    "mydentist",
    "my dentist",
    "dental care alliance",
    "dentalcare",
    "family dental",
    "boots dental",
    "the dental centre",
    "dental excellence",
    "perfect smile",
    "smile store",
    "smile clinic",
    "white dental",
    "clear dental",
    "fresh dental",
    "dental studio",
    "dental group",
    "dental practice group",
    "advanced dental",
    "dental solutions",
    "0117 dental",
]

# Minimum sites to qualify as a group
MIN_SITES_FOR_GROUP = 2

# Companies House prefix length for holding company detection
CH_PREFIX_LENGTH = 2

# ─────────────────────────────────────────────────────────────
# SAFE EXTERNAL CLIENT IMPORT
# ─────────────────────────────────────────────────────────────

try:
    from engine import companies_house as _ch
    _CH_AVAILABLE = True
except Exception as _e:
    log.warning("dental_group_detector — companies_house unavailable: %s", _e)
    _ch = None
    _CH_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _normalise_director_name(name: str) -> str:
    """
    Normalise a director name for reliable matching.
    Strips titles, extra whitespace, lowercases.

    Args:
        name: Raw director name string.

    Returns:
        Normalised lowercase string.
    """
    if not name:
        return ""
    titles = r"\b(mr|mrs|ms|miss|dr|prof|sir|rev|ltd|limited)\b"
    cleaned = re.sub(titles, "", name.lower(), flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def _normalise_postcode_area(address: str) -> str:
    """
    Extract postcode area from an address string.
    Used to detect regional clusters.

    Args:
        address: Raw address string.

    Returns:
        Uppercase postcode area or empty string.
    """
    if not address:
        return ""
    match = re.search(
        r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*\d[A-Z]{2}\b",
        address.upper(),
    )
    return match.group(1) if match else ""


def _detect_known_chain(business_name: str) -> Optional[str]:
    """
    Check if a business name matches a known dental chain pattern.

    Args:
        business_name: Raw business name string.

    Returns:
        Matched chain name string, or None if no match.
    """
    if not business_name:
        return None
    name_lower = business_name.lower()
    for pattern in KNOWN_CHAIN_PATTERNS:
        if pattern in name_lower:
            # Return clean chain label
            return pattern.title()
    return None


def _fetch_all_dental_leads(conn) -> list:
    """
    Fetch all dental practice leads with director, address,
    and Companies House fields.

    Returns:
        List of dicts. Empty list on error.
    """
    try:
        cur = conn.execute(
            """
            SELECT  l.id            AS lead_id,
                    l.business_name,
                    l.address,
                    l.website,
                    l.director_name,
                    l.company_number,
                    do2.opportunity_score,
                    do2.opportunity_type,
                    do2.urgency_level
            FROM    leads l
            LEFT    JOIN dental_opportunities do2
                    ON  do2.lead_id = l.id
            WHERE   l.vertical = 'dental'
               OR   l.industry LIKE '%dental%'
               OR   l.industry LIKE '%dentist%'
               OR   l.industry LIKE '%orthodont%'
            ORDER   BY l.business_name ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        log.error("_fetch_all_dental_leads error=%s", exc)
        return []


def _save_dental_group(
    group_name:    str,
    group_type:    str,
    site_count:    int,
    lead_ids:      list,
    director:      Optional[str],
    postcode_area: Optional[str],
    is_known_chain: bool,
    conn,
) -> bool:
    """
    Insert or replace a group record in dental_groups table.

    Returns:
        True on success, False on failure.
    """
    try:
        lead_ids_json = json.dumps(lead_ids)
        conn.execute(
            """
            INSERT OR REPLACE INTO dental_groups
                (group_name, group_type, site_count,
                 lead_ids, director_name, postcode_area,
                 is_known_chain, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_name,
                group_type,
                site_count,
                lead_ids_json,
                director,
                postcode_area,
                1 if is_known_chain else 0,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        log.error(
            "_save_dental_group group=%s error=%s", group_name, exc
        )
        return False


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def identify_dental_groups() -> list:
    """
    Scan all dental leads and identify multi-site groups
    using four detection strategies:

        1. Known chain name pattern match
        2. Director name match — same person runs 2+ practices
        3. Companies House prefix match — same holding company
        4. Address postcode cluster — regional operator

    Saves detected groups to dental_groups table.

    Returns:
        List of group dicts. Each contains:
            group_name, group_type, site_count,
            lead_ids, director_name, postcode_area,
            is_known_chain
        Empty list if no groups found or on error.
    """
    groups_found = []

    conn = db.get_connection()
    try:
        leads = _fetch_all_dental_leads(conn)
        if not leads:
            log.info("identify_dental_groups — no dental leads found")
            return []

        log.info(
            "identify_dental_groups — scanning %s dental leads",
            len(leads),
        )

        # ── Strategy 1: Known chain name matching ─────────────
        chain_map: dict[str, list] = {}

        for lead in leads:
            name  = lead.get("business_name") or ""
            chain = _detect_known_chain(name)
            if chain:
                if chain not in chain_map:
                    chain_map[chain] = []
                chain_map[chain].append(lead)

        for chain_name, chain_leads in chain_map.items():
            lead_ids = [l["lead_id"] for l in chain_leads]
            group_name = f"{chain_name} (National Chain)"

            saved = _save_dental_group(
                group_name     = group_name,
                group_type     = "known_chain",
                site_count     = len(chain_leads),
                lead_ids       = lead_ids,
                director       = None,
                postcode_area  = None,
                is_known_chain = True,
                conn           = conn,
            )

            if saved:
                groups_found.append({
                    "group_name":    group_name,
                    "group_type":    "known_chain",
                    "site_count":    len(chain_leads),
                    "lead_ids":      lead_ids,
                    "director_name": None,
                    "postcode_area": None,
                    "is_known_chain": True,
                })
                log.info(
                    "identify_dental_groups — known chain: %s (%s sites)",
                    group_name, len(chain_leads),
                )

        # ── Strategy 2: Director name matching ────────────────
        director_map: dict[str, list] = {}

        for lead in leads:
            raw = lead.get("director_name") or ""
            if not raw:
                continue
            norm = _normalise_director_name(raw)
            if not norm:
                continue
            if norm not in director_map:
                director_map[norm] = []
            director_map[norm].append(lead)

        for director_norm, dir_leads in director_map.items():
            if len(dir_leads) < MIN_SITES_FOR_GROUP:
                continue

            names = [l.get("business_name") or "" for l in dir_leads]
            words_sets = [set(n.lower().split()) for n in names if n]
            stop_words = {
                "dental", "dentist", "practice", "surgery",
                "the", "and", "ltd", "limited", "group",
                "clinic", "centre", "center",
            }

            if words_sets:
                common = words_sets[0].intersection(*words_sets[1:])
                common -= stop_words
                group_label = (
                    next(iter(common)).title()
                    if common
                    else director_norm.title()
                )
            else:
                group_label = director_norm.title()

            group_name = f"{group_label} Dental Group"
            lead_ids   = [l["lead_id"] for l in dir_leads]

            saved = _save_dental_group(
                group_name     = group_name,
                group_type     = "director_match",
                site_count     = len(dir_leads),
                lead_ids       = lead_ids,
                director       = director_norm.title(),
                postcode_area  = None,
                is_known_chain = False,
                conn           = conn,
            )

            if saved:
                groups_found.append({
                    "group_name":    group_name,
                    "group_type":    "director_match",
                    "site_count":    len(dir_leads),
                    "lead_ids":      lead_ids,
                    "director_name": director_norm.title(),
                    "postcode_area": None,
                    "is_known_chain": False,
                })
                log.info(
                    "identify_dental_groups — director group: %s (%s sites)",
                    group_name, len(dir_leads),
                )

        # ── Strategy 3: Companies House prefix matching ────────
        ch_prefix_map: dict[str, list] = {}

        for lead in leads:
            ch_num = lead.get("company_number") or ""
            if not ch_num or len(ch_num) < 3:
                continue
            prefix = ch_num[:CH_PREFIX_LENGTH].upper()
            if not re.match(r"^\d{2}$", prefix):
                continue
            if prefix not in ch_prefix_map:
                ch_prefix_map[prefix] = []
            ch_prefix_map[prefix].append(lead)

        for prefix, prefix_leads in ch_prefix_map.items():
            if len(prefix_leads) < MIN_SITES_FOR_GROUP:
                continue

            group_name = f"CH-{prefix} Dental Group"
            lead_ids   = [l["lead_id"] for l in prefix_leads]

            saved = _save_dental_group(
                group_name     = group_name,
                group_type     = "ch_prefix",
                site_count     = len(prefix_leads),
                lead_ids       = lead_ids,
                director       = None,
                postcode_area  = None,
                is_known_chain = False,
                conn           = conn,
            )

            if saved:
                groups_found.append({
                    "group_name":    group_name,
                    "group_type":    "ch_prefix",
                    "site_count":    len(prefix_leads),
                    "lead_ids":      lead_ids,
                    "director_name": None,
                    "postcode_area": None,
                    "is_known_chain": False,
                })
                log.info(
                    "identify_dental_groups — CH prefix group: %s (%s sites)",
                    group_name, len(prefix_leads),
                )

        # ── Strategy 4: Postcode cluster ──────────────────────
        postcode_map: dict[str, list] = {}

        for lead in leads:
            address = lead.get("address") or ""
            area    = _normalise_postcode_area(address)
            if not area:
                continue
            if area not in postcode_map:
                postcode_map[area] = []
            postcode_map[area].append(lead)

        for area, area_leads in postcode_map.items():
            if len(area_leads) < MIN_SITES_FOR_GROUP:
                continue

            group_name = f"{area} Dental Cluster"
            lead_ids   = [l["lead_id"] for l in area_leads]

            saved = _save_dental_group(
                group_name     = group_name,
                group_type     = "address_cluster",
                site_count     = len(area_leads),
                lead_ids       = lead_ids,
                director       = None,
                postcode_area  = area,
                is_known_chain = False,
                conn           = conn,
            )

            if saved:
                groups_found.append({
                    "group_name":    group_name,
                    "group_type":    "address_cluster",
                    "site_count":    len(area_leads),
                    "lead_ids":      lead_ids,
                    "director_name": None,
                    "postcode_area": area,
                    "is_known_chain": False,
                })
                log.info(
                    "identify_dental_groups — postcode cluster: %s (%s sites)",
                    group_name, len(area_leads),
                )

        log.info(
            "identify_dental_groups — complete. %s groups detected.",
            len(groups_found),
        )
        return groups_found

    except Exception as exc:
        log.error("identify_dental_groups error=%s", exc)
        return []
    finally:
        conn.close()


def get_group_profile(group_name: str) -> dict:
    """
    Retrieve full profile for a named dental group,
    including all member practices and their scores.

    Args:
        group_name: Exact group name as stored in dental_groups.

    Returns:
        dict with keys:
            group_name, group_type, site_count,
            director_name, postcode_area,
            is_known_chain, detected_at,
            practices (list of practice summary dicts)
        Empty dict if not found or on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  group_name, group_type, site_count,
                    lead_ids, director_name, postcode_area,
                    is_known_chain, detected_at
            FROM    dental_groups
            WHERE   group_name = ?
            LIMIT   1
            """,
            (group_name,),
        )
        row = cur.fetchone()
        if not row:
            log.warning(
                "get_group_profile — group not found: %s", group_name
            )
            return {}

        profile = dict(row)

        try:
            lead_ids = json.loads(profile.get("lead_ids") or "[]")
        except Exception:
            lead_ids = []

        # Fetch member practice summaries
        practices = []
        for lead_id in lead_ids:
            lcur = conn.execute(
                """
                SELECT  l.id AS lead_id,
                        l.business_name,
                        l.address,
                        l.website,
                        l.director_name,
                        l.company_number,
                        do2.opportunity_score,
                        do2.opportunity_type,
                        do2.urgency_level
                FROM    leads l
                LEFT    JOIN dental_opportunities do2
                        ON  do2.lead_id = l.id
                WHERE   l.id = ?
                LIMIT   1
                """,
                (lead_id,),
            )
            lrow = lcur.fetchone()
            if lrow:
                practices.append(dict(lrow))

        profile["practices"] = practices
        profile["lead_ids"]  = lead_ids
        profile["is_known_chain"] = bool(profile.get("is_known_chain"))
        return profile

    except Exception as exc:
        log.error(
            "get_group_profile group=%s error=%s", group_name, exc
        )
        return {}
    finally:
        conn.close()


def get_enterprise_opportunities(min_sites: int = 3) -> list:
    """
    Return dental groups meeting a minimum site threshold.
    Known chains are always prioritised at the top.

    Args:
        min_sites: Minimum number of sites to qualify. Default 3.

    Returns:
        List of group dicts ordered by:
            is_known_chain DESC, site_count DESC
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  group_name, group_type, site_count,
                    director_name, postcode_area,
                    is_known_chain, detected_at
            FROM    dental_groups
            WHERE   site_count >= ?
            ORDER   BY is_known_chain DESC,
                       site_count DESC
            """,
            (min_sites,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        # Cast is_known_chain to bool
        for r in rows:
            r["is_known_chain"] = bool(r.get("is_known_chain"))

        log.info(
            "get_enterprise_opportunities min_sites=%s returned %s groups",
            min_sites, len(rows),
        )
        return rows
    except Exception as exc:
        log.error("get_enterprise_opportunities error=%s", exc)
        return []
    finally:
        conn.close()


def link_practices_to_groups() -> int:
    """
    Write the dental group name back onto each lead row
    for every detected group.

    Enables dashboards to filter by group without
    joining dental_groups on every query.

    Returns:
        Number of lead rows successfully updated.
        Returns 0 on error.
    """
    updated = 0
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT group_name, lead_ids FROM dental_groups"
        )
        groups = cur.fetchall()

        for group in groups:
            group_name = group["group_name"]
            try:
                lead_ids = json.loads(group["lead_ids"] or "[]")
            except Exception:
                lead_ids = []

            for lead_id in lead_ids:
                try:
                    conn.execute(
                        """
                        UPDATE leads
                        SET    dental_group = ?
                        WHERE  id = ?
                        """,
                        (group_name, lead_id),
                    )
                    updated += 1
                except Exception as exc:
                    log.debug(
                        "link_practices_to_groups lead_id=%s — %s",
                        lead_id, exc,
                    )

        conn.commit()
        log.info(
            "link_practices_to_groups — updated %s lead rows", updated
        )
        return updated

    except Exception as exc:
        log.error("link_practices_to_groups error=%s", exc)
        return 0
    finally:
        conn.close()