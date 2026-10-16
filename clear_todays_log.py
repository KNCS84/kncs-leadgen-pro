import sqlite3
conn = sqlite3.connect('data/leads.db')
conn.execute("DELETE FROM outreach_log WHERE date(sent_at) = date('now')")
conn.commit()
print("Cleared today's phantom log entries. Ready to log real sends.")
conn.close()