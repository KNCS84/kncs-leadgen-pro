# test_companies_house.py
import os
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")

if not API_KEY:
    print("❌ No API key found in .env file")
    exit(1)

print(f"✅ API Key found: {API_KEY[:8]}...")

# Test API call
url = "https://api.company-information.service.gov.uk/search/companies"
params = {"q": "restaurant", "items_per_page": 5}

print(f"\n🔍 Testing Companies House API...")
print(f"   Endpoint: {url}")
print(f"   Search query: restaurant")

response = requests.get(
    url,
    auth=(API_KEY, ""),
    params=params,
    timeout=30,
)

print(f"\n📡 Response Status Code: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    total = data.get("total_results", 0)
    items = data.get("items", [])
    
    print(f"\n✅ SUCCESS: API is working!")
    print(f"   Total restaurants in UK: {total:,}")
    print(f"\n   First 5 companies:")
    
    for i, company in enumerate(items, 1):
        name = company.get("title", "Unknown")
        status = company.get("company_status", "Unknown")
        address = company.get("address_snippet", "No address")
        company_number = company.get("company_number", "N/A")
        
        print(f"\n   {i}. {name}")
        print(f"      Company Number: {company_number}")
        print(f"      Status: {status}")
        print(f"      Address: {address}")
    
    print(f"\n{'='*60}")
    print(f"🎉 Companies House API is ready to use!")
    print(f"{'='*60}")
        
elif response.status_code == 401:
    print("\n❌ ERROR: Invalid API key")
    print("   Your API key is not authorized")
    print("   Check: COMPANIES_HOUSE_API_KEY in .env file")
    print(f"   Current key starts with: {API_KEY[:8]}...")
    
elif response.status_code == 429:
    print("\n⚠️ ERROR: Rate limit exceeded")
    print("   You've made too many requests")
    print("   Wait 5 minutes and try again")
    
else:
    print(f"\n❌ Unexpected Error: {response.status_code}")
    print(f"   Response: {response.text[:300]}")