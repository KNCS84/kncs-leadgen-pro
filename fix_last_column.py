import sys, os
sys.path.insert(0, os.getcwd())
import database as db

conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

final_missing = {
    "incorporated_date":   "TEXT",
    "companies_house_id":  "TEXT",
    "registered_address":  "TEXT",
}

added = []
for col, coltype in final_missing.items():
    if col not in cols:
        conn.execute(
            f"ALTER TABLE leads ADD COLUMN {col} {coltype}"
        )
        added.append(col)

if added:
    conn.commit()
    print(f"Added: {added}")
else:
    print("Nothing to add")

# Final verification - run both summaries clean
import importlib
import engine.care.care_intelligence as ci
import engine.dental.dental_intelligence as di
importlib.reload(ci)
importlib.reload(di)

print()
print("Care summary (should show no column errors above this):")
s1 = ci.get_intelligence_summary()
print(f"  total={s1.get('total_scored')} high={s1.get('high_urgency')} avg={s1.get('avg_opp_score')}")

print()
print("Dental summary (should show no column errors above this):")
s2 = di.get_intelligence_summary()
print(f"  total={s2.get('total_scored')} high={s2.get('high_urgency')} avg={s2.get('avg_opp_score')}")

conn.close()
print()
print("DONE - ready for handover note")