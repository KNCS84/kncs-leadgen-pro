import sqlite3
import re
DB_PATH = "data/leads.db"
def fix_openers():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, business_name, recommended_opener
        FROM leads
        WHERE recommended_opener LIKE 'Hi Amechi%'
    """)
    rows = cur.fetchall()
    print(str(len(rows)) + " leads found")
    cities = ["Benin City","Lagos","Abuja","Warri","Asaba","Port Harcourt","Enugu","Owerri","Kano","Ibadan"]
    fixed = 0
    for row in rows:
        lid = row["id"]
        bname = row["business_name"] or "there"
        opener = row["recommended_opener"] or ""
        clean = bname.strip()
        for city in cities:
            if clean.lower().endswith(" " + city.lower()):
                clean = clean[:len(clean)-len(city)-1].strip()
                break
        greeting = "Hi " + clean + " team,"
        new = re.sub(r"^Hi Amechi[,\s]+", greeting + " ", opener, flags=re.IGNORECASE).strip()
        cur.execute("UPDATE leads SET recommended_opener = ? WHERE id = ?", (new, lid))
        fixed += 1
        print("Lead " + str(lid) + ": " + bname)
        print("  Before: " + opener[:60])
        print("  After:  " + new[:60])
    conn.commit()
    conn.close()
    print("Fixed " + str(fixed) + " leads.")
fix_openers()
