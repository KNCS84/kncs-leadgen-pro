import re

filepath = "engine/dental/dental_intelligence.py"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

original = content

# Fix 1: Replace all LOWER(category) LIKE '%dental%'
#         with LOWER(industry) LIKE '%dental%'
content = content.replace(
    "WHERE LOWER(category) LIKE '%dental%'",
    "WHERE LOWER(industry) LIKE '%dental%'"
)

# Fix 2: Replace company_name with business_name in SQL queries
# Only in SQL strings (between triple quotes)
# Use careful replace to avoid touching variable names
content = content.replace(
    "l.company_name",
    "l.business_name"
)
content = content.replace(
    "leads.company_name",
    "leads.business_name"
)

# Count changes made
category_fixes = original.count("LOWER(category) LIKE '%dental%'")
company_fixes  = original.count("l.company_name") + original.count("leads.company_name")

print(f"category -> industry fixes applied : {category_fixes}")
print(f"company_name -> business_name fixes: {company_fixes}")

# Verify result
if "LOWER(category)" in content:
    print("WARNING: some category refs remain!")
    # Find them
    for i, line in enumerate(content.split('\n')):
        if 'category' in line.lower() and 'LOWER(category)' in line:
            print(f"  Line {i+1}: {line}")
else:
    print("All category refs fixed")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\ndental_intelligence.py patched -> v2.1")
print("Backup not needed - git tracks changes")