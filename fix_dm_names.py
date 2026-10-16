# fix_dm_names.py
# Clears remaining junk decision maker names not caught by
# _is_junk_dm_name() because they are fewer than 4 words
# but are clearly not real person names.
# Run with: python fix_dm_names.py

import sys
sys.path.insert(0, '.')
from database import get_connection

# Each entry: (lead_id, junk_dm_value)
# Only clears if the DB still has exactly this value
JUNK_DMS = [
    (135,  "Carly Dempsey Operations"),
    (98,   "Welcome to"),
    (59,   "Parents and Students Logi"),
    (18,   "Us News Calendar Tuition"),
    (15,   "DaaZ"),
    (14,   "us Get"),
    (70,   "Zeeno Dentals"),
    (135,  "Carly Dempsey Operations Manager"),
]

conn = get_connection()
cur  = conn.cursor()
total = 0

for lead_id, junk_value in JUNK_DMS:
    cur.execute(
        "UPDATE leads SET decision_maker_name = '', dm_source = '' "
        "WHERE id = ? AND decision_maker_name = ?",
        (lead_id, junk_value)
    )
    if cur.rowcount > 0:
        print(f"  Cleared ID {lead_id}: {junk_value}")
        total += cur.rowcount

conn.commit()
conn.close()
print(f"\nDone. Total cleared: {total}")