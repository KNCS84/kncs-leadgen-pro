# care_operator_groups.py
# KNCS LeadGen Pro — v2.0 Phase 1
# NEW FILE — Identifies care home chains and multi-site operators.
#            Detects groups via director name matching,
#            Companies House registration prefix, and address clustering.
# Dependencies: database.py, engine/companies_house.py
# Critical rules observed:
#   - All DB via database.py get_connection()
#   - Parameterized SQL only
#   - logger not print()
#   - Graceful degradation — partial matches still returned
#   - No exceptions raised to caller

import logging
import re
from datetime import datetime
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Minimum number of sites sharing a director to flag as a group
MIN_SITES_FOR_GROUP = 2

# Companies House registration number prefix length to compare
# e.g. "SC" prefix = Scotland, numeric prefix clusters suggest
# same filing agent / holding company
CH_PREFIX_LENGTH = 2

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
    # Remove common titles
    titles = r"\b(mr|mrs|ms|miss|dr|prof|sir|rev|ltd|limited)\b"
    cleaned = re.sub(titles, "", name.lower(), flags=re.IGNORECASE)
    # Collapse whitespace
    return " ".join(cleaned.split()).strip()


def _normalise_postcode_area(address: str) -> str:
    """
    Extract the postcode area (first 2-4 chars) from an address string.
    Used to detect regional clusters.

    Args:
        address: Raw address string.

    Returns:
        Uppercase postcode area string, or empty string if not found.
    """
    if not address:
        return ""
    # Match UK postcode pattern
    match = re.search(
        r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*\d[A-Z]{2}\b",
        address.upper(),
    )
    if match:
        # Return just the outward code (area + district)
        return match.group(1)
    return ""


def _fetch_all_care_leads(conn) -> list:
    """
    Fetch all care home leads with director, address,
    and Companies House number fields.

    Returns:
        List of dicts. Empty list on error.
    """
    try:
        cur = conn.execute(
            """
            SELECT  l.id            AS lead_id,
                    l.business_name,
                    l.address,
                    l.director_name,
                    l.company_number
            FROM    leads l
            WHERE   l.vertical = 'care'
               OR   l.industry LIKE '%care%'
               OR   l.industry LIKE '%nursing%'
               OR   l.industry LIKE '%residential%'
            ORDER   BY l.business_name ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        log.error("_fetch_all_care_leads error=%s", exc)
        return []


def _fetch_lead_ids_for_director(director_norm: str, conn) -> list:
    """
    Find all care lead IDs sharing the same normalised director name.

    Args:
        director_norm: Normalised director name string.
        conn: Active DB connection.

    Returns:
        List of lead_id integers.
    """
    try:
        # We match on LOWER(director_name) LIKE pattern
        pattern = f"%{director_norm}%"
        cur = conn.execute(
            """
            SELECT  id AS lead_id
            FROM    leads
            WHERE   LOWER(director_name) LIKE ?
              AND   (
                        vertical = 'care'
                    OR  industry LIKE '%care%'
                    OR  industry LIKE '%nursing%'
                    OR  industry LIKE '%residential%'
                    )
            """,
            (pattern,),
        )
        return [row["lead_id"] for row in cur.fetchall()]
    except Exception as exc:
        log.error("_fetch_lead_ids_for_director error=%s", exc)
        return []


def _save_operator_group(
    group_name:   str,
    group_type:   str,
    site_count:   int,
    lead_ids:     list,
    director:     Optional[str],
    postcode_area: Optional[str],
    conn,
) -> bool:
    """
    Insert or replace a group record in care_operator_groups.

    Args:
        group_name:    Detected group name.
        group_type:    'director_match' | 'ch_prefix' | 'address_cluster'
        site_count:    Number of sites in group.
        lead_ids:      List of lead_id integers.
        director:      Director name if matched by director, else None.
        postcode_area: Postcode area if cluster, else None.
        conn:          Active DB connection.

    Returns:
        True on success, False on failure.
    """
    try:
        import json
        lead_ids_json = json.dumps(lead_ids)
        conn.execute(
            """
            INSERT OR REPLACE INTO care_operator_groups
                (group_name, group_type, site_count,
                 lead_ids, director_name, postcode_area,
                 detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_name,
                group_type,
                site_count,
                lead_ids_json,
                director,
                postcode_area,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        log.error(
            "_save_operator_group group=%s error=%s", group_name, exc
        )
        return False


def _update_lead_group_link(lead_id: int, group_name: str, conn) -> None:
    """
    Write the group_name back to the lead row so dashboards
    can filter by group without joining.

    Non-fatal if the column does not exist.
    """
    try:
        conn.execute(
            """
            UPDATE leads
            SET    operator_group = ?
            WHERE  id = ?
            """,
            (group_name, lead_id),
        )
        conn.commit()
    except Exception as exc:
        log.debug(
            "_update_lead_group_link lead_id=%s group=%s — %s",
            lead_id, group_name, exc,
        )


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def identify_operator_groups() -> list:
    """
    Scan all care home leads and identify multi-site operator groups
    using three detection strategies:

        1. Director name match — same person runs 2+ homes
        2. Companies House prefix match — same holding company
        3. Address postcode cluster — regional operator

    Saves detected groups to care_operator_groups table.

    Returns:
        List of group dicts, each containing:
            group_name, group_type, site_count,
            lead_ids, director_name, postcode_area
        Empty list if no groups found or on error.
    """
    groups_found = []

    conn = db.get_connection()
    try:
        leads = _fetch_all_care_leads(conn)
        if not leads:
            log.info("identify_operator_groups — no care leads found")
            return []

        log.info("identify_operator_groups — scanning %s care leads", len(leads))

        # ── Strategy 1: Director name matching ────────────────
        director_map: dict[str, list] = {}

        for lead in leads:
            raw_director = lead.get("director_name") or ""
            if not raw_director:
                continue
            norm = _normalise_director_name(raw_director)
            if not norm:
                continue
            if norm not in director_map:
                director_map[norm] = []
            director_map[norm].append(lead)

        for director_norm, director_leads in director_map.items():
            if len(director_leads) >= MIN_SITES_FOR_GROUP:
                # Use the most common business name prefix as group name
                names = [l.get("business_name") or "" for l in director_leads]
                # Find common prefix word if any
                words_sets = [set(n.lower().split()) for n in names if n]
                if words_sets:
                    common_words = words_sets[0].intersection(*words_sets[1:])
                    common_words -= {"care", "home", "house", "the", "and",
                                     "ltd", "limited", "services", "group"}
                    group_label = (
                        next(iter(common_words)).title()
                        if common_words
                        else director_norm.title()
                    )
                else:
                    group_label = director_norm.title()

                group_name = f"{group_label} Group"
                lead_ids   = [l["lead_id"] for l in director_leads]

                saved = _save_operator_group(
                    group_name    = group_name,
                    group_type    = "director_match",
                    site_count    = len(director_leads),
                    lead_ids      = lead_ids,
                    director      = director_norm.title(),
                    postcode_area = None,
                    conn          = conn,
                )

                if saved:
                    groups_found.append({
                        "group_name":   group_name,
                        "group_type":   "director_match",
                        "site_count":   len(director_leads),
                        "lead_ids":     lead_ids,
                        "director_name": director_norm.title(),
                        "postcode_area": None,
                    })
                    log.info(
                        "identify_operator_groups — director group: %s (%s sites)",
                        group_name, len(director_leads),
                    )

        # ── Strategy 2: Companies House prefix matching ────────
        ch_prefix_map: dict[str, list] = {}

        for lead in leads:
            ch_num = lead.get("company_number") or ""
            if not ch_num or len(ch_num) < 3:
                continue
            prefix = ch_num[:CH_PREFIX_LENGTH].upper()
            # Only cluster numeric prefixes (same filing block)
            if not re.match(r"^\d{2}$", prefix):
                continue
            if prefix not in ch_prefix_map:
                ch_prefix_map[prefix] = []
            ch_prefix_map[prefix].append(lead)

        for prefix, prefix_leads in ch_prefix_map.items():
            if len(prefix_leads) >= MIN_SITES_FOR_GROUP:
                group_name = f"CH-{prefix} Care Group"
                lead_ids   = [l["lead_id"] for l in prefix_leads]

                saved = _save_operator_group(
                    group_name    = group_name,
                    group_type    = "ch_prefix",
                    site_count    = len(prefix_leads),
                    lead_ids      = lead_ids,
                    director      = None,
                    postcode_area = None,
                    conn          = conn,
                )

                if saved:
                    groups_found.append({
                        "group_name":    group_name,
                        "group_type":    "ch_prefix",
                        "site_count":    len(prefix_leads),
                        "lead_ids":      lead_ids,
                        "director_name": None,
                        "postcode_area": None,
                    })
                    log.info(
                        "identify_operator_groups — CH prefix group: %s (%s sites)",
                        group_name, len(prefix_leads),
                    )

        # ── Strategy 3: Address postcode cluster ──────────────
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
            if len(area_leads) >= MIN_SITES_FOR_GROUP:
                group_name = f"{area} Regional Care Cluster"
                lead_ids   = [l["lead_id"] for l in area_leads]

                saved = _save_operator_group(
                    group_name    = group_name,
                    group_type    = "address_cluster",
                    site_count    = len(area_leads),
                    lead_ids      = lead_ids,
                    director      = None,
                    postcode_area = area,
                    conn          = conn,
                )

                if saved:
                    groups_found.append({
                        "group_name":    group_name,
                        "group_type":    "address_cluster",
                        "site_count":    len(area_leads),
                        "lead_ids":      lead_ids,
                        "director_name": None,
                        "postcode_area": area,
                    })
                    log.info(
                        "identify_operator_groups — postcode cluster: %s (%s sites)",
                        group_name, len(area_leads),
                    )

        log.info(
            "identify_operator_groups — complete. %s groups detected.",
            len(groups_found),
        )
        return groups_found

    except Exception as exc:
        log.error("identify_operator_groups error=%s", exc)
        return []
    finally:
        conn.close()


def get_group_profile(group_name: str) -> dict:
    """
    Retrieve full profile for a named operator group,
    including all member leads and their scores.

    Args:
        group_name: Exact group name as stored in care_operator_groups.

    Returns:
        dict with keys:
            group_name, group_type, site_count,
            director_name, postcode_area, detected_at,
            leads (list of lead summary dicts)
        Empty dict if not found or on error.
    """
    import json as _json

    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  group_name, group_type, site_count,
                    lead_ids, director_name, postcode_area,
                    detected_at
            FROM    care_operator_groups
            WHERE   group_name = ?
            LIMIT   1
            """,
            (group_name,),
        )
        row = cur.fetchone()
        if not row:
            log.warning("get_group_profile — group not found: %s", group_name)
            return {}

        profile = dict(row)

        # Decode lead_ids JSON
        try:
            lead_ids = _json.loads(profile.get("lead_ids") or "[]")
        except Exception:
            lead_ids = []

        # Fetch member lead summaries
        leads = []
        for lead_id in lead_ids:
            lcur = conn.execute(
                """
                SELECT  l.id AS lead_id,
                        l.business_name,
                        l.address,
                        l.website,
                        l.director_name,
                        l.company_number,
                        co.opportunity_score,
                        co.opportunity_type,
                        co.urgency_level
                FROM    leads l
                LEFT    JOIN care_home_opportunities co
                        ON  co.lead_id = l.id
                WHERE   l.id = ?
                LIMIT   1
                """,
                (lead_id,),
            )
            lrow = lcur.fetchone()
            if lrow:
                leads.append(dict(lrow))

        profile["leads"]    = leads
        profile["lead_ids"] = lead_ids   # keep as list not JSON string
        return profile

    except Exception as exc:
        log.error("get_group_profile group=%s error=%s", group_name, exc)
        return {}
    finally:
        conn.close()


def get_enterprise_opportunities(min_sites: int = 3) -> list:
    """
    Return operator groups that meet or exceed a minimum site count.
    These represent enterprise-level sales opportunities.

    Args:
        min_sites: Minimum number of sites to qualify as enterprise.
                   Default 3.

    Returns:
        List of group dicts ordered by site_count descending.
        Empty list on error.
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """
            SELECT  group_name, group_type, site_count,
                    director_name, postcode_area, detected_at
            FROM    care_operator_groups
            WHERE   site_count >= ?
            ORDER   BY site_count DESC
            """,
            (min_sites,),
        )
        rows = [dict(row) for row in cur.fetchall()]
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


def link_homes_to_groups() -> int:
    """
    Write the operator_group name back onto each lead row
    for every detected group.

    Enables dashboards to filter leads by group without
    joining the care_operator_groups table every time.

    Returns:
        Number of lead rows successfully updated.
        Returns 0 on error.
    """
    import json as _json

    updated = 0
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT group_name, lead_ids FROM care_operator_groups"
        )
        groups = cur.fetchall()

        for group in groups:
            group_name = group["group_name"]
            try:
                lead_ids = _json.loads(group["lead_ids"] or "[]")
            except Exception:
                lead_ids = []

            for lead_id in lead_ids:
                try:
                    conn.execute(
                        """
                        UPDATE leads
                        SET    operator_group = ?
                        WHERE  id = ?
                        """,
                        (group_name, lead_id),
                    )
                    updated += 1
                except Exception as exc:
                    log.debug(
                        "link_homes_to_groups lead_id=%s — %s", lead_id, exc
                    )

        conn.commit()
        log.info("link_homes_to_groups — updated %s lead rows", updated)
        return updated

    except Exception as exc:
        log.error("link_homes_to_groups error=%s", exc)
        return 0
    finally:
        conn.close()