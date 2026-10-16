# fix_data_quality.py
# Clears confirmed bad emails and junk decision maker names
# Run with: python fix_data_quality.py

import sys
sys.path.insert(0, '.')
from database import get_connection

conn = get_connection()
cur  = conn.cursor()
total = 0

# ── Bad emails — domain has no relation to the business ──
bad_emails_by_id = {
    321: "nickhunter@cpnotts.org",        # NHS org, not the clinic
    293: "clientservices@atfsadvisers.com", # atfs ≠ atlas
    181: "info@krinvestcare.com",           # krinvest ≠ andrews court
    133: "bev.dumont@adlcare.com",          # adlcare ≠ allambie
    173: "info@kanesburycare.co.uk",        # kanesbury ≠ regency manor
}

print("Clearing bad emails...")
for lead_id, email in bad_emails_by_id.items():
    cur.execute(
        "UPDATE leads SET email = '' WHERE id = ? AND email = ?",
        (lead_id, email)
    )
    if cur.rowcount > 0:
        print(f"  Cleared ID {lead_id}: {email}")
        total += cur.rowcount

# ── Junk decision maker names — website nav text, company names ──
junk_dm_ids = [
    295,  # Auxilium Administration U  — company name
    279,  # Emandar Nominees Limited   — company name
    129,  # Events Newsletter Help     — nav text
    127,  # Christmas Cracker Cup      — nav text
    124,  # Christmas Cracker Cup      — nav text
    123,  # Latest News Knowledge      — nav text
    122,  # Give Feedback Anonymously  — nav text
    114,  # us My account My           — nav text
    65,   # us My account My           — nav text
    72,   # Us Crops Services Breeder  — nav text
    110,  # Book Appointment           — nav text
    107,  # Find Us                    — nav text
    103,  # Megasmile Dental Clinics   — company name not person
    85,   # Ambeco Global Constructio  — company name
    84,   # Construction Company       — generic text
]

print("\nClearing junk decision maker names...")
for lead_id in junk_dm_ids:
    cur.execute(
        "SELECT business_name, decision_maker_name FROM leads WHERE id = ?",
        (lead_id,)
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE leads SET decision_maker_name = '', dm_source = '' "
            "WHERE id = ?",
            (lead_id,)
        )
        if cur.rowcount > 0:
            print(f"  Cleared ID {lead_id} ({row[0][:40]}): {row[1]}")
            total += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total fields cleared: {total}")