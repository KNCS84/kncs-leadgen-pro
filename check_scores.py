# check_scores.py
from database import get_connection

conn = get_connection()
cur  = conn.cursor()
cur.execute("""
    SELECT
        MIN(fit_score)  AS min_score,
        MAX(fit_score)  AS max_score,
        AVG(fit_score)  AS avg_score,
        COUNT(*)        AS total_leads,
        SUM(CASE WHEN fit_score >= 70 THEN 1 ELSE 0 END) AS hot_leads
    FROM leads
    WHERE fit_score IS NOT NULL
""")
row = cur.fetchone()
print("Min score  : " + str(row[0]))
print("Max score  : " + str(row[1]))
print("Avg score  : " + str(round(row[2] or 0, 1)))
print("Total leads: " + str(row[3]))
print("HOT leads  : " + str(row[4]))
conn.close()