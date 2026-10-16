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
