"""
CQC API Diagnostic -- run this to see raw API responses
"""
import os
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
key = os.getenv("CQC_API_KEY", "")
base = "https://api.service.cqc.org.uk/public/v1"
headers = {"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"}

print("=== CQC API Diagnostic ===")
print(f"Key loaded: {bool(key)} | Length: {len(key)}")
print()

# Test 1: Call locations with careHome filter
url1 = f"{base}/locations?careHome=Y&page=1&perPage=5"
print(f"1. GET {url1}")
try:
    r = requests.get(url1, headers=headers, timeout=30)
    print(f"   Status: {r.status_code}")
    print(f"   Content-Type: {r.headers.get('content-type', 'N/A')}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Keys: {list(data.keys())}")
        print(f"   Total: {data.get('total', 'N/A')}")
        locs = data.get('locations', [])
        print(f"   Locations count: {len(locs)}")
        if locs:
            print(f"   First location keys: {list(locs[0].keys())}")
            print(f"   First location name: {locs[0].get('name', 'N/A')}")
            print(f"   First location ID: {locs[0].get('locationId', 'N/A')}")
    else:
        print(f"   Response text (first 500 chars): {r.text[:500]}")
except Exception as e:
    print(f"   ERROR: {e}")

print()

# Test 2: Call without careHome filter
url2 = f"{base}/locations?page=1&perPage=5"
print(f"2. GET {url2}")
try:
    r = requests.get(url2, headers=headers, timeout=30)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Total: {data.get('total', 'N/A')}")
        print(f"   Locations count: {len(data.get('locations', []))}")
    else:
        print(f"   Response: {r.text[:500]}")
except Exception as e:
    print(f"   ERROR: {e}")