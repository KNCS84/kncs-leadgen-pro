import sys, os
sys.path.insert(0, os.getcwd())

print("=" * 60)
print("STEP 1 - Read dashboard with correct encoding")
print("=" * 60)

filepath = "pages/care_intelligence_dashboard.py"

# Read with utf-8 and replace bad chars
with open(filepath, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

print(f"File read OK, length: {len(content)} chars")

# Find the problematic character area
# Position 16421 mentioned in error
problem_area = content[16350:16500]
print(f"Content around position 16421:")
print(repr(problem_area))

print()
print("=" * 60)
print("STEP 2 - Find _render_summary_cards")
print("=" * 60)

lines = content.split('\n')
print(f"Total lines: {len(lines)}")

# Find _render_summary_cards
for i, line in enumerate(lines):
    if '_render_summary_cards' in line:
        print(f"Line {i+1}: {repr(line)}")

print()
print("=" * 60)
print("STEP 3 - Show lines 420-490 (around summary cards)")
print("=" * 60)
for i in range(419, min(490, len(lines))):
    print(f"Line {i+1}: {lines[i]}")

print()
print("=" * 60)
print("STEP 4 - Check director_changed_date in leads table")
print("=" * 60)
import database as db
conn = db.get_connection()
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]
# Check director-related columns
director_cols = [c for c in cols if 'director' in c.lower()]
print("Director-related columns in leads:", director_cols)
conn.close()

print()
print("=" * 60)
print("STEP 5 - Rerun dental refresh now that category is fixed")
print("=" * 60)
try:
    # Force reload to pick up patched file
    import importlib
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    
    result = di.refresh_all_dental_intelligence(batch_size=100)
    print("Dental result:", result)
except AttributeError:
    # Function might be named differently
    try:
        from engine.dental.dental_intelligence import refresh_all_dental_intelligence
        result = refresh_all_dental_intelligence(batch_size=100)
        print("Dental result:", result)
    except Exception as e2:
        print("ERROR:", type(e2).__name__, str(e2))
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))

print()
print("=" * 60)
print("STEP 6 - Check what refresh function is available in dental")
print("=" * 60)
try:
    import engine.dental.dental_intelligence as di2
    funcs = [f for f in dir(di2) if not f.startswith('_')]
    print("Public functions in dental_intelligence:", funcs)
except Exception as e:
    print("ERROR:", e)

print()
print("ALL STEPS COMPLETE")