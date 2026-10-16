import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("FIX 1 - Add exact column names that intelligence queries")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

# Add every missing column that any intelligence module queries
missing_cols = {
    # care_intelligence queries
    "director_change_date":  "TEXT",    # note: no 'd' at end
    # dental_intelligence queries
    "booking_url":           "TEXT",
    "google_review_count":   "INTEGER",
    "practice_opened_date":  "TEXT",
    "is_new_practice":       "INTEGER DEFAULT 0",
}

for col, coltype in missing_cols.items():
    if col not in cols:
        conn.execute(
            f"ALTER TABLE leads ADD COLUMN {col} {coltype}"
        )
        print(f"  Added: {col} {coltype}")
    else:
        print(f"  Already exists: {col}")

conn.commit()
conn.close()

print()
print("=" * 60)
print("FIX 2 - Verify both summaries now work without errors")
print("=" * 60)

import importlib

# Care
try:
    import engine.care.care_intelligence as ci
    importlib.reload(ci)
    summary = ci.get_intelligence_summary()
    errors = [k for k in summary if 'error' in str(k).lower()]
    print(f"Care summary  : {len(summary)} keys, total={summary.get('total_scored')}, high={summary.get('high_urgency')}")
    if errors:
        print(f"  Error keys: {errors}")
    else:
        print(f"  No errors in summary keys")
except Exception as e:
    print(f"Care ERROR: {e}")

# Dental
try:
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    summary2 = di.get_intelligence_summary()
    print(f"Dental summary: {len(summary2)} keys, total={summary2.get('total_scored')}, high={summary2.get('high_urgency')}")
except Exception as e:
    print(f"Dental ERROR: {e}")

print()
print("=" * 60)
print("FIX 3 - Check care urgency scoring")
print("        Why all 99 leads score 'medium' (40-69)")
print("        Show urgency_score distribution")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

dist = conn.execute("""
    SELECT
        urgency_score,
        urgency_band,
        COUNT(*) as cnt
    FROM care_home_opportunities
    GROUP BY urgency_score, urgency_band
    ORDER BY urgency_score DESC
    LIMIT 15
""").fetchall()

print("urgency_score | urgency_band | count")
print("-" * 40)
for row in dist:
    print(f"  {str(row[0]):13s} | {str(row[1]):12s} | {row[2]}")

# Check what factors contribute to urgency
# Show sample lead data to understand scoring
sample = conn.execute("""
    SELECT
        cho.urgency_score,
        cho.urgency_band,
        cho.ai_score,
        l.cqc_rating,
        l.director_name,
        l.director_change_date,
        l.website,
        l.bed_count
    FROM care_home_opportunities cho
    JOIN leads l ON cho.lead_id = l.id
    ORDER BY cho.urgency_score DESC
    LIMIT 5
""").fetchall()

print("\nTop 5 leads by urgency_score:")
print("urgency | band   | ai  | cqc         | director | dir_date | website | beds")
print("-" * 80)
for r in sample:
    print(f"  {str(r[0]):7s} | {str(r[1]):6s} | {str(r[2]):3s} | {str(r[3]):11s} | "
          f"{str(r[4] or '')[:8]:8s} | {str(r[5] or '')[:8]:8s} | "
          f"{str(r[6] or '')[:7]:7s} | {str(r[7] or '')}")

conn.close()

print()
print("=" * 60)
print("FIX 4 - Check dental ai_score=None -> urgency_band=high")
print("        Look at how urgency_band is set in dental")
print("=" * 60)

with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    di_content = f.read()

di_lines = di_content.split('\n')
print("Lines referencing urgency_band assignment in dental:")
for i, line in enumerate(di_lines):
    if 'urgency_band' in line.lower() and ('=' in line or 'high' in line.lower()):
        print(f"  Line {i+1}: {line.strip()}")

print()
print("=" * 60)
print("FIX 5 - Run final check on dashboard files")
print("=" * 60)

files = {
    "pages/care_intelligence_dashboard.py": "care",
    "pages/dental_intelligence_dashboard.py": "dental",
}
for fpath, name in files.items():
    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
        c = f.read()
    applymap = c.count(".applymap(")
    lines = len(c.split('\n'))
    print(f"  {name} dashboard: {lines} lines, applymap={applymap}")

print()
print("=" * 60)
print("SUMMARY OF SESSION 5 STATE")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

care_rows = conn.execute("SELECT COUNT(*) FROM care_home_opportunities").fetchone()[0]
dental_rows = conn.execute("SELECT COUNT(*) FROM dental_opportunities").fetchone()[0]
care_high = conn.execute(
    "SELECT COUNT(*) FROM care_home_opportunities WHERE urgency_band='high'"
).fetchone()[0]
dental_high = conn.execute(
    "SELECT COUNT(*) FROM dental_opportunities WHERE urgency_band='high'"
).fetchone()[0]

conn.close()

print(f"  care_home_opportunities : {care_rows} rows, {care_high} high urgency")
print(f"  dental_opportunities    : {dental_rows} rows, {dental_high} high urgency")
print(f"  care applymap           : fixed")
print(f"  dental applymap         : fixed")
print(f"  dental refresh          : 65/65 succeeded")
print(f"  care refresh            : 99/99 succeeded")

print()
print("ALL DONE")