import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("STEP 1 - List ALL tables in database")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()]
print("All tables:")
for t in tables:
    print(f"  {t}")
conn.close()

print()
print("=" * 60)
print("STEP 2 - Check dental_operator_groups in dental_intelligence")
print("         What does it expect?")
print("=" * 60)
with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    content = f.read()

lines = content.split('\n')
print("Lines referencing 'dental_operator_groups':")
for i, line in enumerate(lines):
    if 'dental_operator_groups' in line or 'operator_group' in line.lower():
        print(f"  Line {i+1}: {line.strip()}")

print()
print("Lines referencing '_is_in_dental_group':")
in_func = False
for i, line in enumerate(lines):
    if '_is_in_dental_group' in line and 'def ' in line:
        in_func = True
    if in_func:
        print(f"  Line {i+1}: {line}")
        if i > 0 and line.strip().startswith('def ') and '_is_in_dental_group' not in line:
            break
        if in_func and (i - [j for j,l in enumerate(lines) if '_is_in_dental_group' in l and 'def ' in l][0]) > 25:
            print("  ... (truncated)")
            break

print()
print("=" * 60)
print("STEP 3 - Create dental_operator_groups if missing")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

tables2 = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]

if 'dental_operator_groups' not in tables2:
    print("Creating dental_operator_groups table...")
    conn.execute("""
        CREATE TABLE dental_operator_groups (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name          TEXT,
            site_count          INTEGER DEFAULT 0,
            director_names      TEXT,
            company_numbers     TEXT,
            enterprise_score    INTEGER DEFAULT 0,
            contact_lead_id     INTEGER,
            lead_id             INTEGER,
            group_type          TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    print("  dental_operator_groups created")
else:
    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(dental_operator_groups)"
    ).fetchall()]
    print("  Already exists, columns:", cols)
    if 'lead_id' not in cols:
        conn.execute(
            "ALTER TABLE dental_operator_groups ADD COLUMN lead_id INTEGER"
        )
        conn.commit()
        print("  Added lead_id column")

conn.close()

print()
print("=" * 60)
print("STEP 4 - Create dental_acquisition_signals if missing")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

tables3 = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]

if 'dental_acquisition_signals' not in tables3:
    print("Creating dental_acquisition_signals table...")
    conn.execute("""
        CREATE TABLE dental_acquisition_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id         INTEGER,
            signal_type     TEXT,
            signal_detail   TEXT,
            signal_date     TEXT,
            detected_at     TEXT DEFAULT (datetime('now')),
            is_actioned     INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            detail          TEXT
        )
    """)
    conn.commit()
    print("  dental_acquisition_signals created")
else:
    cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(dental_acquisition_signals)"
    ).fetchall()]
    print("  Already exists, columns:", cols)
    if 'detected_at' not in cols:
        conn.execute(
            "ALTER TABLE dental_acquisition_signals "
            "ADD COLUMN detected_at TEXT"
        )
        conn.commit()
        print("  Added detected_at column")
conn.close()

print()
print("=" * 60)
print("STEP 5 - Rerun dental refresh")
print("=" * 60)
try:
    import importlib
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    result = di.refresh_all_dental_intelligence(batch_size=100)
    print("Dental refresh result:", result)
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
print("STEP 6 - Check pandas version for applymap fix")
print("=" * 60)
import pandas as pd
ver = pd.__version__
print("Pandas version:", ver)
major, minor = int(ver.split('.')[0]), int(ver.split('.')[1])
if major > 2 or (major == 2 and minor >= 1):
    print("STATUS: applymap DEPRECATED — dashboard will crash on styling")
    print("ACTION: need to patch care_intelligence_dashboard.py line 588")
else:
    print("STATUS: applymap OK — not the crash cause")

print()
print("=" * 60)
print("STEP 7 - Find actual crash in care dashboard")
print("         Check what error Streamlit shows")
print("=" * 60)
# Read the dashboard and look for the try/except around
# _render_top_opportunities to understand error handling
with open("pages/care_intelligence_dashboard.py", "r",
          encoding="utf-8", errors="replace") as f:
    dash_lines = f.readlines()

# Find main() function and section rendering
print("Lines calling _render_top_opportunities:")
for i, line in enumerate(dash_lines):
    if '_render_top_opportunities' in line or 'failed to load' in line.lower():
        start = max(0, i-3)
        end = min(len(dash_lines), i+5)
        for j in range(start, end):
            print(f"  Line {j+1}: {dash_lines[j]}", end="")
        print()

print()
print("ALL STEPS COMPLETE")