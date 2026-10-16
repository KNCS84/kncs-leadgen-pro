# test_auditor.py
# Run this with: python test_auditor.py

from engine.website_auditor import WebsiteAuditor

print("Loading auditor...")
a = WebsiteAuditor()

print("Auditing SEB Healthcare website...")
r = a.audit_website('https://sebhealthcareltd.co.uk', 'SEB Healthcare')

print("=" * 50)
print("AUDIT RESULTS")
print("=" * 50)
print("Score:   ", r['score'], "/10")
print("Grade:   ", r['grade'])
print("Priority:", r['priority'])
print()
print("Issues found:")
for issue in r['issues']:
    print("  -", issue)
print()
print("Strengths:")
for strength in r['strengths']:
    print("  +", strength)
print()
print("Email hook:")
print(" ", r['email_hook'])
print()
print("Recommendation:")
print(" ", r['recommendation'])