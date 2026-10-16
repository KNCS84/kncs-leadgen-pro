import sys, os
sys.path.insert(0, os.getcwd())

print("=" * 60)
print("FIX 1 - Patch dental dashboard applymap -> map")
print("=" * 60)

filepath = "pages/dental_intelligence_dashboard.py"

with open(filepath, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

count_before = content.count(".applymap(")
print(f"Found {count_before} occurrence(s)")

content = content.replace(".applymap(", ".map(")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

with open(filepath, "r", encoding="utf-8") as f:
    verify = f.read()

remaining = verify.count(".applymap(")
print(f"Remaining after fix: {remaining}")
print("Dental dashboard applymap fixed" if remaining == 0 else "WARNING: still present")

print()
print("=" * 60)
print("FIX 2 - Add director_changed_date to leads table")
print("        (care_intelligence queries this column)")
print("=" * 60)

import database as db
conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

if 'director_changed_date' not in cols:
    conn.execute(
        "ALTER TABLE leads ADD COLUMN director_changed_date TEXT"
    )
    conn.commit()
    print("Added director_changed_date to leads table")
else:
    print("director_changed_date already exists")

print()
print("=" * 60)
print("FIX 3 - Add google_rating to leads table")
print("        (dental_intelligence summary queries this)")
print("=" * 60)

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

if 'google_rating' not in cols:
    conn.execute(
        "ALTER TABLE leads ADD COLUMN google_rating REAL"
    )
    # Map from rating column
    conn.execute(
        "UPDATE leads SET google_rating = rating WHERE rating IS NOT NULL"
    )
    conn.commit()
    print("Added google_rating, mapped from rating column")
else:
    print("google_rating already exists")

conn.close()

print()
print("=" * 60)
print("FIX 4 - Clean duplicate dental_opportunities rows")
print("        Table has 130 rows but only 65 dental leads")
print("        Keep only the most recent scored_at per lead_id")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

count_before = conn.execute(
    "SELECT COUNT(*) FROM dental_opportunities"
).fetchone()[0]
print(f"Rows before cleanup: {count_before}")

conn.executescript("""
    BEGIN;

    -- Keep only the row with highest id (most recent) per lead_id
    DELETE FROM dental_opportunities
    WHERE id NOT IN (
        SELECT MAX(id)
        FROM dental_opportunities
        WHERE lead_id IS NOT NULL
        GROUP BY lead_id
    )
    AND lead_id IS NOT NULL;

    -- Delete any remaining NULL lead_id rows
    DELETE FROM dental_opportunities
    WHERE lead_id IS NULL;

    COMMIT;
""")

count_after = conn.execute(
    "SELECT COUNT(*) FROM dental_opportunities"
).fetchone()[0]
print(f"Rows after cleanup : {count_after}")
conn.close()

print()
print("=" * 60)
print("FIX 5 - Verify care_intelligence summary now works")
print("        without director_changed_date error")
print("=" * 60)

try:
    import importlib
    import engine.care.care_intelligence as ci
    importlib.reload(ci)
    summary = ci.get_intelligence_summary()
    print(f"Care summary keys   : {list(summary.keys())}")
    print(f"total_scored        : {summary.get('total_scored')}")
    print(f"high_urgency        : {summary.get('high_urgency')}")
    print(f"director_changes_90d: {summary.get('director_changes_90d')}")
    print(f"avg_opp_score       : {summary.get('avg_opp_score')}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

print()
print("=" * 60)
print("FIX 6 - Verify dental_intelligence summary now works")
print("        without google_rating error")
print("=" * 60)

try:
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    summary2 = di.get_intelligence_summary()
    print(f"Dental summary keys : {list(summary2.keys())}")
    print(f"total_scored        : {summary2.get('total_scored')}")
    print(f"high_urgency        : {summary2.get('high_urgency')}")
    print(f"low_rating_count    : {summary2.get('low_rating_count')}")
    print(f"avg_opp_score       : {summary2.get('avg_opp_score')}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

print()
print("=" * 60)
print("FIX 7 - Care High Opportunity = 0 diagnosis")
print("        All 99 leads are medium — check scoring thresholds")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

# Check ai_score distribution to understand why all are medium
scores = conn.execute("""
    SELECT
        MIN(ai_score)          as min_ai,
        MAX(ai_score)          as max_ai,
        AVG(ai_score)          as avg_ai,
        MIN(opportunity_score) as min_opp,
        MAX(opportunity_score) as max_opp,
        AVG(opportunity_score) as avg_opp
    FROM care_home_opportunities
""").fetchone()
print(f"ai_score    min/max/avg : {scores[0]}/{scores[1]}/{scores[2]:.1f}")
print(f"opp_score   min/max/avg : {scores[3]}/{scores[4]}/{scores[5]:.1f}")

# Check what threshold care_intelligence uses for 'high'
with open("engine/care/care_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    ci_content = f.read()

ci_lines = ci_content.split('\n')
print("\nLines referencing urgency thresholds in care_intelligence.py:")
for i, line in enumerate(ci_lines):
    if any(kw in line.lower() for kw in
           ['high', 'urgency_band', 'threshold', '>= 70', '>= 80',
            '>= 60', 'band', 'urgency']):
        if 'urgency' in line.lower() or 'band' in line.lower() or '>=' in line:
            print(f"  Line {i+1}: {line.strip()}")

conn.close()

print()
print("ALL FIXES COMPLETE")