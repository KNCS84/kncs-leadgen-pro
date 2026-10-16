# test_email_validation.py
# Verifies BUG-RE-01/02/03/06/07 fixes are working correctly
# Run with: python test_email_validation.py

import sys
sys.path.insert(0, '.')

from engine.enricher import is_junk_email, is_foreign_email

print("\n" + "="*60)
print("  Email Validation Test Suite")
print("="*60)

# ── Cases that MUST be rejected ────────────────────────────────
MUST_REJECT = [
    # BUG-RE-03: comma in local part
    ("kaleem,.naila@atlas.gov.gr",   "comma in local part"),
    ("test,name@domain.com",          "comma in local part"),
    ("first last@domain.com",         "space in local part"),
    # BUG-RE-02 / BUG-RE-06: Bing CDN domains
    ("rana.zubair@cc.bingj.com",      "Bing CDN domain"),
    ("anyone@bingj.com",              "Bing CDN domain"),
    ("anyone@bingj.net",              "Bing CDN domain"),
    # Foreign TLDs
    ("someone@company.gr",            "Greek TLD"),
    ("someone@company.de",            "German TLD"),
    ("someone@company.fr",            "French TLD"),
    # Standard junk
    ("noreply@company.com",           "noreply pattern"),
    ("test@example.com",              "example pattern"),
    ("info@example.com",              "example pattern"),
    # Google CDN
    ("user@googleusercontent.com",    "Google CDN"),
    ("user@gstatic.com",              "Google CDN"),
    # No TLD
    ("user@nodomain",                 "no TLD"),
    # Consecutive dots
    ("user..name@domain.com",         "consecutive dots"),
    ("user@domain..com",              "consecutive dots in domain"),
    # Multiple @ signs
    ("a@b@c.com",                     "multiple @ signs"),
    # Image extensions
    ("logo.png@domain.com",           "image extension"),
]

# ── Cases that MUST be accepted ────────────────────────────────
MUST_ACCEPT = [
    ("info@nottinghamclinic.co.uk",   "valid UK business email"),
    ("contact@surgerycentre.com",     "valid .com business email"),
    ("admin@happyears.co.uk",         "valid UK email"),
    ("sarah.jones@dentalpractice.co.uk", "valid personal email"),
    ("enquiries@lasercliniclimited.co.uk", "valid UK email"),
    ("hello@merciaskin.com",          "valid .com email"),
]

passed = 0
failed = 0

print("\n── MUST REJECT (expect: REJECTED) ──────────────────────")
for email, reason in MUST_REJECT:
    result = is_junk_email(email)
    status = "REJECTED ✓" if result else "ACCEPTED ✗ FAIL"
    if result:
        passed += 1
    else:
        failed += 1
    print(f"  {status:15}  {email:45}  ({reason})")

print("\n── MUST ACCEPT (expect: ACCEPTED) ──────────────────────")
for email, reason in MUST_ACCEPT:
    result = is_junk_email(email)
    status = "ACCEPTED ✓" if not result else "REJECTED ✗ FAIL"
    if not result:
        passed += 1
    else:
        failed += 1
    print(f"  {status:15}  {email:45}  ({reason})")

print("\n" + "="*60)
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
print(f"  Result: {'ALL GOOD — proceed to reenrich.py' if failed == 0 else 'FAILURES FOUND — do not proceed'}")
print("="*60 + "\n")