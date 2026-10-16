# rescore.py
# Run this once to reset old scores and re-score all leads
# Usage: python rescore.py

from database import get_connection
from engine.scorer import score_all_unscored

print("Connecting to database...")
conn = get_connection()

print("Resetting old scores...")
conn.execute("UPDATE leads SET fit_score = NULL, lead_priority = 'unscored'")
conn.commit()
conn.close()
print("Old scores cleared.")

print("Starting re-score of all leads...")
print("This may take a few minutes depending on lead count.")
total = score_all_unscored(delay=1.5)
print("Done. Leads scored: " + str(total))