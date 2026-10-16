# test_email_template.py
# Run this with: python test_email_template.py

from engine.email_outreach import EmailTemplateEngine

print("Loading template engine...")
t = EmailTemplateEngine()

test_lead = {
    'business_name': 'Sunrise Care Home',
    'decision_maker_name': 'Martin Rafter',
    'email': 'martin@sunrisecare.co.uk',
    'website_score': 3,
    'website_grade': 'F',
    'website_priority': 'HIGH',
    'website_email_hook': 'I visited your website and found it is not mobile-friendly.',
    'website_issues': ['Not mobile-friendly', 'No CQC rating shown'],
    'cqc_rating': 'Good',
}

print("Generating email...")
email = t.generate_email(test_lead, sequence=1)

print("=" * 50)
print("EMAIL PREVIEW")
print("=" * 50)
print("SUBJECT:", email['subject'])
print("TEMPLATE TYPE:", email['template_type'])
print()
print("EMAIL BODY:")
print("-" * 40)
print(email['text'])
print("-" * 40)
print("Done!")