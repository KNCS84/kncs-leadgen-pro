# check_files.py
# Run this: python check_files.py

import os

print("=" * 60)
print("KNCS File Checker")
print("=" * 60)

files_to_check = [
    "pages/dashboard.py",
    "pages/nigeria_dashboard.py",
    "pages/leads.py",
    "pages/care_home_dashboard.py",
    ".streamlit/config.toml",
]

for filepath in files_to_check:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print(f"\n{'='*40}")
        print(f"FILE: {filepath}")
        print(f"Total lines: {len(lines)}")
        print(f"\n--- FIRST 5 LINES ---")
        for i, line in enumerate(lines[:5], 1):
            print(f"  {i}: {line.rstrip()}")
        print(f"\n--- LAST 10 LINES ---")
        for i, line in enumerate(
            lines[-10:], max(1, len(lines)-9)
        ):
            print(f"  {i}: {line.rstrip()}")
    else:
        print(f"\n[MISSING] {filepath}")

print("\n" + "="*60)
print("DONE")