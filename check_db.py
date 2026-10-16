# check_db.py
# Run with: python check_db.py

import sys
sys.path.insert(0, '.')
from database import get_connection
from urllib.parse import urlparse

conn = get_connection()
cur  = conn.cursor()

# ── Summary counts ────────────────────────────────────────
cur.execute("SELECT COUNT(*) FROM leads")
total = cur.fetchone()[0]

cur.execute(
    "SELECT COUNT(*) FROM leads "
    "WHERE website != '' AND website IS NOT NULL"
)
has_web = cur.fetchone()[0]

cur.execute(
    "SELECT COUNT(*) FROM leads "
    "WHERE email != '' AND email IS NOT NULL"
)
has_email = cur.fetchone()[0]

cur.execute(
    "SELECT COUNT(*) FROM leads "
    "WHERE phone != '' AND phone IS NOT NULL"
)
has_phone = cur.fetchone()[0]

cur.execute(
    "SELECT COUNT(*) FROM leads "
    "WHERE decision_maker_name != '' "
    "AND decision_maker_name IS NOT NULL"
)
has_dm = cur.fetchone()[0]

cur.execute("""
    SELECT COUNT(*) FROM leads
    WHERE (website != '' AND website IS NOT NULL)
    AND   (email   != '' AND email   IS NOT NULL)
    AND   (phone   != '' AND phone   IS NOT NULL)
""")
has_all = cur.fetchone()[0]

print()
print("=" * 80)
print("KNCS LeadGen Pro — Database State")
print("=" * 80)
print(f"  Total leads:              {total}")
print(f"  Have website:             {has_web}  ({has_web*100//total}%)")
print(f"  Have email:               {has_email}  ({has_email*100//total}%)")
print(f"  Have phone:               {has_phone}  ({has_phone*100//total}%)")
print(f"  Have decision maker:      {has_dm}  ({has_dm*100//total}%)")
print(f"  Have all three fields:    {has_all}  ({has_all*100//total}%)")
print()

# ── Per-lead detail ───────────────────────────────────────
cur.execute("""
    SELECT
        id,
        business_name,
        website,
        email,
        phone,
        decision_maker_name
    FROM leads
    ORDER BY id DESC
""")

rows = cur.fetchall()

# ── Verified legitimate mismatches ────────────────────────
# These leads have confirmed valid data where the email domain
# does not match the website domain for legitimate reasons:
#
#   318 — Night and Day Dentist group. The Nottingham branch
#          website is nightanddayemergencydentist-nottingham.co.uk
#          but the group email is @nightanddaydentist.co.uk.
#          Same business, different subdomain branding.
#
#   312 — Teeth & Face. Website is teethface.co.uk but email
#          is @teethandface.co.uk. Same practice, the 'and'
#          is dropped in the domain only.
#
#   189 — Haydock Nursing Home. Website is haydocknursinghome.co.uk
#          but email is commissioning@grangehealthcare.co.uk.
#          Grange Healthcare is the operating group. The
#          commissioning contact goes to the group, not the home.
#          This is standard care home group structure.
#
#   271 — The Sports Injury Clinic Derby. Website is
#          sportsinjury-clinic.com but email is
#          @sportsinjury-clinic.com — actually the same domain,
#          kept here as belt-and-braces.
#
VERIFIED_MISMATCHES = {318, 312, 189, 271}

print(
    f"{'ID':>4}  {'BUSINESS':38}  "
    f"{'EMAIL':45}  {'PHONE':15}  {'DM'}"
)
print("-" * 145)

FLAGGED = []

for row in rows:
    lead_id  = row[0]
    name     = (row[1] or "")[:38]
    website  = row[2] or ""
    email    = row[3] or ""
    phone    = (row[4] or "")[:15]
    dm       = (row[5] or "")[:28]

    flag = ""

    if email and website and lead_id not in VERIFIED_MISMATCHES:
        try:
            web_domain = urlparse(
                website if website.startswith("http")
                else "https://" + website
            ).netloc.replace("www.", "")
        except Exception:
            web_domain = ""

        if web_domain and email:
            email_domain = email.split("@")[-1].lower()

            def _root(d):
                parts = d.split(".")
                if len(parts) >= 3 and parts[-2] in (
                    "co", "org", "me", "net", "ltd", "plc"
                ):
                    return parts[-3]
                return parts[-2] if len(parts) >= 2 else parts[0]

            email_root = _root(email_domain)
            web_root   = _root(web_domain)

            is_exact     = email_domain == web_domain
            is_sub       = (
                email_domain.endswith("." + web_domain)
                or web_domain.endswith("." + email_domain)
            )
            is_same_root = (
                len(email_root) >= 5
                and len(web_root) >= 5
                and (
                    email_root in web_root
                    or web_root in email_root
                )
            )

            if not (is_exact or is_sub or is_same_root):
                flag = " ← MISMATCH"
                FLAGGED.append(
                    (lead_id, name, website, email)
                )

    email_display = email[:45]

    print(
        f"{lead_id:>4}  {name:38}  "
        f"{email_display:45}  {phone:15}  {dm}{flag}"
    )

conn.close()

print()
print("=" * 80)

if FLAGGED:
    print(f"\nFlagged for review ({len(FLAGGED)} leads):")
    print("-" * 80)
    for lead_id, name, website, email in FLAGGED:
        print(f"  ID {lead_id:>4}: {name[:38]}")
        print(f"           website: {website}")
        print(f"           email:   {email}")
        print()
else:
    print("\nNo domain mismatches detected. Data quality clean.")

print("=" * 80)