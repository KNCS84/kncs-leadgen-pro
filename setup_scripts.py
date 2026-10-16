"""
setup_scripts.py — One-time bootstrap.
Creates all the pipeline scripts as real files in this folder.
Run once: python setup_scripts.py
"""

import os

FILES = {}

FILES["check_opportunity_tables.py"] = '''
import sqlite3

conn = sqlite3.connect('data/leads.db')
tables = ['care_home_opportunities', 'care_acquisition_signals', 'care_operator_groups',
          'dental_opportunities', 'dental_growth_signals', 'dental_review_analysis',
          'website_health_scans', 'digital_maturity_scores', 'cyber_risk_profiles',
          'technology_profiles', 'decision_makers']

for t in tables:
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({t})').fetchall()]
        print(f"{t}: {n} rows")
        print(f"  columns: {cols}\\n")
    except sqlite3.OperationalError as e:
        print(f"{t}: ERROR - {e}\\n")

conn.close()
'''

FILES["validate_leads.py"] = '''
import sqlite3, re

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
    print(f"Flagged {len(flagged)} as suspicious:\\n")
    for f in flagged:
        print(f"  ID {f[\\'id\\']:4} | {f[\\'business_name\\'][:40]:40} | DM field: \\'{f[\\'decision_maker_name\\']}\\'")

    conn.close()

if __name__ == "__main__":
    main()
'''

FILES["log_outreach.py"] = '''
import sqlite3, argparse
from datetime import datetime, timedelta

DB_PATH = "data/leads.db"

def log_one(conn, lead_id, channel, template, follow_up_days=3):
    now = datetime.now().isoformat(timespec="seconds")
    follow_up = (datetime.now() + timedelta(days=follow_up_days)).date().isoformat()
    conn.execute("""
        INSERT INTO outreach_log
            (lead_id, action, channel, status, message_text, sent_at,
             opened, replied, converted, follow_up_date, created_at)
        VALUES (?, \\'sent\\', ?, \\'sent\\', ?, ?, 0, 0, 0, ?, ?)
    """, (lead_id, channel, template, now, follow_up, now))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int)
    p.add_argument("--ids", type=str)
    p.add_argument("--channel", default="email")
    p.add_argument("--template", default="")
    p.add_argument("--follow-up-days", type=int, default=3)
    args = p.parse_args()

    ids = []
    if args.id:
        ids = [args.id]
    elif args.ids:
        ids = [int(x.strip()) for x in args.ids.split(",")]
    else:
        print("Provide --id or --ids")
        return

    conn = sqlite3.connect(DB_PATH)
    for lead_id in ids:
        log_one(conn, lead_id, args.channel, args.template, args.follow_up_days)
        try:
            conn.execute(
                "UPDATE leads SET contact_status = \\'contacted\\', contacted = 1 WHERE id = ?",
                (lead_id,)
            )
        except sqlite3.OperationalError:
            pass
    conn.commit()
    print(f"Logged {len(ids)} outreach actions.")
    conn.close()

if __name__ == "__main__":
    main()
'''

FILES["daily_report.py"] = '''
import sqlite3
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

print(f"\\nFOLLOW UP TODAY ({len(due)} leads):")
for r in due:
    print(f"  ID {r[\\'lead_id\\']:4} | {r[\\'business_name\\'][:35]:35} | "
          f"{r[\\'decision_maker_name\\'] or \\'\\':22} | sent {r[\\'sent_at\\'][:10]} via {r[\\'channel\\']}")

total_sent = conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
total_replied = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE replied = 1").fetchone()[0]
total_converted = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE converted = 1").fetchone()[0]

print(f"\\nPIPELINE TOTALS:")
print(f"  Total outreach sent:  {total_sent}")
print(f"  Total replies:        {total_replied}")
print(f"  Total converted:      {total_converted}")
if total_sent:
    print(f"  Reply rate:           {total_replied/total_sent*100:.1f}%")

conn.close()
'''

FILES["mark_replied.py"] = '''
import sqlite3, argparse

p = argparse.ArgumentParser()
p.add_argument("--id", type=int, required=True)
p.add_argument("--outcome", default="")
args = p.parse_args()

conn = sqlite3.connect("data/leads.db")
conn.execute("""
    UPDATE outreach_log SET replied = 1, outcome = ?
    WHERE lead_id = ? AND id = (
        SELECT id FROM outreach_log WHERE lead_id = ? ORDER BY sent_at DESC LIMIT 1
    )
""", (args.outcome, args.id, args.id))
conn.commit()
print(f"Marked lead {args.id} as replied. Outcome: {args.outcome}")
conn.close()
'''

def main():
    for filename, content in FILES.items():
        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        print(f"Created: {filename}")
    print(f"\nDone. {len(FILES)} scripts created in {os.getcwd()}")

if __name__ == "__main__":
    main()