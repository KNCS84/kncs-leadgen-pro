import sys, os
sys.path.insert(0, os.getcwd())

import database as db

print("=" * 60)
print("STEP 1 - Check care_home_opportunities sample")
print("=" * 60)
conn = db.get_connection()
rows = conn.execute("""
    SELECT urgency_band, COUNT(*)
    FROM care_home_opportunities
    GROUP BY urgency_band
""").fetchall()
print("urgency_band distribution:", rows)

scores = conn.execute("""
    SELECT MIN(ai_score), MAX(ai_score), AVG(ai_score), COUNT(*)
    FROM care_home_opportunities
""").fetchone()
print("Score stats (min/max/avg/count):", scores)

sample = conn.execute("""
    SELECT lead_id, ai_score, urgency_band, scored_at
    FROM care_home_opportunities
    LIMIT 3
""").fetchall()
print("Sample rows:", sample)
conn.close()

print()
print("=" * 60)
print("STEP 2 - Test get_top_opportunities")
print("=" * 60)
try:
    import engine.care.care_intelligence as ci
    result = ci.get_top_opportunities(limit=5)
    print("SUCCESS - count:", len(result))
    if result:
        print("First item:", result[0])
    else:
        print("Result is EMPTY LIST")
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))

print()
print("=" * 60)
print("STEP 3 - Check care_operator_groups schema")
print("=" * 60)
conn = db.get_connection()
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(care_operator_groups)"
).fetchall()]
print("care_operator_groups columns:", cols)

if 'lead_id' not in cols:
    print("lead_id missing - adding it...")
    conn.execute(
        "ALTER TABLE care_operator_groups ADD COLUMN lead_id INTEGER"
    )
    if 'contact_lead_id' in cols:
        conn.execute(
            "UPDATE care_operator_groups SET lead_id = contact_lead_id"
        )
        print("Mapped lead_id from contact_lead_id")
    else:
        print("WARNING: no contact_lead_id found either")
        print("Available cols:", cols)
    conn.commit()
    print("care_operator_groups FIXED")
else:
    print("lead_id already exists - OK")
conn.close()

print()
print("=" * 60)
print("STEP 4 - Check care_acquisition_signals schema")
print("=" * 60)
conn = db.get_connection()
cols2 = [r[1] for r in conn.execute(
    "PRAGMA table_info(care_acquisition_signals)"
).fetchall()]
print("care_acquisition_signals columns:", cols2)
count = conn.execute(
    "SELECT COUNT(*) FROM care_acquisition_signals"
).fetchone()[0]
print("Row count:", count)
conn.close()

print()
print("=" * 60)
print("STEP 5 - Run dental refresh")
print("=" * 60)
try:
    from engine.dental.dental_intelligence import refresh_all_dental_intelligence
    result = refresh_all_dental_intelligence(batch_size=100)
    print("Dental result:", result)
except Exception as e:
    print("DENTAL ERROR:", type(e).__name__, str(e))

print()
print("=" * 60)
print("ALL STEPS COMPLETE")
print("=" * 60)