import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("STEP 1 - Fix dental_opportunities missing columns")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(dental_opportunities)"
).fetchall()]
print("Current dental_opportunities columns:", cols)

needed = {
    "ai_score":            "INTEGER",
    "ai_reason":           "TEXT",
    "urgency_band":        "TEXT",
    "revenue_estimate_min":"INTEGER",
    "revenue_estimate_max":"INTEGER",
    "scored_at":           "TEXT",
    "created_at":          "TEXT",
}

for col, coltype in needed.items():
    if col not in cols:
        conn.execute(
            f"ALTER TABLE dental_opportunities ADD COLUMN {col} {coltype}"
        )
        print(f"  Added column: {col} {coltype}")
    else:
        print(f"  Already exists: {col}")

conn.commit()
print("dental_opportunities schema fixed")
conn.close()

print()
print("=" * 60)
print("STEP 2 - Fix dental_operator_groups lead_id column")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

cols2 = [r[1] for r in conn.execute(
    "PRAGMA table_info(dental_operator_groups)"
).fetchall()]
print("Current dental_operator_groups columns:", cols2)

if 'lead_id' not in cols2:
    conn.execute(
        "ALTER TABLE dental_operator_groups ADD COLUMN lead_id INTEGER"
    )
    # Map from whichever column holds the lead reference
    if 'contact_lead_id' in cols2:
        conn.execute(
            "UPDATE dental_operator_groups SET lead_id = contact_lead_id"
        )
        print("  Mapped lead_id from contact_lead_id")
    elif 'primary_lead_id' in cols2:
        conn.execute(
            "UPDATE dental_operator_groups SET lead_id = primary_lead_id"
        )
        print("  Mapped lead_id from primary_lead_id")
    else:
        print("  WARNING: no source column found to map lead_id")
        print("  Available columns:", cols2)
    conn.commit()
    print("  dental_operator_groups lead_id fixed")
else:
    print("  lead_id already exists")
conn.close()

print()
print("=" * 60)
print("STEP 3 - Check dental_acquisition_signals schema")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

# Check if table exists
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]
print("All tables:", tables)

if 'dental_acquisition_signals' in tables:
    cols3 = [r[1] for r in conn.execute(
        "PRAGMA table_info(dental_acquisition_signals)"
    ).fetchall()]
    print("dental_acquisition_signals columns:", cols3)
    
    if 'detected_at' not in cols3:
        conn.execute(
            "ALTER TABLE dental_acquisition_signals ADD COLUMN detected_at TEXT"
        )
        if 'created_at' in cols3:
            conn.execute(
                "UPDATE dental_acquisition_signals SET detected_at = created_at"
            )
        conn.commit()
        print("  detected_at added")
    else:
        print("  detected_at already exists")
else:
    print("  dental_acquisition_signals table does not exist - will be created on refresh")
conn.close()

print()
print("=" * 60)
print("STEP 4 - Rerun dental refresh after schema fixes")
print("=" * 60)
try:
    import importlib
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    result = di.refresh_all_dental_intelligence(batch_size=100)
    print("Dental refresh result:", result)
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))

print()
print("=" * 60)
print("STEP 5 - Fix pandas applymap deprecation in care dashboard")
print("         (applymap renamed to map in pandas 2.1+)")
print("=" * 60)

import pandas as pd
print("Pandas version:", pd.__version__)

# Check if applymap exists or if we need map
major = int(pd.__version__.split('.')[0])
minor = int(pd.__version__.split('.')[1])
if major > 2 or (major == 2 and minor >= 1):
    print("Pandas >= 2.1 detected — applymap is DEPRECATED, must use .map()")
    print("This is causing the Streamlit crash in _render_top_opportunities")
    needs_applymap_fix = True
else:
    print("Pandas < 2.1 — applymap is fine")
    needs_applymap_fix = False

print("needs_applymap_fix:", needs_applymap_fix)

print()
print("=" * 60)
print("STEP 6 - Check care_intelligence get_intelligence_summary")
print("         director_changed_date column fix")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None
director_cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]
director_related = [c for c in director_cols if 'director' in c.lower()]
print("Director columns in leads:", director_related)

# Check what care_intelligence.py queries
with open("engine/care/care_intelligence.py", "r", encoding="utf-8", errors="replace") as f:
    ci_content = f.read()

ci_lines = ci_content.split('\n')
print("\nLines in care_intelligence.py referencing 'director':")
for i, line in enumerate(ci_lines):
    if 'director' in line.lower():
        print(f"  Line {i+1}: {line.strip()}")
conn.close()

print()
print("ALL STEPS COMPLETE")