import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("FIX 1 - Add online_booking column to leads")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

missing = {
    "online_booking":      "TEXT",
    "booking_system":      "TEXT",
    "has_online_booking":  "INTEGER DEFAULT 0",
}

for col, coltype in missing.items():
    if col not in cols:
        conn.execute(
            f"ALTER TABLE leads ADD COLUMN {col} {coltype}"
        )
        print(f"  Added: {col}")
    else:
        print(f"  Already exists: {col}")

conn.commit()
conn.close()

print()
print("=" * 60)
print("FIX 2 - Verify dental summary now clean")
print("=" * 60)

import importlib
import engine.dental.dental_intelligence as di
importlib.reload(di)

try:
    summary = di.get_intelligence_summary()
    print(f"Dental summary: {summary}")
except Exception as e:
    print(f"ERROR: {e}")

print()
print("=" * 60)
print("FIX 3 - Diagnose dental opp_score = 100 for all leads")
print("        Check _get_urgency_band thresholds")  
print("=" * 60)

with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    content = f.read()

lines = content.split('\n')

# Find _get_urgency_band
print("_get_urgency_band function:")
in_func = False
for i, line in enumerate(lines):
    if "def _get_urgency_band" in line:
        in_func = True
    if in_func:
        print(f"  Line {i+1}: {line}")
        if i > 0 and line.strip().startswith("def ") and "_get_urgency_band" not in line:
            break
        if in_func and (i > 0) and "return" in line and in_func:
            # Print a few more lines then stop
            pass

print()
# Find calculate_opportunity_score
print("calculate_opportunity_score function (first 40 lines):")
in_func2 = False
count = 0
for i, line in enumerate(lines):
    if "def calculate_opportunity_score" in line:
        in_func2 = True
        count = 0
    if in_func2:
        print(f"  Line {i+1}: {line}")
        count += 1
        if count > 40:
            print("  ... truncated")
            break

print()
print("=" * 60)
print("FIX 4 - Check dental opp_score distribution in DB")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

dist = conn.execute("""
    SELECT opportunity_score, urgency_band, COUNT(*)
    FROM dental_opportunities
    GROUP BY opportunity_score, urgency_band
    ORDER BY opportunity_score DESC
    LIMIT 10
""").fetchall()

print("opportunity_score | urgency_band | count")
for r in dist:
    print(f"  {str(r[0]):17s} | {str(r[1]):12s} | {r[2]}")

# Also check if refresh_intelligence sets a default score
print()
print("refresh_intelligence function (lines around scoring):")
in_ref = False
count2 = 0
for i, line in enumerate(lines):
    if "def refresh_intelligence" in line:
        in_ref = True
        count2 = 0
    if in_ref:
        print(f"  Line {i+1}: {line}")
        count2 += 1
        if count2 > 60:
            print("  ... truncated")
            break

conn.close()

print()
print("ALL DONE")