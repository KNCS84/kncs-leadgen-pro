from database import get_connection

conn = get_connection()
cur = conn.cursor()
cur.execute("""
    SELECT id, keyword, city, source, status,
           leads_found, inserted, skipped, updated,
           message, started_at, finished_at, duration_seconds
    FROM scrape_jobs
    ORDER BY id DESC
    LIMIT 10
""")
rows = cur.fetchall()
print()
for r in rows:
    print(f"ID={r[0]} | {r[1]} | {r[2]} | {r[3]}")
    print(f"  status={r[4]} | found={r[5]} inserted={r[6]} skipped={r[7]} updated={r[8]}")
    print(f"  message={r[9]}")
    print(f"  started={r[10]} finished={r[11]} duration={r[12]}s")
    print()
conn.close()