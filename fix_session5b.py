import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("STEP 1 - Print actual data values (fix Row printing)")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None  # Force plain tuples

rows = conn.execute("""
    SELECT urgency_band, COUNT(*)
    FROM care_home_opportunities
    GROUP BY urgency_band
""").fetchall()
print("urgency_band distribution:", rows)

scores = conn.execute("""
    SELECT MIN(ai_score), MAX(ai_score), ROUND(AVG(ai_score),1), COUNT(*)
    FROM care_home_opportunities
""").fetchone()
print("Score stats (min/max/avg/count):", scores)

sample = conn.execute("""
    SELECT lead_id, ai_score, urgency_band, scored_at
    FROM care_home_opportunities
    LIMIT 3
""").fetchall()
print("Sample rows:")
for r in sample:
    print("  ", r)
conn.close()

print()
print("=" * 60)
print("STEP 2 - Check what keys dashboard expects vs what")
print("         get_top_opportunities actually returns")
print("=" * 60)
import engine.care.care_intelligence as ci
result = ci.get_top_opportunities(limit=5)
if result:
    print("Keys returned by get_top_opportunities:")
    for k in result[0].keys():
        print("  -", k, ":", result[0][k])
else:
    print("Empty result")

print()
print("=" * 60)
print("STEP 3 - Check care_acquisition_signals for detected_at")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(care_acquisition_signals)"
).fetchall()]
print("Columns:", cols)

# Add detected_at if missing (code queries cas.detected_at)
if 'detected_at' not in cols:
    print("Adding detected_at column...")
    conn.execute(
        "ALTER TABLE care_acquisition_signals ADD COLUMN detected_at TEXT"
    )
    # Map from created_at if it exists
    if 'created_at' in cols:
        conn.execute(
            "UPDATE care_acquisition_signals SET detected_at = created_at"
        )
        print("Mapped detected_at from created_at")
    conn.commit()
    print("care_acquisition_signals FIXED")
else:
    print("detected_at already exists - OK")
conn.close()

print()
print("=" * 60)
print("STEP 4 - Fix dental_intelligence category->industry")
print("         by checking what dental_intelligence queries")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None
# Test the exact query dental_intelligence uses
try:
    rows = conn.execute("""
        SELECT id, company_name, industry, city, country
        FROM leads
        WHERE LOWER(industry) LIKE '%dental%'
        LIMIT 3
    """).fetchall()
    print("Dental leads found with industry field:", len(rows))
    for r in rows:
        print("  ", r)
except Exception as e:
    print("ERROR:", e)

# Also check what columns leads table has
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]
print("leads table columns:", cols)
conn.close()

print()
print("=" * 60)
print("STEP 5 - Read dental_intelligence.py to find category ref")
print("=" * 60)
try:
    with open("engine/dental/dental_intelligence.py", "r") as f:
        content = f.read()
    # Find lines with 'category'
    lines = content.split('\n')
    print("Lines referencing 'category':")
    for i, line in enumerate(lines):
        if 'category' in line.lower():
            print(f"  Line {i+1}: {line}")
    print()
    # Find _get_all_dental_leads function
    print("_get_all_dental_leads function:")
    in_func = False
    for i, line in enumerate(lines):
        if '_get_all_dental_leads' in line:
            in_func = True
        if in_func:
            print(f"  {i+1}: {line}")
            if i > 0 and in_func and line.strip() == '' and i > lines.index(line):
                pass
            if in_func and i > 10:
                # Print 20 lines of the function
                pass
        if in_func and line.strip().startswith('def ') and '_get_all_dental_leads' not in line:
            break
except Exception as e:
    print("Could not read file:", e)

print()
print("=" * 60)
print("ALL STEPS COMPLETE")
print("=" * 60)