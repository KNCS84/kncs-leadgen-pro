# check.py
import sys
sys.path.insert(0, '.')

from engine.enricher import _CH_AVAILABLE, DDGS_AVAILABLE
print("Companies House available:", _CH_AVAILABLE)
print("DDGS available:           ", DDGS_AVAILABLE)
print()

from database import get_connection
conn = get_connection()
cur  = conn.cursor()

cur.execute("""
    SELECT
        business_name,
        phone,
        email,
        decision_maker_name,
        dm_source,
        website,
        enrichment_status,
        fit_score
    FROM leads
    ORDER BY id DESC
    LIMIT 10
""")

rows = cur.fetchall()
conn.close()

print(f"{'BUSINESS':<35} {'EMAIL':^30} {'PHONE':^18} {'DM NAME':^25} {'DM SOURCE':^20} {'SCORE':^6}")
print("-" * 140)

for row in rows:
    d = dict(row)
    print(
        f"{(d.get('business_name') or '')[:34]:<35} "
        f"{(d.get('email') or 'NO EMAIL')[:29]:^30} "
        f"{(d.get('phone') or 'NO PHONE')[:17]:^18} "
        f"{(d.get('decision_maker_name') or 'NO DM')[:24]:^25} "
        f"{(d.get('dm_source') or '-')[:19]:^20} "
        f"{str(d.get('fit_score') or '-'):^6}"
    )