import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("CHECK 1 - care dashboard applymap fix")
print("=" * 60)
with open("pages/care_intelligence_dashboard.py", "r",
          encoding="utf-8", errors="replace") as f:
    content = f.read()

if "applymap" in content:
    print("WARNING: applymap still present - fix did not work")
    for i, line in enumerate(content.split('\n')):
        if 'applymap' in line:
            print(f"  Line {i+1}: {line.strip()}")
else:
    print("applymap replaced with map - GOOD")

print()
print("=" * 60)
print("CHECK 2 - dental_opportunities populated")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

count = conn.execute(
    "SELECT COUNT(*) FROM dental_opportunities"
).fetchone()[0]
print(f"dental_opportunities rows: {count}")

dist = conn.execute(
    "SELECT urgency_band, COUNT(*) FROM dental_opportunities GROUP BY urgency_band"
).fetchall()
print(f"urgency_band distribution: {dist}")

sample = conn.execute(
    "SELECT lead_id, ai_score, urgency_band FROM dental_opportunities LIMIT 3"
).fetchall()
print(f"Sample rows: {sample}")

print()
print("=" * 60)
print("CHECK 3 - care_home_opportunities still populated")
print("=" * 60)
care_count = conn.execute(
    "SELECT COUNT(*) FROM care_home_opportunities"
).fetchone()[0]
print(f"care_home_opportunities rows: {care_count}")

care_dist = conn.execute(
    "SELECT urgency_band, COUNT(*) FROM care_home_opportunities GROUP BY urgency_band"
).fetchall()
print(f"urgency_band distribution: {care_dist}")

print()
print("=" * 60)
print("CHECK 4 - Test care get_top_opportunities still works")
print("=" * 60)
try:
    import engine.care.care_intelligence as ci
    rows = ci.get_top_opportunities(limit=5)
    print(f"get_top_opportunities returned: {len(rows)} rows")
    if rows:
        print(f"Keys: {list(rows[0].keys())}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

print()
print("=" * 60)
print("CHECK 5 - Test dental get_top_opportunities")
print("=" * 60)
try:
    import engine.dental.dental_intelligence as di
    rows2 = di.get_top_opportunities(limit=5)
    print(f"dental get_top_opportunities returned: {len(rows2)} rows")
    if rows2:
        print(f"Keys: {list(rows2[0].keys())}")
        print(f"First: {dict(rows2[0])}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

conn.close()
print()
print("ALL CHECKS COMPLETE")