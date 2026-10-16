"""
test_decision_maker.py
Run: python test_decision_maker.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from engine.nigeria.decision_maker_finder import DecisionMakerFinder

finder = DecisionMakerFinder()

test_cases = [
    {
        "business_name": "Greensprings School",
        "website": "www.greenspringsschool.com",
        "city": "Lekki Lagos Nigeria",
        "niche": "private_school",
    },
    {
        "business_name": "Templars Law Firm",
        "website": "www.templars-law.com",
        "city": "Victoria Island Lagos Nigeria",
        "niche": "law_firm",
    },
    {
        "business_name": "Reddington Hospital",
        "website": "www.reddingtonhospital.com",
        "city": "Victoria Island Lagos Nigeria",
        "niche": "private_hospital",
    },
]

print("=" * 60)
print("Decision Maker Finder Test")
print("=" * 60)

for case in test_cases:
    print(f"\nSearching: {case['business_name']}")
    print("-" * 40)
    result = finder.find(**case)
    for k, v in result.items():
        if v:
            print(f"  {k:30s}: {v}")

print("\n[DONE]")