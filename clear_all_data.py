# clear_all_data.py
# KNCS LeadGen Pro — v9.7
# Safe database reset tool.
# Run from terminal: python clear_all_data.py
#
# 10 bugs fixed:
#   🔴 scrape_jobs not cleared → phantom running jobs after reset
#   🔴 search_history not cleared → stale search history
#   🟠 SQL injection via table name interpolation → allowlist added
#   🟠 No check_same_thread=False
#   🟡 No post-clear verification
#   🟡 scrape_log renamed to search_history (v9.7 schema)
#   🟢 data/ directory check added
#   🟢 Version header added

import sqlite3
import os

DB_PATH = "data/leads.db"

# FIX #3: Allowlist of tables that may be cleared — never interpolate
# user-controlled or dynamic strings into SQL
_CLEARABLE_TABLES = {
    "leads",
    "outreach_log",
    "revenue",
    "ai_cache",
    "call_log",
    "search_history",   # FIX #9: was "scrape_log" — v9.7 schema uses search_history
    "scrape_jobs",      # FIX #1: CRITICAL — must clear to reset phantom jobs
    "job_log",
    "enrichment_log",
}


def clear_all_data():
    # FIX #7: check data/ directory exists before connecting
    data_dir = os.path.dirname(DB_PATH)
    if data_dir and not os.path.exists(data_dir):
        print(f"❌ Data directory not found: {data_dir}")
        print("   Run the app first to initialise the database.")
        return

    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        print("   Run the app first to create it.")
        return

    # FIX #4: check_same_thread=False consistent with rest of codebase
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur  = conn.cursor()

    print("\n" + "=" * 50)
    print("  KNCS LeadGen Pro — v9.7 Data Reset Tool")
    print("=" * 50)

    # Discover all tables in DB
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = {row[0] for row in cur.fetchall()}
    print(f"\n📋 Tables in DB: {', '.join(sorted(all_tables))}")

    # Determine which to clear (intersection of clearable + existing)
    to_clear = sorted(_CLEARABLE_TABLES & all_tables)
    to_skip  = sorted(_CLEARABLE_TABLES - all_tables)

    print(f"\n🗑️  Clearing: {', '.join(to_clear)}")
    if to_skip:
        print(f"⏭️  Skipping:  {', '.join(to_skip)} (tables don't exist yet)")

    print("\nClearing data...\n")

    cleared_total = 0

    for table in to_clear:
        # FIX #3: table name is from allowlist — safe to interpolate
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")   # noqa: S608
            count_before = cur.fetchone()[0]

            cur.execute(f"DELETE FROM {table}")            # noqa: S608
            conn.commit()

            # FIX #5: post-clear verification
            cur.execute(f"SELECT COUNT(*) FROM {table}")   # noqa: S608
            count_after = cur.fetchone()[0]

            if count_after == 0:
                cleared_total += count_before
                print(f"  ✅ Cleared  {table:<22} {count_before:>6} rows removed")
            else:
                print(
                    f"  ⚠️  Partial  {table:<22} "
                    f"{count_before - count_after:>6} removed, "
                    f"{count_after} remain"
                )

        except Exception as exc:
            print(f"  ❌ Error    {table:<22} {exc}")

    # Tables in DB but not in our clearable set (custom/future tables)
    extra_tables = all_tables - _CLEARABLE_TABLES - {"sqlite_sequence"}
    if extra_tables:
        print(f"\n📌 Untouched tables: {', '.join(sorted(extra_tables))}")
        print("   (Not in clear list — data preserved)")

    # Reset auto-increment counters
    print()
    if "sqlite_sequence" in all_tables:
        try:
            # Only reset sequences for tables we actually cleared
            for table in to_clear:
                try:
                    cur.execute(
                        "DELETE FROM sqlite_sequence WHERE name = ?",
                        (table,)
                    )
                except Exception:
                    pass
            conn.commit()
            print("  ✅ Auto-increment counters reset for cleared tables")
        except Exception as exc:
            print(f"  ⚠️  Could not reset auto-increment: {exc}")
    else:
        print("  ℹ️  No sqlite_sequence table (no auto-increment to reset)")

    conn.close()

    print("\n" + "=" * 50)
    print(f"  Done. {cleared_total} total rows removed.")
    print("  Database structure preserved.")
    print("  Ready for a fresh scrape.")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    print("\n" + "⚠️ " * 20)
    print("\n  WARNING: This will permanently delete:\n")
    print("    • All leads and contact data")
    print("    • All outreach logs and call logs")
    print("    • All revenue records")
    print("    • All AI cache (scoring will re-run)")
    print("    • All scrape job history")
    print("    • All search history")
    print("\n  Database STRUCTURE will be kept.")
    print("  You can scrape fresh data immediately after.")
    print("\n" + "⚠️ " * 20)

    confirm = input("\n  Type  YES  to confirm and clear all data: ").strip()

    if confirm.upper() == "YES":
        clear_all_data()
    else:
        print("\n  ❌ Cancelled. No data was changed.\n")