# database_patch.py
# Adds the three functions missing from database.py
# Called by pages/leads.py
# Safe to run — only adds, never modifies existing functions

import sqlite3
import logging
from database import get_connection

logger = logging.getLogger(__name__)


def update_lead_status(lead_id: int, status: str) -> bool:
    """
    Update the contact_status field for a lead.
    Called by pages/leads.py Mark Called button.
    Returns True on success, False on failure.
    """
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "UPDATE leads SET contact_status = ? WHERE id = ?",
            (status, lead_id),
        )
        conn.commit()
        logger.info("update_lead_status: lead %s → %s", lead_id, status)
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_status failed: %s", exc)
        return False


def update_lead_email(lead_id: int, email: str) -> bool:
    """
    Update the email field for a lead.
    Called by pages/leads.py Find Email button.
    Returns True on success, False on failure.
    """
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "UPDATE leads SET email = ? WHERE id = ?",
            (email, lead_id),
        )
        conn.commit()
        logger.info("update_lead_email: lead %s → %s", lead_id, email)
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_email failed: %s", exc)
        return False


def update_lead_enrichment(lead_id: int, enrichment_data: dict) -> bool:
    """
    Update enrichment fields for a lead.
    Called by pages/leads.py Re-Enrich button.
    Accepts a dict of field→value pairs and updates all of them.
    Returns True on success, False on failure.
    """
    if not enrichment_data:
        return False

    # Only update columns that actually exist in leads table
    allowed_columns = {
        "email", "phone", "website", "address",
        "decision_maker_name", "dm_source",
        "enrichment_status", "opportunity_score",
        "cqc_rating", "cqc_url", "cqc_location_id",
        "registered_manager", "director_name",
        "social_facebook_url", "social_instagram_url",
        "social_linkedin_url", "website_discovered",
        "website_discovery_source", "wa_status",
        "whatsapp_number", "bed_count", "urgency_band",
    }

    fields = {
        k: v for k, v in enrichment_data.items()
        if k in allowed_columns
    }

    if not fields:
        logger.warning(
            "update_lead_enrichment: no valid fields for lead %s", lead_id
        )
        return False

    try:
        conn        = get_connection()
        cur         = conn.cursor()
        set_clause  = ", ".join(f"{col} = ?" for col in fields)
        values      = list(fields.values()) + [lead_id]
        cur.execute(
            f"UPDATE leads SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
        logger.info(
            "update_lead_enrichment: lead %s updated %s fields",
            lead_id, len(fields),
        )
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_enrichment failed: %s", exc)
        return False