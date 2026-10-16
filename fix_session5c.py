import sys, os
sys.path.insert(0, os.getcwd())

print("=" * 60)
print("STEP 1 - Verify dental_intelligence.py actual content")
print("=" * 60)

filepath = "engine/dental/dental_intelligence.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Check for category references
category_count = content.count("category")
industry_count = content.count("industry")
print(f"'category' occurrences in file : {category_count}")
print(f"'industry' occurrences in file : {industry_count}")

# Show lines with category
lines = content.split('\n')
print("\nLines containing 'category':")
for i, line in enumerate(lines):
    if 'category' in line:
        print(f"  Line {i+1}: {repr(line)}")

print("\nLines containing 'company_name':")
for i, line in enumerate(lines):
    if 'company_name' in line:
        print(f"  Line {i+1}: {repr(line)}")

print()
print("=" * 60)
print("STEP 2 - Apply dental fix directly with exact strings")
print("=" * 60)

fixes_applied = 0

# Show exactly what is on lines 101, 784, 795, 807, 829
target_lines = [101, 784, 795, 807, 829]
print("Content at previously identified lines:")
for ln in target_lines:
    if ln <= len(lines):
        print(f"  Line {ln}: {repr(lines[ln-1])}")

print()
print("=" * 60)
print("STEP 3 - Check why care dashboard shows 'failed to load'")
print("         by reading the Streamlit error more carefully")
print("=" * 60)

import database as db
import engine.care.care_intelligence as ci

# Replicate exactly what the dashboard does
print("Testing _load_intelligence_summary equivalent:")
try:
    raw = ci.get_intelligence_summary()
    print("raw keys:", list(raw.keys()) if raw else "EMPTY")
    print("raw values:", raw)
except Exception as e:
    print("get_intelligence_summary ERROR:", type(e).__name__, str(e))

print()
print("Testing get_top_opportunities:")
try:
    rows = ci.get_top_opportunities(limit=10)
    print(f"Returned {len(rows)} rows")
    if rows:
        print("Keys:", list(rows[0].keys()))
        # Check if urgency_band filter works
        high = [r for r in rows if str(r.get('urgency_band','')).lower() == 'high']
        medium = [r for r in rows if str(r.get('urgency_band','')).lower() == 'medium']
        low = [r for r in rows if str(r.get('urgency_band','')).lower() == 'low']
        print(f"  high: {len(high)}, medium: {len(medium)}, low: {len(low)}")
except Exception as e:
    print("get_top_opportunities ERROR:", type(e).__name__, str(e))

print()
print("=" * 60)
print("STEP 4 - Read _render_summary_cards from dashboard")
print("=" * 60)

dash_path = "pages/care_intelligence_dashboard.py"
with open(dash_path, "r", encoding="utf-8") as f:
    dash_lines = f.readlines()

# Find _render_summary_cards
in_func = False
func_indent = 0
print("_render_summary_cards function:")
for i, line in enumerate(dash_lines):
    if "def _render_summary_cards" in line:
        in_func = True
        func_indent = len(line) - len(line.lstrip())
    if in_func:
        stripped = line.strip()
        if stripped.startswith("def ") and "_render_summary_cards" not in line:
            break
        print(f"Line {i+1}: {line}", end="")

print()
print("=" * 60)
print("STEP 5 - Check Streamlit app error log")
print("         Look for the ACTUAL exception in care dashboard")
print("=" * 60)

# Try to import and run the dashboard function that fails
# by simulating what streamlit does
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "care_intelligence_dashboard",
        "pages/care_intelligence_dashboard.py"
    )
    # Don't actually run it - just check for syntax errors
    with open("pages/care_intelligence_dashboard.py", "r") as f:
        source = f.read()
    compile(source, "care_intelligence_dashboard.py", "exec")
    print("Dashboard file: no syntax errors")
except SyntaxError as e:
    print(f"SYNTAX ERROR at line {e.lineno}: {e.msg}")
    print(f"  Text: {e.text}")
except Exception as e:
    print(f"Import check error: {type(e).__name__}: {e}")

print()
print("ALL STEPS COMPLETE")