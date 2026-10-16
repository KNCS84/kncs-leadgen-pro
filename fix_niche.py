from database import get_connection

conn = get_connection()

industry_map = {
    "private_school":   "Private Schools",
    "private_hospital": "Private Hospitals",
    "real_estate":      "Real Estate",
    "hotel":            "Hotels & Guest Houses",
    "manufacturing":    "Manufacturing",
    "construction":     "Construction",
    "law_firm":         "Law Firms",
}

for key, label in industry_map.items():
    conn.execute(
        """
        UPDATE leads
        SET industry = ?
        WHERE country = 'Nigeria'
        AND (
            industry = ?
            OR industry LIKE ?
        )
        """,
        (label, key, "%" + key.replace("_", " ") + "%")
    )

conn.commit()
conn.close()

print("✅ Done — Nigerian industry labels cleaned")