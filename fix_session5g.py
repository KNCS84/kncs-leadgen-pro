import sys, os
sys.path.insert(0, os.getcwd())
import database as db

print("=" * 60)
print("STEP 1 - Rebuild dental_opportunities without NOT NULL")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

# Check current schema
cols = [r[1] for r in conn.execute(
    "PRAGMA table_info(dental_opportunities)"
).fetchall()]
print("Current columns:", cols)

conn.executescript("""
    BEGIN;

    ALTER TABLE dental_opportunities
    RENAME TO dental_opportunities_old;

    CREATE TABLE dental_opportunities (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id               INTEGER,
        opportunity_score     INTEGER DEFAULT 0,
        growth_score          INTEGER DEFAULT 0,
        review_health_score   INTEGER DEFAULT 0,
        buyer_match_json      TEXT,
        signals_detected      TEXT,
        group_id              INTEGER,
        last_refreshed        TEXT,
        created_at            TEXT,
        ai_score              INTEGER,
        ai_reason             TEXT,
        urgency_band          TEXT,
        revenue_estimate_min  INTEGER,
        revenue_estimate_max  INTEGER,
        scored_at             TEXT
    );

    INSERT INTO dental_opportunities (
        id, lead_id, opportunity_score, growth_score,
        review_health_score, buyer_match_json, signals_detected,
        group_id, last_refreshed, created_at,
        ai_score, ai_reason, urgency_band,
        revenue_estimate_min, revenue_estimate_max, scored_at
    )
    SELECT
        id, lead_id, opportunity_score, growth_score,
        review_health_score, buyer_match_json, signals_detected,
        group_id, last_refreshed, created_at,
        ai_score, ai_reason, urgency_band,
        revenue_estimate_min, revenue_estimate_max, scored_at
    FROM dental_opportunities_old;

    DROP TABLE dental_opportunities_old;

    COMMIT;
""")
print("dental_opportunities rebuilt with DEFAULT created_at")

# Verify
cols2 = [r[1] for r in conn.execute(
    "PRAGMA table_info(dental_opportunities)"
).fetchall()]
print("New columns:", cols2)
conn.close()

print()
print("=" * 60)
print("STEP 2 - Fix dental_groups lead_id column")
print("=" * 60)
conn = db.get_connection()
conn.row_factory = None

cols3 = [r[1] for r in conn.execute(
    "PRAGMA table_info(dental_groups)"
).fetchall()]
print("dental_groups columns:", cols3)

if 'lead_id' not in cols3:
    conn.execute(
        "ALTER TABLE dental_groups ADD COLUMN lead_id INTEGER"
    )
    # Map from whichever column has the lead reference
    if 'contact_lead_id' in cols3:
        conn.execute(
            "UPDATE dental_groups SET lead_id = contact_lead_id"
        )
        print("  Mapped from contact_lead_id")
    elif 'primary_lead_id' in cols3:
        conn.execute(
            "UPDATE dental_groups SET lead_id = primary_lead_id"
        )
        print("  Mapped from primary_lead_id")
    else:
        print("  No source column — lead_id added as NULL")
        print("  All cols:", cols3)
    conn.commit()
    print("  dental_groups lead_id fixed")
else:
    print("  lead_id already exists")
conn.close()

print()
print("=" * 60)
print("STEP 3 - Rerun dental refresh")
print("=" * 60)
try:
    import importlib
    import engine.dental.dental_intelligence as di
    importlib.reload(di)
    result = di.refresh_all_dental_intelligence(batch_size=100)
    print("Dental refresh result:", result)
    if result.get('succeeded', 0) > 0:
        print("SUCCESS - dental refresh working")
    else:
        print("Still failing - need to check _save_opportunity in dental_intelligence.py")
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
    import traceback
    traceback.print_exc()

print()
print("ALL STEPS COMPLETE")