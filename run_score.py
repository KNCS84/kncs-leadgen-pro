import sqlite3

conn = sqlite3.connect("data/leads.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== BEFORE MIGRATION ===")
cur.execute("""
    SELECT lead_priority, COUNT(*) as count 
    FROM leads 
    GROUP BY lead_priority
""")
for r in cur.fetchall():
    print(f"  priority='{r['lead_priority']}' → {r['count']} leads")

# Migrate old labels to new labels
print("\n=== MIGRATING OLD LABELS ===")

cur.execute("""
    UPDATE leads SET lead_priority = 'high'
    WHERE lead_priority = 'hot'
""")
print(f"  hot → high: {cur.rowcount} leads updated")

cur.execute("""
    UPDATE leads SET lead_priority = 'medium'
    WHERE lead_priority = 'warm'
""")
print(f"  warm → medium: {cur.rowcount} leads updated")

cur.execute("""
    UPDATE leads SET lead_priority = 'low'
    WHERE lead_priority = 'cold'
""")
print(f"  cold → low: {cur.rowcount} leads updated")

cur.execute("""
    UPDATE leads SET lead_priority = 'unscored'
    WHERE lead_priority = 'unqualified'
""")
print(f"  unqualified → unscored: {cur.rowcount} leads updated")

conn.commit()

print("\n=== AFTER MIGRATION ===")
cur.execute("""
    SELECT lead_priority, COUNT(*) as count 
    FROM leads 
    GROUP BY lead_priority
""")
for r in cur.fetchall():
    print(f"  priority='{r['lead_priority']}' → {r['count']} leads")

print("\n=== DASHBOARD SHOULD NOW SEE ===")
cur.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN lead_priority = 'high' THEN 1 ELSE 0 END) as high,
        SUM(CASE WHEN lead_priority = 'medium' THEN 1 ELSE 0 END) as medium,
        SUM(CASE WHEN lead_priority = 'low' THEN 1 ELSE 0 END) as low,
        SUM(CASE WHEN fit_score >= 8 THEN 1 ELSE 0 END) as high_fit,
        SUM(CASE WHEN email != '' AND email IS NOT NULL THEN 1 ELSE 0 END) as with_email,
        SUM(CASE WHEN phone != '' AND phone IS NOT NULL THEN 1 ELSE 0 END) as with_phone
    FROM leads
""")
r = cur.fetchone()
print(f"  Total:     {r['total']}")
print(f"  High:      {r['high']}")
print(f"  Medium:    {r['medium']}")
print(f"  Low:       {r['low']}")
print(f"  High Fit:  {r['high_fit']}")
print(f"  Emails:    {r['with_email']}")
print(f"  Phones:    {r['with_phone']}")

conn.close()
print("\nMigration complete")