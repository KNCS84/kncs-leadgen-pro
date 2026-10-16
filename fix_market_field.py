# fix_market_field.py — run once: python fix_market_field.py
from database import get_connection

conn = get_connection()
cur = conn.cursor()

updates = [
    ("United States", "United States"),
    ("USA", "United States"),
    ("US", "United States"),
    ("United Kingdom", "United Kingdom"),
    ("UK", "United Kingdom"),
    ("Nigeria", "Nigeria"),
]

for country_val, market_val in updates:
    cur.execute(
        "UPDATE leads SET market = ? "
        "WHERE LOWER(TRIM(country)) = LOWER(?) "
        "AND (market IS NULL OR TRIM(market) = '')",
        (market_val, country_val)
    )
    print(f"{country_val}: updated {cur.rowcount} rows")

conn.commit()
conn.close()
print("Done.")