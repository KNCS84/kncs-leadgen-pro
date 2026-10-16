import sqlite3

conn = sqlite3.connect("data/leads.db")
cur = conn.cursor()
cur.execute("PRAGMA table_info(leads)")
for row in cur.fetchall():
    print(row[1])
conn.close()