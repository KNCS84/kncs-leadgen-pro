# manual_entry.py
# Manually enter verified data found through human research
# Run with: python manual_entry.py

import sys
sys.path.insert(0, '.')
from database import get_connection

conn = get_connection()
cur  = conn.cursor()
total = 0

VERIFIED_DATA = [
    {
        "id":      261,
        "name":    "DERBY COTTAGE CLINIC LIMITED",
        "website": "https://derbycottageclinic.com",
        "email":   "info@derbycottageclinic.com",
        "phone":   "01638577121",
    },
]

for entry in VERIFIED_DATA:
    cur.execute("""
        UPDATE leads SET
            website = CASE
                WHEN (website IS NULL OR website = '')
                THEN :website ELSE website
            END,
            email = CASE
                WHEN (email IS NULL OR email = '')
                THEN :email ELSE email
            END,
            phone = CASE
                WHEN (phone IS NULL OR phone = '')
                THEN :phone ELSE phone
            END,
            enrichment_status = 'complete',
            last_enriched_at  = datetime('now')
        WHERE id = :id
    """, entry)
    if cur.rowcount:
        print(f"  Updated ID {entry['id']}: {entry['name']}")
        print(f"    website: {entry['website']}")
        print(f"    email:   {entry['email']}")
        print(f"    phone:   {entry['phone']}")
        total += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total updated: {total}")