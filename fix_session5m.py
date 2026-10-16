import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("FIX 1 - Add date_established and any other missing columns")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(leads)"
).fetchall()]

# All columns that any intelligence module might query
# that don't already exist
needed = {
    "date_established":      "TEXT",
    "established_date":      "TEXT",
    "year_established":      "INTEGER",
    "cqc_last_inspection":   "TEXT",
    "cqc_next_inspection":   "TEXT",
    "inspection_overdue":    "INTEGER DEFAULT 0",
    "supplier_signals":      "TEXT",
    "has_supplier_signals":  "INTEGER DEFAULT 0",
}

added = []
for col, coltype in needed.items():
    if col not in cols:
        conn.execute(
            f"ALTER TABLE leads ADD COLUMN {col} {coltype}"
        )
        added.append(col)
    
if added:
    conn.commit()
    print(f"Added: {added}")
else:
    print("All columns already exist")

conn.close()

print()
print("=" * 60)
print("FIX 2 - Verify dental summary now fully clean")
print("=" * 60)

import importlib
import engine.dental.dental_intelligence as di
importlib.reload(di)

import logging
# Suppress log output temporarily to see only errors
errors_found = []

try:
    summary = di.get_intelligence_summary()
    print(f"total_scored     : {summary.get('total_scored')}")
    print(f"high_urgency     : {summary.get('high_urgency')}")
    print(f"no_website_count : {summary.get('no_website_count')}")
    print(f"low_rating_count : {summary.get('low_rating_count')}")
    print(f"no_booking_count : {summary.get('no_booking_count')}")
    print(f"avg_opp_score    : {summary.get('avg_opp_score')}")
    print("Dental summary: OK")
except Exception as e:
    print(f"ERROR: {e}")

print()
print("=" * 60)
print("FIX 3 - Verify care summary fully clean")
print("=" * 60)

import engine.care.care_intelligence as ci
importlib.reload(ci)

try:
    summary2 = ci.get_intelligence_summary()
    print(f"total_scored         : {summary2.get('total_scored')}")
    print(f"high_urgency         : {summary2.get('high_urgency')}")
    print(f"medium_urgency       : {summary2.get('medium_urgency')}")
    print(f"avg_opp_score        : {summary2.get('avg_opp_score')}")
    print(f"director_changes_90d : {summary2.get('director_changes_90d')}")
    print(f"inadequate_count     : {summary2.get('inadequate_count')}")
    print("Care summary: OK")
except Exception as e:
    print(f"ERROR: {e}")

print()
print("=" * 60)
print("FIX 4 - Full final state report")
print("=" * 60)

conn = db.get_connection()
conn.row_factory = None

checks = [
    ("care_home_opportunities",   "SELECT COUNT(*) FROM care_home_opportunities"),
    ("dental_opportunities",      "SELECT COUNT(*) FROM dental_opportunities"),
    ("care high urgency",         "SELECT COUNT(*) FROM care_home_opportunities WHERE urgency_band='high'"),
    ("dental high urgency",       "SELECT COUNT(*) FROM dental_opportunities WHERE urgency_band='high'"),
    ("care operator groups",      "SELECT COUNT(*) FROM care_operator_groups"),
    ("dental groups",             "SELECT COUNT(*) FROM dental_groups"),
    ("care acq signals",          "SELECT COUNT(*) FROM care_acquisition_signals"),
    ("dental acq signals",        "SELECT COUNT(*) FROM dental_acquisition_signals"),
]

print(f"{'Check':<30} {'Value':>8}")
print("-" * 40)
for label, sql in checks:
    try:
        val = conn.execute(sql).fetchone()[0]
        print(f"  {label:<28} {val:>8}")
    except Exception as e:
        print(f"  {label:<28} ERROR: {e}")

conn.close()

print()
print("=" * 60)
print("FIX 5 - Test both get_top_opportunities")
print("=" * 60)

try:
    importlib.reload(ci)
    care_opps = ci.get_top_opportunities(limit=3)
    print(f"Care top opportunities  : {len(care_opps)} rows")
    if care_opps:
        print(f"  Keys: {list(care_opps[0].keys())}")
except Exception as e:
    print(f"Care ERROR: {e}")

try:
    importlib.reload(di)
    dental_opps = di.get_top_opportunities(limit=3)
    print(f"Dental top opportunities: {len(dental_opps)} rows")
    if dental_opps:
        print(f"  Keys: {list(dental_opps[0].keys())}")
except Exception as e:
    print(f"Dental ERROR: {e}")

print()
print("=" * 60)
print("SESSION 5 COMPLETE — READY FOR HANDOVER")
print("=" * 60)
print()
print("REMAINING ISSUES FOR SESSION 6:")
print("  1. Care High Opportunity = 0")
print("     Cause: all leads have empty CQC rating in DB")
print("     Fix needed: re-scrape or manually set cqc_rating")
print("     for sample leads to test scoring")
print()
print("  2. Nigerian dental leads getting UK CQC AI prompts")
print("     Cause: dental_intelligence AI prompt doesn't")
print("     check country before calling CQC scorer")
print("     Fix: patch _get_ai_score to skip non-UK leads")
print("     File: dental_intelligence.py (careful - v2.0)")
print()
print("  3. Operator groups = 0, Acquisition signals = 0")
print("     Cause: no data has been added yet - need to run")
print("     the operator group detection and signal detection")
print()
print("  4. Dental dashboard key translation layer")
print("     Check if dental dashboard reads same keys as")
print("     dental_intelligence returns")
print()
print("ALL DONE")