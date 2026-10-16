with open('database.py', 'r', encoding='utf-8') as f:
    text = f.read()

if 'def update_lead_enrichment(' not in text:
    funcs_code = """
# ─────────────────────────────────────────────────────────
# v10.8 Missing Lead Update Helpers (`update_lead_enrichment`, `update_lead_fit_score`, etc.)
# ─────────────────────────────────────────────────────────

def update_lead_status(lead_id: int, status: str) -> bool:
    try:
        conn = get_connection()
        if not conn: return False
        cur = conn.cursor()
        cur.execute("UPDATE leads SET contact_status = ? WHERE id = ?", (status, lead_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_status failed: %s", exc)
        return False
    finally:
        if conn: conn.close()

def update_lead_email(lead_id: int, email: str) -> bool:
    try:
        conn = get_connection()
        if not conn: return False
        cur = conn.cursor()
        cur.execute("UPDATE leads SET email = ? WHERE id = ?", (email, lead_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_email failed: %s", exc)
        return False
    finally:
        if conn: conn.close()

def update_lead_enrichment(lead_id: int, enrichment_data: dict) -> bool:
    if not enrichment_data or not isinstance(enrichment_data, dict):
        return False
    try:
        conn = get_connection()
        if not conn: return False
        cur = conn.cursor()
        allowed = {
            "phone", "phone_confidence", "phone_source", "phone_verified",
            "phone_country_code", "email", "email_confidence", "emails_found_count",
            "phones_found_count", "decision_maker_name", "decision_maker_title",
            "decision_maker_confidence", "decision_maker_source", "dm_source",
            "website", "website_active", "website_audit_score", "website_discovered",
            "website_discovery_source", "website_discovery_confidence",
            "social_linkedin_url", "social_facebook_url", "social_instagram_url",
            "cqc_rating", "cqc_location_id", "cqc_url", "enrichment_status",
            "enrichment_notes", "company_number", "company_registration_number",
            "director_name", "last_enriched_at"
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
        cur.execute(f"UPDATE leads SET {', '.join(set_clauses)} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_enrichment failed: %s", exc)
        return False
    finally:
        if conn: conn.close()

def update_lead_fit_score(lead_id: int, fit_score: int, priority: str = "", best_channel: str = "", fit_reason: str = "") -> bool:
    try:
        conn = get_connection()
        if not conn: return False
        cur = conn.cursor()
        from datetime import datetime, timezone
        cur.execute(
            \"\"\"
            UPDATE leads
            SET fit_score = ?,
                lead_priority = COALESCE(NULLIF(?, ''), lead_priority),
                best_channel = COALESCE(NULLIF(?, ''), best_channel),
                fit_reason = COALESCE(NULLIF(?, ''), fit_reason),
                fit_scored_at = ?
            WHERE id = ?
            \"\"\",
            (fit_score, priority, best_channel, fit_reason, datetime.now(timezone.utc).isoformat(), lead_id)
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        logger.error("update_lead_fit_score failed: %s", exc)
        return False
    finally:
        if conn: conn.close()
"""
    text = text + "\n" + funcs_code
    with open('database.py', 'w', encoding='utf-8') as out:
        out.write(text)
    print("Added update_lead_enrichment and update_lead_fit_score to database.py cleanly!")
