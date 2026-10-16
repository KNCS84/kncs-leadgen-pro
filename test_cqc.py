# test_cqc.py — confirm dental gacServiceTypes value
import requests
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

key = os.getenv("CQC_API_KEY")
headers = {
    "Ocp-Apim-Subscription-Key": key,
    "Accept": "application/json",
}
base = "https://api.service.cqc.org.uk/public/v1"

# Fetch detail for the 3 dental practices found
dental_ids = [
    "1-10000697432",  # Vara Dental Practice
    "1-10007378777",  # Lewis Grove Dental Practice
    "1-10012016520",  # Andrew Brown Dental Practice
]

for loc_id in dental_ids:
    r = requests.get(
        base + "/locations/" + loc_id,
        headers=headers,
        timeout=15,
    )
    if r.status_code != 200:
        print(f"{loc_id} -> ERROR {r.status_code}")
        continue

    d = r.json()
    print("=" * 55)
    print("Name:", d.get("name"))
    print("type:", d.get("type"))
    print("careHome:", d.get("careHome"))
    print("inspectionDirectorate:", d.get("inspectionDirectorate"))
    print("gacServiceTypes:", d.get("gacServiceTypes"))
    print("inspectionCategories:", d.get("inspectionCategories"))
    print("regulatedActivities:", [
        a.get("name") for a in d.get("regulatedActivities", [])
    ])
    print("specialisms:", d.get("specialisms"))
    print("phone:", d.get("mainPhoneNumber", d.get("telephone", "")))
    print("contacts:", d.get("contacts", [])[:2])
    print()