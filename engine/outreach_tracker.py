# engine/outreach_tracker.py
"""
Outreach tracking utilities.

This version does NOT import missing database functions.
It only uses functions that exist in database.py.
"""

from datetime import datetime, timedelta
from database import (
    update_lead_status,
    log_call,
    add_revenue,
    get_connection,
)

CALL_STATUSES = {
    "interested": "😊 Interested",
    "not_interested": "😔 Not Interested",
    "callback": "📅 Callback Requested",
    "no_answer": "📵 No Answer",
    "wrong_number": "❌ Wrong Number",
    "deal_closed": "🤝 Deal Closed",
}


def record_call_outcome(
    lead_id: int,
    status: str,
    notes: str = "",
    deal_value: float = None,
    service_type: str = None,
    callback_date: str = None,
    duration_seconds: int = 0
) -> dict:
    """
    Record the outcome of a call.
    """
    result = {"success": False, "message": ""}

    try:
        # Update lead status
        update_lead_status(lead_id, status, notes)

        # Log the call
        log_call(lead_id, status, notes, duration_seconds)

        # If deal closed, store revenue
        if status == "deal_closed" and deal_value is not None:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT business_name FROM leads WHERE id = ?",
                (lead_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                add_revenue(
                    lead_id=lead_id,
                    business_name=row[0],
                    amount=deal_value,
                    service_type=service_type or "Website",
                    notes=notes
                )

        # If callback, save follow-up date
        if status == "callback" and callback_date:
            conn = get_connection()
            conn.execute("""
                UPDATE leads
                SET follow_up_date = ?,
                    updated_at = datetime('now')
                WHERE id = ?
            """, (callback_date, lead_id))
            conn.commit()
            conn.close()

        result["success"] = True
        result["message"] = f"Call recorded: {CALL_STATUSES.get(status, status)}"
        return result

    except Exception as e:
        result["message"] = f"Error: {str(e)}"
        return result


def get_todays_callbacks() -> list:
    """Get all callback leads due today or earlier."""
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT * FROM leads
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= ?
          AND contact_status = 'callback'
        ORDER BY follow_up_date ASC
    """, (today,))

    leads = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return leads


def get_next_lead_to_call(min_score: int = 50) -> dict:
    """
    Get the next best lead to call.
    Priority:
    1. callbacks due
    2. uncontacted high-score leads with phone numbers
    """
    # Callback first
    callbacks = get_todays_callbacks()
    if callbacks:
        return callbacks[0]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM leads
        WHERE contacted = 0
          AND opportunity_score >= ?
          AND phone IS NOT NULL
          AND phone != ''
        ORDER BY opportunity_score DESC
        LIMIT 1
    """, (min_score,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


# Compatibility alias
def log_outreach(*args, **kwargs):
    """
    Backward-compatible alias.
    Some older files may call this.
    """
    return record_call_outcome(*args, **kwargs)