"""
run_migration.py
KNCS LeadGen Pro — One-time database migration script
Adds all Phase 1 columns missing from the live database.
Safe to run multiple times — uses try/except per column.
Run once from terminal: python run_migration.py
"""

import sqlite3
import os
import sys

DB_PATH = "data/leads.db"


def run_migration():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Make sure you are running this from the project root.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("=" * 55)
    print("  KNCS LeadGen Pro — Database Migration")
    print("=" * 55)

    # Each migration is (table, column, definition)
    migrations = [
        # ── leads table ────────────────────────────────────────
        ("leads", "urgency_band",    "TEXT    DEFAULT ''"),
        ("leads", "director_name",   "TEXT    DEFAULT ''"),
        ("leads", "bed_count",       "INTEGER DEFAULT 0"),

        # ── care_operator_groups table ─────────────────────────
        ("care_operator_groups", "group_type", "TEXT DEFAULT ''"),

        # ── care_acquisition_signals table ─────────────────────
        ("care_acquisition_signals", "detail", "TEXT DEFAULT ''"),
    ]

    success = 0
    skipped = 0
    errors  = 0

    for table, column, definition in migrations:
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        try:
            cursor.execute(sql)
            conn.commit()
            print(f"  ✓ Added   : {table}.{column}")
            success += 1
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "duplicate column name" in msg or "already exists" in msg:
                print(f"  — Skipped : {table}.{column} (already exists)")
                skipped += 1
            else:
                print(f"  ✗ ERROR   : {table}.{column} — {msg}")
                errors += 1
        except Exception as exc:
            print(f"  ✗ ERROR   : {table}.{column} — {exc}")
            errors += 1

    print("=" * 55)
    print(f"  Added: {success}  |  Skipped: {skipped}  |  Errors: {errors}")
    print("=" * 55)

    # ── Verify the tables exist before migrating ───────────────
    print("\nVerifying tables exist...")
    tables_to_check = [
        "leads",
        "care_operator_groups",
        "care_acquisition_signals",
    ]
    for tbl in tables_to_check:
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name=?",
            (tbl,)
        )
        row = cursor.fetchone()
        if row:
            print(f"  ✓ Table exists : {tbl}")
        else:
            print(f"  ✗ MISSING TABLE: {tbl}")
            print(f"    → Run the app once so init_database() creates it,")
            print(f"      then run this script again.")

    # ── Verify columns were added ──────────────────────────────
    print("\nVerifying columns...")
    checks = [
        ("leads",                    "urgency_band"),
        ("leads",                    "director_name"),
        ("leads",                    "bed_count"),
        ("care_operator_groups",     "group_type"),
        ("care_acquisition_signals", "detail"),
    ]
    all_ok = True
    for tbl, col in checks:
        cursor.execute(f"PRAGMA table_info({tbl})")
        cols = [row[1] for row in cursor.fetchall()]
        if col in cols:
            print(f"  ✓ {tbl}.{col}")
        else:
            print(f"  ✗ MISSING: {tbl}.{col}")
            all_ok = False

    conn.close()

    print("\n" + "=" * 55)
    if errors == 0 and all_ok:
        print("  Migration complete. Restart Streamlit now.")
        print("  Command: streamlit run app.py")
    else:
        print("  Migration had issues. Check output above.")
        print("  If tables are MISSING, run: streamlit run app.py")
        print("  first (to create tables), then re-run this script.")
    print("=" * 55)


if __name__ == "__main__":
    run_migration()