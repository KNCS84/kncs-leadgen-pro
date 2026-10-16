# fix_portal_emails.py
# Clears emails that come from portal/wrong domains
# Run with: python fix_portal_emails.py

import sys
sys.path.insert(0, '.')
from database import get_connection

conn = get_connection()
cur  = conn.cursor()
total = 0

# ID 301 — david.collier@viva-birmingham.portals.com
# This is a portal platform domain, not the dental practice
cur.execute(
    "UPDATE leads SET email = '' "
    "WHERE id = 301 AND email LIKE '%viva-birmingham%'"
)
if cur.rowcount > 0:
    print(f"  Cleared ID 301: viva-birmingham portal email")
    total += cur.rowcount

# ID 300 — farimah.hassan@rodericksdentalpartners.co.uk
# Check this first — rodericksdentalpartners could be legitimate
# The lead is 'The Dentist at Liberty Place'
# rodericksdentalpartners.co.uk is a different dental practice
cur.execute(
    "SELECT email FROM leads WHERE id = 300"
)
row = cur.fetchone()
if row:
    email = row[0] or ""
    print(f"  ID 300 current email: {email}")
    if "rodericksdentalpartn" in email.lower():
        cur.execute(
            "UPDATE leads SET email = '' WHERE id = 300"
        )
        if cur.rowcount > 0:
            print(f"  Cleared ID 300: rodericks dental email (wrong practice)")
            total += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total cleared: {total}")