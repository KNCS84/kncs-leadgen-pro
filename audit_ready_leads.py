import sys
sys.path.insert(0, '.')
from database import get_connection

conn = get_connection()
cur  = conn.cursor()

cur.execute("""
    SELECT id, business_name, decision_maker_name, email, phone, industry
    FROM leads
    WHERE (email IS NOT NULL AND email != '')
    AND   (phone IS NOT NULL AND phone != '')
    AND   (decision_maker_name IS NOT NULL AND decision_maker_name != '')
    AND   (
        country IS NULL
        OR TRIM(country) = ''
        OR LOWER(TRIM(country)) IN (
            'united kingdom','uk','england',
            'wales','scotland','northern ireland',
            'great britain','gb'
        )
    )
    ORDER BY business_name
""")

rows = cur.fetchall()
conn.close()

care   = []
dental = []
other  = []

for r in rows:
    ind = (r[5] or '').lower()
    if any(w in ind for w in ['care','nurs','resid']):
        care.append(r)
    elif 'dent' in ind:
        dental.append(r)
    else:
        other.append(r)

print("=" * 60)
print("LEADS READY FOR OUTREACH TODAY")
print("=" * 60)
print(f"TOTAL READY: {len(rows)}")
print(f"Care homes:  {len(care)}")
print(f"Dental:      {len(dental)}")
print(f"Other:       {len(other)}")
print()

for label, data in [('CARE HOMES', care), ('DENTAL', dental), ('OTHER', other)]:
    print(f"--- {label} ---")
    for r in data:
        print(f"  ID {r[0]} | {r[1]}")
        print(f"    DM:    {r[2]}")
        print(f"    Email: {r[3]}")
        print(f"    Phone: {r[4]}")
        print()

print("=" * 60)