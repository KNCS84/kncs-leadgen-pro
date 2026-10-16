import logging
logging.basicConfig(level=logging.INFO)

from engine.email_finder import find_personal_email

print("\n" + "="*50)
print("TEST 1: Barry Allan Oliver @ standonhouse.co.uk")
print("="*50)
result = find_personal_email(
    full_name="Barry Allan Oliver",
    website_url="https://www.standonhouse.co.uk",
)
print("RESULT:", result)

print("\n" + "="*50)
print("TEST 2: Leonie Verona Ricketts @ highgrove")
print("="*50)
result2 = find_personal_email(
    full_name="Leonie Verona Ricketts",
    website_url="https://www.highgrove.co.uk",
)
print("RESULT:", result2)

print("\n" + "="*50)
print("TEST 3: Domain only — no website URL")
print("="*50)
result3 = find_personal_email(
    full_name="Teresa Millar",
    domain="standonhouse.co.uk",
)
print("RESULT:", result3)

print("\nDone.")