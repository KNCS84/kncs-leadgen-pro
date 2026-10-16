# fix_flagged.py
# Fixes confirmed wrong data identified from check_db.py flags
# Run with: python fix_flagged.py

import sys
sys.path.insert(0, '.')
from database import get_connection

conn = get_connection()
cur  = conn.cursor()
total = 0

print("Fixing flagged leads...\n")

# ── Wrong emails — email domain unrelated to website domain ──

# ID 323: HAPPY EARS — website is earwaxremovalnottingham.net
#         email is enquiries@happy-ears.uk — different business
cur.execute(
    "UPDATE leads SET email = '' "
    "WHERE id = 323 AND email = 'enquiries@happy-ears.uk'"
)
if cur.rowcount:
    print(f"  Cleared ID 323 email: enquiries@happy-ears.uk")
    total += cur.rowcount

# ID 286: ANCHOR FINANCIAL — website is anchorwealth.co.uk
#         email is info@anchor-finance.co.uk — different entity
cur.execute(
    "UPDATE leads SET email = '' "
    "WHERE id = 286 AND email = 'info@anchor-finance.co.uk'"
)
if cur.rowcount:
    print(f"  Cleared ID 286 email: info@anchor-finance.co.uk")
    total += cur.rowcount

# ID 196: Willow Lodge — website is brhw.co.uk
#         email is enquiries@blueribbon.co.uk — different org
cur.execute(
    "UPDATE leads SET email = '' "
    "WHERE id = 196 AND email = 'enquiries@blueribbon.co.uk'"
)
if cur.rowcount:
    print(f"  Cleared ID 196 email: enquiries@blueribbon.co.uk")
    total += cur.rowcount

# ── Portal websites — not the practice's own site ────────────

# ID 301: Night Dental — portal.dental platform, not real site
cur.execute(
    "UPDATE leads SET website = '' "
    "WHERE id = 301 AND website LIKE '%portal.dental%'"
)
if cur.rowcount:
    print(f"  Cleared ID 301 website: viva-birmingham.portal.dental")
    total += cur.rowcount

# ID 300: The Dentist at Liberty Place — portal.dental platform
cur.execute(
    "UPDATE leads SET website = '' "
    "WHERE id = 300 AND website LIKE '%portal.dental%'"
)
if cur.rowcount:
    print(f"  Cleared ID 300 website: rodericksdentalpartners.portal.dental")
    total += cur.rowcount

# ID 321: CENTRAL NOTTINGHAMSHIRE — website is community pharmacy
#         org, not the clinical services company. Clearing so the
#         system does not keep searching for cpnotts.org emails.
cur.execute(
    "UPDATE leads SET website = '' "
    "WHERE id = 321 "
    "AND website LIKE '%communitypharmacy%'"
)
if cur.rowcount:
    print(f"  Cleared ID 321 website: nottinghamshire.communitypharmacy.org.uk")
    total += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total cleared: {total}")