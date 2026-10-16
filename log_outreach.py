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
        VALUES (?, \'sent\', ?, \'sent\', ?, ?, 0, 0, 0, ?, ?)
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
                "UPDATE leads SET contact_status = \'contacted\', contacted = 1 WHERE id = ?",
                (lead_id,)
            )
        except sqlite3.OperationalError:
            pass
    conn.commit()
    print(f"Logged {len(ids)} outreach actions.")
    conn.close()

if __name__ == "__main__":
    main()
