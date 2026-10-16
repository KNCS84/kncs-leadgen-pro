import sqlite3
conn = sqlite3.connect('data/leads.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(id) FROM leads WHERE recommended_opener LIKE 'Hi Amechi%'")
count = cur.fetchone()[0]
print('Bad openers remaining:', count)
conn.close()
