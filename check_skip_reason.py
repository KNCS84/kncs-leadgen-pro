import sqlite3

conn = sqlite3.connect("data/leads.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT id, business_name, city, country, phone
    FROM leads
    WHERE business_name LIKE '%Roto-Rooter%'
       OR business_name LIKE '%Drain Service%'
""")
rows = cur.fetchall()
print("Existing matches found:", len(rows))
for r in rows:
    print(dict(r))

print()
print("--- Cities currently stored under country='United Kingdom' ---")
cur.execute("""
    SELECT DISTINCT city, COUNT(*) as n
    FROM leads
    WHERE country = 'United Kingdom'
    GROUP BY city
    ORDER BY n DESC
""")
for r in cur.fetchall():
    print(dict(r))

conn.close()