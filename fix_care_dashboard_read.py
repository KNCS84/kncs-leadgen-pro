# Read care_intelligence_dashboard.py and show the
# _load_top_opportunities and _render_top_opportunities functions

filepath = "pages/care_intelligence_dashboard.py"

with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
print()

# Find and print relevant functions
targets = [
    "_load_top_opportunities",
    "_render_top_opportunities",
    "_load_intelligence_summary",
    "_render_summary_cards"
]

for target in targets:
    print("=" * 60)
    print(f"FUNCTION: {target}")
    print("=" * 60)
    in_func = False
    func_indent = 0
    for i, line in enumerate(lines):
        if f"def {target}" in line:
            in_func = True
            func_indent = len(line) - len(line.lstrip())
            print(f"Line {i+1}: {line}", end="")
            continue
        if in_func:
            current_indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            # Stop at next function at same or lower indent
            if stripped.startswith("def ") and current_indent <= func_indent:
                break
            print(f"Line {i+1}: {line}", end="")
    print()