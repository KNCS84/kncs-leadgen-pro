# check_mislabeled.py — diagnostic only, makes NO changes
from database import get_connection

conn = get_connection()
cur = conn.cursor()

cur.execute("""
    SELECT id, business_name, city, country, market
    FROM leads
    WHERE country = 'United Kingdom'
    AND (
        LOWER(city) LIKE '%dallas%'
        OR LOWER(city) LIKE '%austin%'
    )
    LIMIT 20
""")

rows = cur.fetchall()
print(f"Found {len(rows)} suspicious rows (showing up to 20):\n")
for r in rows:
    print(dict(r))

conn.close()