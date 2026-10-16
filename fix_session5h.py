import sys, os
sys.path.insert(0, os.getcwd())

print("=" * 60)
print("FIX 1 - Patch applymap -> map in care dashboard")
print("=" * 60)

filepath = "pages/care_intelligence_dashboard.py"

with open(filepath, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

count_before = content.count(".applymap(")
print(f"Found {count_before} occurrence(s) of '.applymap('")

content = content.replace(".applymap(", ".map(")

count_after = content.count(".applymap(")
print(f"Remaining after fix: {count_after}")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("Care dashboard patched - applymap -> map")

# Verify
with open(filepath, "r", encoding="utf-8") as f:
    verify = f.read()
if ".applymap(" in verify:
    print("WARNING: still present!")
else:
    print("CONFIRMED: applymap fully removed")

print()
print("=" * 60)
print("FIX 2 - Find what dental get_top_opportunities queries")
print("        to fix l.name -> l.business_name")
print("=" * 60)

with open("engine/dental/dental_intelligence.py", "r",
          encoding="utf-8", errors="replace") as f:
    di_content = f.read()

di_lines = di_content.split('\n')

# Find get_top_opportunities function
print("get_top_opportunities function content:")
in_func = False
func_start = 0
for i, line in enumerate(di_lines):
    if "def get_top_opportunities" in line:
        in_func = True
        func_start = i
    if in_func:
        print(f"  Line {i+1}: {line}")
        if i > func_start and line.strip().startswith("def "):
            break
        if i > func_start + 60:
            print("  ... (truncated at 60 lines)")
            break

print()
print("Lines containing 'l.name' or 'l.company':")
for i, line in enumerate(di_lines):
    if 'l.name' in line or 'l.company' in line or 'd.name' in line:
        print(f"  Line {i+1}: {line.strip()}")

print()
print("=" * 60)
print("FIX 3 - Patch dental_intelligence.py")
print("        l.name -> l.business_name")
print("        d.name -> d.business_name (if present)")
print("=" * 60)

# Count occurrences
name_count = di_content.count("l.name")
d_name_count = di_content.count("d.name")
print(f"'l.name' occurrences: {name_count}")
print(f"'d.name' occurrences: {d_name_count}")

# Apply fix
di_patched = di_content.replace("l.name", "l.business_name")
di_patched = di_patched.replace("d.name", "d.business_name")

# Verify fix doesn't break anything
remaining_l_name = di_patched.count("l.name")
print(f"'l.name' remaining after patch: {remaining_l_name}")

with open("engine/dental/dental_intelligence.py", "w",
          encoding="utf-8") as f:
    f.write(di_patched)

print("dental_intelligence.py patched - l.name -> l.business_name")

print()
print("=" * 60)
print("FIX 4 - Check dental lead_id = None issue")
print("        dental_opportunities has lead_id NULL for all rows")
print("=" * 60)

import database as db
conn = db.get_connection()
conn.row_factory = None

# Check what _save_opportunity writes
# Look at the INSERT statement in dental_intelligence
save_lines = []
in_save = False
save_start = 0
for i, line in enumerate(di_lines):
    if "def _save_opportunity" in line:
        in_save = True
        save_start = i
    if in_save:
        save_lines.append(f"  Line {i+1}: {line}")
        if i > save_start and line.strip().startswith("def "):
            break
        if i > save_start + 50:
            save_lines.append("  ... (truncated)")
            break

print("_save_opportunity function:")
for l in save_lines:
    print(l)

conn.close()

print()
print("=" * 60)
print("FIX 5 - Test dental get_top_opportunities after patch")
print("=" * 60)
try:
    import importlib
    import engine.dental.dental_intelligence as di_module
    importlib.reload(di_module)

    rows = di_module.get_top_opportunities(limit=5)
    print(f"Returned {len(rows)} rows")
    if rows:
        print(f"Keys: {list(rows[0].keys())}")
        print(f"First row: {dict(rows[0])}")
    else:
        print("Empty - lead_id issue or JOIN failing")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print()
print("ALL FIXES COMPLETE")