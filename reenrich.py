# reenrich.py
# KNCS LeadGen Pro v10.3 — Re-enrichment Script
# Finds email + phone for leads missing them
# Run with: python reenrich.py
#
# Changes vs v10.2:
#   FIX-RE-01: _is_valid_email() now checks BLOCKED_EMAIL_DOMAINS
#              and BLOCKED_EMAIL_LOCAL_PARTS — brings it in line with
#              deep_research.py v10.8 (FIX-DR-24).
#              Previously gmail.com, nhs.net, outlook.com etc would
#              pass validation here and reach the database.
#
#   FIX-RE-02: Replaced weak _is_valid_phone() (9-15 digit count only)
#              with full _is_valid_uk_phone() from deep_research.py.
#              Previous version accepted 087x premium rate, 070x
#              personal numbering, 9-digit numbers, and international
#              numbers up to 15 digits. None of these are valid UK
#              business numbers.
#
#   FIX-RE-03: Added _email_fail_reason() for informative rejection
#              output. Previously all rejections printed the same
#              "failed validation" message with no detail.

import sys
import re
import time
sys.path.insert(0, '.')

from database import get_connection
from engine.enricher import enrich_lead, is_junk_email, is_foreign_email

# ─────────────────────────────────────────────────────────
# FIX-RE-01: Consumer, NHS and institutional email domains
# Mirrors BLOCKED_EMAIL_DOMAINS in deep_research.py v10.8
# Must be kept in sync if either file is updated.
# ─────────────────────────────────────────────────────────

BLOCKED_EMAIL_DOMAINS = {
    # Consumer webmail
    "gmail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "outlook.co.uk",
    "icloud.com",
    "me.com",
    "live.com",
    "live.co.uk",
    "msn.com",
    "btinternet.com",
    "sky.com",
    "talktalk.net",
    "virginmedia.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    # NHS — staff emails, not business contacts
    "nhs.net",
    "nhs.uk",
    # Placeholder / test domains
    "email.com",
    "example.com",
    "test.com",
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwam.com",
    "sharklasers.com",
    # Known directories used as email domains
    "wheree.com",
    "cylex.co.uk",
    "cylex-uk.co.uk",
    "yell.com",
}

# Role addresses and placeholders — not decision maker contacts
BLOCKED_EMAIL_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "myname",
    "yourname",
    "example",
    "test",
    "postmaster",
    "mailer-daemon",
    "bounce",
    "bounces",
}

# ─────────────────────────────────────────────────────────
# Validation — email
# ─────────────────────────────────────────────────────────

_RFC5321_LOCAL = re.compile(r'^[a-zA-Z0-9._%+\-]+$')


def _is_valid_email(email: str) -> bool:
    """
    Hard gate — rejects anything that should never reach the DB.

    Checks (in order):
      1. Non-empty string with exactly one @
      2. RFC 5321 local part characters
      3. No double dots anywhere
      4. Domain has at least one dot
      5. FIX-RE-01: domain in BLOCKED_EMAIL_DOMAINS
      6. FIX-RE-01: domain ends in .ac.uk (academic institution)
      7. FIX-RE-01: local part in BLOCKED_EMAIL_LOCAL_PARTS
      8. is_junk_email() from enricher.py
      9. is_foreign_email() from enricher.py
    """
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    if email.count('@') != 1:
        return False
    local, domain = email.split('@', 1)
    if not _RFC5321_LOCAL.match(local):
        return False
    if '..' in local or '..' in domain:
        return False
    if '.' not in domain:
        return False

    # FIX-RE-01: block consumer, NHS, institutional domains
    if domain in BLOCKED_EMAIL_DOMAINS:
        return False

    # FIX-RE-01: block all academic institutions
    if domain.endswith('.ac.uk'):
        return False

    # FIX-RE-01: block placeholder and role-address local parts
    if local in BLOCKED_EMAIL_LOCAL_PARTS:
        return False

    if is_junk_email(email):
        return False
    if is_foreign_email(email):
        return False

    return True


def _email_fail_reason(email: str) -> str:
    """
    FIX-RE-03: Returns a short string explaining why an email
    fails validation. Used for informative rejection output.
    Returns empty string if the email passes all checks.
    """
    if not email or not isinstance(email, str):
        return "empty"
    email = email.strip().lower()

    if email.count('@') != 1:
        return "no-at-sign"

    local, domain = email.split('@', 1)

    if not _RFC5321_LOCAL.match(local):
        return "invalid-local-chars"

    if '..' in local or '..' in domain:
        return "double-dot"

    if '.' not in domain:
        return "no-dot-in-domain"

    if domain in BLOCKED_EMAIL_DOMAINS:
        return f"blocked-domain:{domain}"

    if domain.endswith('.ac.uk'):
        return "academic-institution"

    if local in BLOCKED_EMAIL_LOCAL_PARTS:
        return f"blocked-local:{local}"

    if is_junk_email(email):
        return "enricher-junk"

    if is_foreign_email(email):
        return "foreign-email"

    return ""


# ─────────────────────────────────────────────────────────
# Validation — phone
# FIX-RE-02: Full UK Ofcom validation replaces weak digit count
# ─────────────────────────────────────────────────────────

# Valid mobile prefixes: 740–799 (Ofcom allocated ranges)
_VALID_MOBILE_PREFIXES = set(range(740, 800))
# Valid 08 prefixes: 800 (freephone), 808 (freephone)
_VALID_08_PREFIXES     = {"800", "808"}


def _is_valid_uk_phone(phone: str) -> bool:
    """
    Validates a UK phone number against Ofcom allocation rules.

    Accepts:
        07400-07999  mobile (11 digits)
        02x          London/geographic (11 digits)
        01x          geographic landline (10-11 digits)
        03x          non-geographic (11 digits)
        0800/0808    freephone (11 digits)

    Rejects:
        070xx        personal numbering
        071/072/073  unallocated
        081/082      old pre-1995 codes
        084x/087x    premium / revenue share
        09xxx        premium rate
        < 10 or > 11 digits
    """
    if not phone or not isinstance(phone, str):
        return False

    cleaned = re.sub(r'[\s\-\.\(\)]', '', phone.strip())
    if cleaned.startswith('+44'):
        cleaned = '0' + cleaned[3:]
    if not cleaned.startswith('0'):
        return False

    digits = re.sub(r'\D', '', cleaned)
    if len(digits) < 10 or len(digits) > 11:
        return False

    # Premium rate — always reject
    if digits.startswith('09'):
        return False

    # Revenue share / premium
    if digits.startswith('084') or digits.startswith('087'):
        return False

    # Old pre-1995 codes
    if digits.startswith('081') or digits.startswith('082'):
        return False

    # Mobile
    if digits.startswith('07'):
        if len(digits) != 11:
            return False
        # 070xx = personal numbering
        if digits.startswith('070'):
            return False
        mobile_prefix = int(digits[1:4])
        if mobile_prefix not in _VALID_MOBILE_PREFIXES:
            return False
        return True

    if digits.startswith('02'):
        return len(digits) == 11

    if digits.startswith('01'):
        return len(digits) in (10, 11)

    if digits.startswith('03'):
        return len(digits) == 11

    if digits.startswith('08'):
        three_digit = digits[1:4]
        if three_digit not in _VALID_08_PREFIXES:
            return False
        return len(digits) == 11

    return False


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

print("\nLoading leads missing email or phone...\n")

conn = get_connection()
cur  = conn.cursor()

cur.execute("""
    SELECT
        id, business_name, city, country,
        website, phone, email,
        decision_maker_name, industry,
        postcode, source
    FROM leads
    WHERE (email IS NULL OR email = '')
    OR    (phone IS NULL OR phone = '')
    ORDER BY id DESC
    LIMIT 30
""")

columns = [d[0] for d in cur.description]
rows    = cur.fetchall()
leads   = [dict(zip(columns, row)) for row in rows]
conn.close()

print(f"Found {len(leads)} leads to re-enrich\n")
print("-" * 60)

total_improved = 0

for i, lead in enumerate(leads):
    name = lead.get("business_name", "?")
    print(f"\n[{i+1}/{len(leads)}] {name[:55]}")
    print(
        f"  Before: email={lead.get('email') or 'NONE':30} "
        f"phone={lead.get('phone') or 'NONE'}"
    )

    before = {
        "email":   lead.get("email",                "") or "",
        "phone":   lead.get("phone",                "") or "",
        "website": lead.get("website",              "") or "",
        "dm":      lead.get("decision_maker_name",  "") or "",
    }

    try:
        enriched = enrich_lead(
            lead,
            find_email=True,
            find_phone=True,
        )
    except Exception as exc:
        print(f"  ERROR: {exc}")
        continue

    # ── Validate email before any DB write ───────────────────────
    # FIX-RE-01/03: use _email_fail_reason() for informative output
    raw_email = enriched.get("email", "") or ""
    if raw_email:
        reason = _email_fail_reason(raw_email)
        if reason:
            print(
                f"  REJECTED email [{reason}]: "
                f"{raw_email[:55]}"
            )
            enriched["email"]            = ""
            enriched["email_confidence"] = 0
        # else: email passed — no output needed, gained[] handles it

    # ── Validate phone before any DB write ───────────────────────
    # FIX-RE-02: full UK Ofcom rules instead of digit count
    raw_phone = enriched.get("phone", "") or ""
    if raw_phone and not _is_valid_uk_phone(raw_phone):
        print(f"  REJECTED phone (invalid UK number): {raw_phone}")
        enriched["phone"] = ""

    # ── Determine what was actually gained ───────────────────────
    gained = []
    if enriched.get("email") and not before["email"]:
        gained.append(f"email={enriched['email'][:45]}")
    if enriched.get("phone") and not before["phone"]:
        gained.append(f"phone={enriched['phone']}")
    if enriched.get("website") and not before["website"]:
        gained.append("website found")
    if enriched.get("decision_maker_name") and not before["dm"]:
        gained.append(f"dm={enriched['decision_maker_name']}")

    if gained:
        print(f"  FOUND: {', '.join(gained)}")

        conn2 = None
        try:
            conn2 = get_connection()
            cur2  = conn2.cursor()
            cur2.execute(
                """
                UPDATE leads SET
                    email = CASE
                        WHEN (email IS NULL OR email = '')
                        AND ? != ''
                        THEN ?
                        ELSE email
                    END,
                    phone = CASE
                        WHEN (phone IS NULL OR phone = '')
                        AND ? != ''
                        THEN ?
                        ELSE phone
                    END,
                    website = CASE
                        WHEN (website IS NULL OR website = '')
                        AND ? != ''
                        THEN ?
                        ELSE website
                    END,
                    decision_maker_name = CASE
                        WHEN (
                            decision_maker_name IS NULL
                            OR decision_maker_name = ''
                        )
                        AND ? != ''
                        THEN ?
                        ELSE decision_maker_name
                    END,
                    enrichment_status = 'complete',
                    last_enriched_at  = datetime('now')
                WHERE id = ?
                """,
                (
                    enriched.get("email",                ""),
                    enriched.get("email",                ""),
                    enriched.get("phone",                ""),
                    enriched.get("phone",                ""),
                    enriched.get("website",              ""),
                    enriched.get("website",              ""),
                    enriched.get("decision_maker_name",  ""),
                    enriched.get("decision_maker_name",  ""),
                    lead["id"],
                ),
            )
            conn2.commit()
            total_improved += 1
        except Exception as exc:
            print(f"  DB ERROR: {exc}")
        finally:
            if conn2:
                conn2.close()
    else:
        print("  Nothing new found")

    time.sleep(2)

print(f"\n{'=' * 60}")
print(f"Re-enrichment complete")
print(f"Leads improved: {total_improved} / {len(leads)}")
print(f"\nRun check.py to see updated results")