"""
export_hot_leads.py — KNCS LeadGen Pro v2
Schema-aware version matching your actual leads.db structure.
"""

import sqlite3, csv, os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.environ.get("DB_PATH", "data/leads.db")
EXPORT_DIR = "data/exports"

NIGERIA_MARKERS = ["nigeria", "benin city", "lagos", "abuja", "uselu", "ugbowo", "edo"]

NICHE_KEYWORDS = [
    (["care home", "nursing home", "residential home", "residential care",
      "nursing and residential", "dementia", "convent", "manor care",
      "rest home", "cottage residential"], "Care Home"),
    (["dental", "dentist", "smile clinic", "orthodont"], "Dental"),
    (["aesthetic", "cosmetic", "botox", "laser clinic", "skin clinic",
      "hair loss clinic", "ear wax", "beauty clinic"], "Aesthetics"),
    (["private gp", "walk in clinic", "medical clinic", "health clinic",
      "circumcision", "ent clinic"], "Private GP/Clinic"),
    (["financial advis", "wealth management", " ifa ", "pension advis",
      "mortgage broker", "financial planning"], "IFA"),
]

def classify_niche(vertical, business_name):
    if vertical and str(vertical).strip():
        return str(vertical).strip()
    n = (business_name or "").lower()
    for kws, label in NICHE_KEYWORDS:
        if any(k in n for k in kws):
            return label
    return "Other"

def is_nigerian(row):
    for field in ("country", "market", "location"):
        val = str(row.get(field) or "").lower()
        if any(m in val for m in NIGERIA_MARKERS):
            return True
    return False

def looks_like_named_person(dm):
    if not dm:
        return False
    junk = ["login", "parents and students", "n/a", "unknown", "student", "pinnacle groups"]
    if any(j in dm.lower() for j in junk):
        return False
    return len(dm.split()) >= 2

def table_columns(conn, table):
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except sqlite3.OperationalError:
        return []

def find_email_source(conn):
    leads_cols = table_columns(conn, "leads")
    if "email" in leads_cols:
        rows = conn.execute("SELECT id, email FROM leads").fetchall()
        return {r[0]: r[1] for r in rows if r[1]}

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    email_map = {}
    diagnostic = []
    for t in tables:
        if t == "leads":
            continue
        cols = table_columns(conn, t)
        email_cols = [c for c in cols if "email" in c.lower()]
        if not email_cols:
            continue
        fk_candidates = [c for c in cols if c.lower() in
                          ("lead_id", "business_id", "company_id", "id")]
        diagnostic.append((t, cols, email_cols, fk_candidates))
        if not fk_candidates:
            continue
        fk, email_col = fk_candidates[0], email_cols[0]
        try:
            rows = conn.execute(f"SELECT {fk}, {email_col} FROM {t}").fetchall()
            for lead_id, email in rows:
                if email and lead_id not in email_map:
                    email_map[lead_id] = email
        except sqlite3.OperationalError:
            continue

    if not email_map and diagnostic:
        print("\n[DIAGNOSTIC] Could not auto-resolve emails. Candidate tables found:")
        for t, cols, email_cols, fk_candidates in diagnostic:
            print(f"  Table '{t}': email columns={email_cols}, possible FK columns={fk_candidates}")
            print(f"    Full columns: {cols}")
        print("Paste this block back and I will write the exact join.\n")
    elif not diagnostic:
        print("\n[DIAGNOSTIC] No table anywhere contains an email-like column.")
        print("check_db.py must be building emails a different way — open it and search for 'email'.\n")

    return email_map

def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    email_map = find_email_source(conn)

    raw_rows = conn.execute("SELECT * FROM leads").fetchall()

    candidates = []
    for r in raw_rows:
        row = dict(r)  # fixes the .get() crash

        business = row.get("business_name")
        phone = row.get("phone")
        dm = row.get("decision_maker_name") or row.get("director_name")
        dm_title = row.get("decision_maker_title") or ""
        email = email_map.get(row.get("id"))

        if not business or is_nigerian(row):
            continue
        if not (email or phone):
            continue
        if not email and not looks_like_named_person(dm):
            continue

        niche = classify_niche(row.get("vertical"), business)
        contacted = row.get("contacted") or row.get("contact_status") or ""

        candidates.append({
            "id": row.get("id"), "niche": niche, "business": business,
            "dm": dm or "", "dm_title": dm_title,
            "email": email or "", "phone": phone or "",
            "website": row.get("website") or "",
            "fit_score": row.get("fit_score") or 0,
            "urgency_score": row.get("urgency_score") or 0,
            "reachability_score": row.get("reachability_score") or 0,
            "lead_priority": row.get("lead_priority") or "",
            "contacted": contacted,
            "cqc_rating": row.get("cqc_rating") or "",
        })

    email_counts, phone_counts = defaultdict(list), defaultdict(list)
    for c in candidates:
        if c["email"]:
            email_counts[c["email"].lower()].append(c["business"])
        if c["phone"]:
            phone_counts[c["phone"]].append(c["business"])
    for c in candidates:
        c["shared_contact_flag"] = (
            len(email_counts.get(c["email"].lower(), [])) > 1 if c["email"] else False
        ) or (
            len(phone_counts.get(c["phone"], [])) > 1 if c["phone"] else False
        )

    NICHE_ORDER = {"Care Home": 0, "Dental": 1, "Aesthetics": 2,
                   "Private GP/Clinic": 3, "IFA": 4, "Other": 9}

    def sort_key(c):
        already = 1 if c["contacted"] in (1, "1", True, "yes", "contacted") else 0
        complete = 0 if (c["email"] and c["phone"] and looks_like_named_person(c["dm"])) else 1
        return (already, c["shared_contact_flag"], NICHE_ORDER.get(c["niche"], 9),
                 complete, -float(c["fit_score"] or 0), -float(c["urgency_score"] or 0), c["business"])

    candidates.sort(key=sort_key)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(EXPORT_DIR, f"hot_leads_{timestamp}.csv")
    fieldnames = ["id","niche","business","dm","dm_title","email","phone","website",
                  "fit_score","urgency_score","reachability_score","lead_priority",
                  "contacted","cqc_rating","shared_contact_flag"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for c in candidates:
            w.writerow({k: c[k] for k in fieldnames})

    print(f"\nExported {len(candidates)} contactable, non-Nigerian leads to {out_path}\n")

    clean_new = [c for c in candidates if not c["shared_contact_flag"]
                 and c["contacted"] not in (1, "1", True, "yes", "contacted")]

    print("=" * 120)
    print("TOP 20 — SEND TODAY")
    print("=" * 120)
    for c in clean_new[:20]:
        print(f"[{c['niche']:16}] {c['business'][:35]:35} | {c['dm'][:22]:22} | {c['email'][:32]:32} | {c['phone']}")

    flagged = [c for c in candidates if c["shared_contact_flag"]]
    if flagged:
        print(f"\nFLAGGED — {len(flagged)} share contact info with another business (verify first)\n")
        for c in flagged[:15]:
            print(f"[{c['niche']:16}] {c['business'][:35]:35} | {c['dm'][:22]:22} | {c['email'][:32]:32} | {c['phone']}")

    conn.close()

if __name__ == "__main__":
    main()