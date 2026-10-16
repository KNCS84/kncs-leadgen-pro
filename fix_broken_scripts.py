"""
fix_broken_scripts.py — patches the quoting bug in validate_leads.py and daily_report.py
Run once: python fix_broken_scripts.py
"""

import os

FIXED = {}

FIXED["validate_leads.py"] = '''import sqlite3, re

DB_PATH = "data/leads.db"

JUNK_PATTERNS = [
    r"\\bcup\\b", r"\\bchristmas\\b", r"\\bevent\\b", r"\\bnews\\b",
    r"\\blogin\\b", r"\\bstudents?\\b", r"\\bparents\\b", r"\\bwelcome\\b",
    r"\\bhome\\b$", r"^\\d", r"\\bltd\\b", r"\\bgroup\\b$",
]

def is_suspicious(name):
    if not name:
        return False
    n = name.lower().strip()
    if len(n.split()) < 2 or len(n.split()) > 5:
        return True
    for pat in JUNK_PATTERNS:
        if re.search(pat, n):
            return True
    return False

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, business_name, decision_maker_name FROM leads "
        "WHERE decision_maker_name IS NOT NULL AND decision_maker_name != \\'\\'"
    ).fetchall()

    flagged = [dict(r) for r in rows if is_suspicious(r["decision_maker_name"])]

    print(f"Checked {len(rows)} leads with a decision_maker_name.")
    print(f"Flagged {len(flagged)} as suspicious:")
    print()
    for f in flagged:
        lead_id = f["id"]
        name = f["business_name"][:40]
        dm = f["decision_maker_name"]
        print(f"  ID {lead_id:4} | {name:40} | DM field: '{dm}'")

    conn.close()

if __name__ == "__main__":
    main()
'''

FIXED["daily_report.py"] = '''import sqlite3
from datetime import date

conn = sqlite3.connect("data/leads.db")
conn.row_factory = sqlite3.Row
today = date.today().isoformat()

print("=" * 70)
print(f"KNCS LeadGen Pro - Daily Report - {today}")
print("=" * 70)

due = conn.execute("""
    SELECT o.lead_id, l.business_name, l.decision_maker_name, o.channel,
           o.sent_at, o.follow_up_date, o.replied
    FROM outreach_log o
    JOIN leads l ON l.id = o.lead_id
    WHERE o.follow_up_date <= ? AND (o.replied IS NULL OR o.replied = 0)
    ORDER BY o.follow_up_date ASC
""", (today,)).fetchall()

print()
print(f"FOLLOW UP TODAY ({len(due)} leads):")
for r in due:
    lead_id = r["lead_id"]
    name = r["business_name"][:35]
    dm = r["decision_maker_name"] or ""
    sent = r["sent_at"][:10]
    channel = r["channel"]
    print(f"  ID {lead_id:4} | {name:35} | {dm:22} | sent {sent} via {channel}")

total_sent = conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
total_replied = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE replied = 1").fetchone()[0]
total_converted = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE converted = 1").fetchone()[0]

print()
print("PIPELINE TOTALS:")
print(f"  Total outreach sent:  {total_sent}")
print(f"  Total replies:        {total_replied}")
print(f"  Total converted:      {total_converted}")
if total_sent:
    rate = total_replied / total_sent * 100
    print(f"  Reply rate:           {rate:.1f}%")

conn.close()
'''

def main():
    for filename, content in FIXED.items():
        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Fixed: {filename}")
    print("\nDone. Both files rewritten with correct syntax.")

if __name__ == "__main__":
    main()