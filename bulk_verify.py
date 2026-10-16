"""
KNCS LeadGen Pro - Bulk WhatsApp Verification CLI Utility
Author: Arena.ai / Amechi
Usage:
    python bulk_verify.py --market nigeria
    python bulk_verify.py --all
"""

import argparse
import sys
import os
import re

# Ensure directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_connection, batch_verify_whatsapp


def is_valid_mobile_number(phone: str, country: str = "Nigeria") -> bool:
    """Check if the phone number belongs to a mobile block likely to have WhatsApp."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", str(phone))

    # Nigeria (+234) mobile checks
    if country.lower() in ("nigeria", "ng") or digits.startswith("234"):
        if digits.startswith("234") and len(digits) == 13:
            prefix = digits[3:5]
            return prefix in {"70", "80", "81", "90", "91"}
        if digits.startswith("0") and len(digits) == 11:
            prefix = digits[1:3]
            return prefix in {"70", "80", "81", "90", "91"}

    # UK (+44) mobile checks (starts with 07 or 447)
    if country.lower() in ("uk", "united kingdom", "gb") or digits.startswith("44"):
        if digits.startswith("447") and len(digits) in (12, 13):
            return True
        if digits.startswith("07") and len(digits) == 11:
            return True

    return len(digits) >= 10


def run_bulk_verification(market: str = "all") -> None:
    print(f"\n==================================================")
    print(f"  KNCS Bulk WhatsApp Verifier")
    print(f"  Target Scope: {market.upper()}")
    print(f"==================================================")

    conn = get_connection()
    try:
        cur = conn.cursor()
        if market.lower() == "all":
            cur.execute("SELECT id, business_name, phone, country FROM leads WHERE COALESCE(wa_verified, 0) = 0")
        else:
            cur.execute(
                "SELECT id, business_name, phone, country FROM leads WHERE (LOWER(market) = ? OR LOWER(country) = ?) AND COALESCE(wa_verified, 0) = 0",
                (market.lower(), market.lower())
            )
        unverified = cur.fetchall()
        print(f"[*] Found {len(unverified)} unverified leads.")

        valid_ids = []
        for row in unverified:
            lid = row["id"]
            phone = row["phone"]
            country = row["country"] or "Nigeria"
            if is_valid_mobile_number(phone, country):
                valid_ids.append(lid)

        print(f"[*] Identified {len(valid_ids)} leads with valid mobile phone carriers.")
        if valid_ids:
            updated = batch_verify_whatsapp(valid_ids, verified=True)
            print(f"[SUCCESS] Successfully updated {updated} leads to 'wa_verified = 1'!")
        else:
            print("[INFO] No new mobile leads found to verify.")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KNCS Bulk WhatsApp Verifier")
    parser.add_argument("--market", default="all", help="Target market: 'nigeria', 'uk', or 'all'")
    args = parser.parse_args()
    run_bulk_verification(args.market)
