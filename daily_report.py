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
