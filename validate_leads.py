import sqlite3, re

DB_PATH = "data/leads.db"

JUNK_PATTERNS = [
    r"\bcup\b", r"\bchristmas\b", r"\bevent\b", r"\bnews\b",
    r"\blogin\b", r"\bstudents?\b", r"\bparents\b", r"\bwelcome\b",
    r"\bhome\b$", r"^\d", r"\bltd\b", r"\bgroup\b$",
]

def is_suspicious(name):
    if not name:
        return False
    n = name.lower().strip()
    if len(n.split()) < 2 or len(n.split()) > 5:
        return True
    for pat in JUNK_PATTERNS:
        if re.search(pat, n):
            return True
    return False

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, business_name, decision_maker_name FROM leads "
        "WHERE decision_maker_name IS NOT NULL AND decision_maker_name != \'\'"
    ).fetchall()

    flagged = [dict(r) for r in rows if is_suspicious(r["decision_maker_name"])]

    print(f"Checked {len(rows)} leads with a decision_maker_name.")
    print(f"Flagged {len(flagged)} as suspicious:")
    print()
    for f in flagged:
        lead_id = f["id"]
        name = f["business_name"][:40]
        dm = f["decision_maker_name"]
        print(f"  ID {lead_id:4} | {name:40} | DM field: '{dm}'")

    conn.close()

if __name__ == "__main__":
    main()
