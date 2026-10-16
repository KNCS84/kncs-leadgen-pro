import sqlite3

conn = sqlite3.connect('data/leads.db')

# Patch: rename 'done' → 'complete' so Call Center can find leads
result = conn.execute("""
    UPDATE leads
    SET enrichment_status = 'complete'
    WHERE enrichment_status = 'done'
""")
print("Updated 'done' → 'complete':", result.rowcount, "leads")

result2 = conn.execute("""
    UPDATE leads
    SET enrichment_status = 'complete'
    WHERE enrichment_status = 'attempted'
""")
print("Updated 'attempted' → 'complete':", result2.rowcount, "leads")

conn.commit()

# Verify
rows = conn.execute("""
    SELECT enrichment_status, COUNT(*) as count
    FROM leads
    GROUP BY enrichment_status
    ORDER BY count DESC
""").fetchall()
print("")
print("=== AFTER PATCH ===")
for r in rows:
    print(f"  '{r[0]}' : {r[1]}")

conn.close()
print("")
print("Done. Hard refresh browser now.")