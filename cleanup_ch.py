import sqlite3

conn = sqlite3.connect("data/leads.db")
cur = conn.cursor()
cur.execute("""
    UPDATE leads
    SET decision_maker_name = NULL,
        decision_maker_first = NULL,
        decision_maker_title = NULL,
        decision_maker_source = NULL,
        decision_maker_confidence = NULL,
        dm_source = NULL,
        director_name = NULL
    WHERE (dm_source = 'companies_house'
           OR decision_maker_source = 'companies_house')
      AND country NOT IN ('United Kingdom', 'UK', 'GB', 'England',
                           'Scotland', 'Wales', 'Northern Ireland')
""")
print("Rows updated:", cur.rowcount)
conn.commit()
conn.close()