"""
Debug script for CQC client — run this to see exactly where it fails.
"""
import os
import sys

print("=== CQC Client Debug ===")
print("Python:", sys.executable)
print("CWD:", os.getcwd())

# 1. Check .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
key = os.getenv("CQC_API_KEY", "")
print("\n1. API Key loaded:", bool(key), "| Length:", len(key))

# 2. Check DDGS
try:
    from duckduckgo_search import DDGS
    print("2. DDGS available: YES")

    # Try a live search
    query = 'site:cqc.org.uk/location "Ashby Lodge Care Home" Nuneaton'
    print("   Searching:", query)
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=3))
    print("   Results found:", len(results))
    for i, r in enumerate(results):
        print(f"   [{i}] {r.get('title','')} -> {r.get('href','')}")
except Exception as e:
    print("2. DDGS available: NO | Error:", e)

# 3. Check CQC website search
try:
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import quote_plus

    search_url = "https://www.cqc.org.uk/search?search=" + quote_plus("Ashby Lodge Care Home Nuneaton") + "&ac=1"
    print("\n3. CQC website search:", search_url)
    resp = requests.get(search_url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
    print("   Status:", resp.status_code)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = [a["href"] for a in soup.find_all("a", href=True) if "/location/" in a["href"]]
    print("   Location links found:", len(links))
    for href in links[:3]:
        print("   -", href)
except Exception as e:
    print("3. CQC website search FAILED:", e)

# 4. Check old public API
try:
    url = "https://api.cqc.org.uk/public/v1/locations?careHome=Y&perPage=5&partnerCode=KNCS"
    print("\n4. Old public API test:", url)
    resp = requests.get(url, timeout=15)
    print("   Status:", resp.status_code)
    if resp.status_code == 200:
        data = resp.json()
        print("   Total locations:", data.get("total", "N/A"))
        print("   First location name:", data.get("locations", [{}])[0].get("name", "N/A"))
except Exception as e:
    print("4. Old public API FAILED:", e)

# 5. Run full lookup
print("\n5. Running full lookup...")
from engine.cqc_client import lookup_cqc_location
result = lookup_cqc_location("Ashby Lodge Care Home", "Nuneaton")
print("   Result:", result)