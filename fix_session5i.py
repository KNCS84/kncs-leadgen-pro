import sys, os
sys.path.insert(0, os.getcwd())

print("=" * 60)
print("STEP 1 - Read full get_top_opportunities SQL in dental")
print("=" * 60)

with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    di_content = f.read()

di_lines = di_content.split('\n')

# Print lines 624-750 to see full function
print("Lines 624-760:")
for i in range(623, min(760, len(di_lines))):
    print(f"  {i+1}: {di_lines[i]}")

print()
print("=" * 60)
print("STEP 2 - Check what columns leads table actually has")
print("         that dental_intelligence might reference")
print("=" * 60)

import database as db
conn = db.get_connection()
conn.row_factory = None

all_cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

# Columns dental_intelligence might be looking for
check_cols = [
    'name', 'google_rating', 'company_name', 'practice_name',
    'rating', 'business_name', 'google_reviews', 'reviews',
    'review_count', 'website', 'phone', 'email', 'city',
    'country', 'cqc_rating', 'has_website'
]

print("Column mapping (what dental wants -> what leads has):")
for col in check_cols:
    status = "EXISTS" if col in all_cols else "MISSING"
    print(f"  {col:25s} : {status}")

conn.close()

print()
print("=" * 60)
print("STEP 3 - Find ALL column references in dental_intelligence")
print("         that might not exist in leads table")
print("=" * 60)

# Reload content after previous patches
with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    di_content = f.read()

# Find all l.column_name references
import re
l_refs = re.findall(r'\bl\.([a-zA-Z_]+)', di_content)
unique_l_refs = sorted(set(l_refs))
print("All 'l.column' references in dental_intelligence.py:")
for ref in unique_l_refs:
    exists = ref in all_cols if 'all_cols' in dir() else "?"
    print(f"  l.{ref:30s}")

print()
print("=" * 60)
print("STEP 4 - Apply all column name fixes to dental_intelligence")
print("=" * 60)

import database as db
conn = db.get_connection()
conn.row_factory = None
all_cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]
conn.close()

with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    di_content = f.read()

fixes = {
    "l.google_rating":   "l.rating",
    "l.google_reviews":  "l.reviews",
    "l.practice_name":   "l.business_name",
    "l.company_name":    "l.business_name",
    "l.review_count":    "l.reviews",
    "l.name,":           "l.business_name,",
    "l.name ":           "l.business_name ",
    "l.name\n":          "l.business_name\n",
}

applied = []
for wrong, correct in fixes.items():
    if wrong in di_content:
        count = di_content.count(wrong)
        di_content = di_content.replace(wrong, correct)
        applied.append(f"  '{wrong}' -> '{correct}' ({count}x)")

if applied:
    print("Fixes applied:")
    for a in applied:
        print(a)
    with open("engine/dental/dental_intelligence.py", "w",
              encoding="utf-8") as f:
        f.write(di_content)
    print("dental_intelligence.py saved")
else:
    print("No additional fixes needed")

print()
print("=" * 60)
print("STEP 5 - Rerun dental refresh to fix lead_id = NULL")
print("         Then test get_top_opportunities")
print("=" * 60)
try:
    import importlib
    import engine.dental.dental_intelligence as di_module
    importlib.reload(di_module)

    # First clear bad rows with NULL lead_id
    conn = db.get_connection()
    conn.row_factory = None
    deleted = conn.execute(
        "DELETE FROM dental_opportunities WHERE lead_id IS NULL"
    ).rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} rows with NULL lead_id")

    # Rerun refresh
    result = di_module.refresh_all_dental_intelligence(batch_size=100)
    print(f"Dental refresh: {result}")

    # Test get_top_opportunities
    rows = di_module.get_top_opportunities(limit=5)
    print(f"get_top_opportunities returned: {len(rows)} rows")
    if rows:
        print(f"Keys: {list(rows[0].keys())}")
        print(f"First: {dict(rows[0])}")

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print()
print("ALL STEPS COMPLETE")