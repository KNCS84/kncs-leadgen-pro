# check_country_breakdown.py — diagnostic only, makes NO changes
from database import get_connection

conn = get_connection()
cur = conn.cursor()

print("=== Country value breakdown (all leads) ===\n")
cur.execute("""
    SELECT
        COALESCE(NULLIF(TRIM(country), ''), '(BLANK)') AS country_val,
        COALESCE(NULLIF(TRIM(market), ''), '(BLANK)') AS market_val,
        COUNT(*) as total
    FROM leads
    GROUP BY country_val, market_val
    ORDER BY total DESC
""")

rows = cur.fetchall()
for r in rows:
    print(dict(r))

print("\n=== Sample of leads with BLANK country (up to 15) ===\n")
cur.execute("""
    SELECT id, business_name, city, country, market
    FROM leads
    WHERE country IS NULL OR TRIM(country) = ''
    LIMIT 15
""")
for r in cur.fetchall():
    print(dict(r))

print("\n=== Sample of leads with city containing 'dallas' or 'austin' (any country) ===\n")
cur.execute("""
    SELECT id, business_name, city, country, market
    FROM leads
    WHERE LOWER(city) LIKE '%dallas%' OR LOWER(city) LIKE '%austin%'
    LIMIT 15
""")
for r in cur.fetchall():
    print(dict(r))

conn.close()