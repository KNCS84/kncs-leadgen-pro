# fix_bad_emails.py
# One-time cleanup — clears confirmed bad emails from the database
# Run with: python fix_bad_emails.py

import sys
sys.path.insert(0, '.')
from database import get_connection

BAD_EMAILS = [
    'team@fannoxnetwork.com',
    'jpowers@uga.edu',
    'bonus@bonus.fo',
    'info@alikamusic.eu',
    'info@mysite.com',
    'info@autodistribution.com',
    'edihospital@agcaregroup.com',
]

conn = get_connection()
cur  = conn.cursor()

cleared = 0
for email in BAD_EMAILS:
    cur.execute(
        "UPDATE leads SET email = '' WHERE email = ?",
        (email,)
    )
    if cur.rowcount > 0:
        print(f"  Cleared: {email} ({cur.rowcount} row)")
        cleared += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total cleared: {cleared}")