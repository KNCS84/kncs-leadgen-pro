import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("FINAL STATE CHECK — Session 5")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

print()
print("── DATABASE ──────────────────────────────────────────")
tables_to_check = [
    ("care_home_opportunities",  "urgency_band"),
    ("dental_opportunities",     "urgency_band"),
    ("care_operator_groups",     "lead_id"),
    ("dental_groups",            "lead_id"),
    ("care_acquisition_signals", "detected_at"),
    ("dental_acquisition_signals","detected_at"),
]

for table, key_col in tables_to_check:
    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        cols = [r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()]
        has_col = key_col in cols
        print(f"  {table:35s} rows={count:4d}  {key_col}={'YES' if has_col else 'NO'}")
    except Exception as e:
        print(f"  {table:35s} ERROR: {e}")

conn.close()

print()
print("── CARE INTELLIGENCE ────────────────────────────────")
try:
    import engine.care.care_intelligence as ci
    summary = ci.get_intelligence_summary()
    print(f"  get_intelligence_summary keys : {list(summary.keys())}")
    print(f"  total_scored                  : {summary.get('total_scored', '?')}")
    print(f"  high_urgency                  : {summary.get('high_urgency', '?')}")
    print(f"  avg_opp_score                 : {summary.get('avg_opp_score', '?')}")

    opps = ci.get_top_opportunities(limit=5)
    print(f"  get_top_opportunities         : {len(opps)} rows returned")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("── DENTAL INTELLIGENCE ──────────────────────────────")
try:
    import importlib
    import engine.dental.dental_intelligence as di
    importlib.reload(di)

    summary2 = di.get_intelligence_summary()
    print(f"  get_intelligence_summary keys : {list(summary2.keys())}")
    print(f"  total_scored                  : {summary2.get('total_scored', '?')}")
    print(f"  high_urgency                  : {summary2.get('high_urgency', '?')}")
    print(f"  avg_opp_score                 : {summary2.get('avg_opp_score', '?')}")

    opps2 = di.get_top_opportunities(limit=5)
    print(f"  get_top_opportunities         : {len(opps2)} rows returned")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("── DASHBOARD FILES ──────────────────────────────────")
files = [
    "pages/care_intelligence_dashboard.py",
    "pages/dental_intelligence_dashboard.py",
]
for f in files:
    if os.path.exists(f):
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        applymap_count = content.count(".applymap(")
        lines = len(content.split('\n'))
        print(f"  {f}")
        print(f"    lines={lines}  applymap_remaining={applymap_count}")
    else:
        print(f"  {f} — NOT FOUND")

print()
print("── ISSUES REMAINING ─────────────────────────────────")
issues = []

# Check care urgency distribution
conn = db.get_connection()
conn.row_factory = None
care_dist = conn.execute(
    "SELECT urgency_band, COUNT(*) FROM care_home_opportunities "
    "GROUP BY urgency_band"
).fetchall()
dental_dist = conn.execute(
    "SELECT urgency_band, COUNT(*) FROM dental_opportunities "
    "GROUP BY urgency_band"
).fetchall()
conn.close()

print(f"  care urgency distribution   : {care_dist}")
print(f"  dental urgency distribution : {dental_dist}")

care_high = sum(c for b, c in care_dist if b == 'high')
dental_high = sum(c for b, c in dental_dist if b == 'high')

if care_high == 0:
    issues.append("Care: all leads scored 'medium' — High Opportunity card = 0")
if dental_high > 0:
    print(f"  dental has {dental_high} high urgency leads - good")

# Check dental dashboard exists
if not os.path.exists("pages/dental_intelligence_dashboard.py"):
    issues.append("dental_intelligence_dashboard.py does not exist yet")
else:
    with open("pages/dental_intelligence_dashboard.py", "r",
              encoding="utf-8", errors="replace") as fh:
        dc = fh.read()
    if ".applymap(" in dc:
        issues.append("dental dashboard still has applymap - needs fix")

print()
if issues:
    print("ISSUES TO FIX:")
    for idx, issue in enumerate(issues, 1):
        print(f"  {idx}. {issue}")
else:
    print("No blocking issues found")

print()
print("=" * 60)
print("DONE")
print("=" * 60)