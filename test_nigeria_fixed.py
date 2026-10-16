"""
test_nigeria_fixed.py
Run from project root:  python test_nigeria_fixed.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 60)
print("Nigeria Client — Fixed Version Test")
print("=" * 60)

# 1. Import
try:
    from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient
    print("[PASS] Import OK")
except Exception as e:
    print(f"[FAIL] Import failed: {e}")
    sys.exit(1)

client = NigeriaGoogleMapsClient()   # no API key

# 2. Test niche+city resolution
city = client.NIGERIAN_CITIES.get("lagos_lekki")
print(f"[INFO] lagos_lekki -> {city}")

# 3. Live search — should return results now
print("\n[TEST] Searching: private_school / lagos_lekki (max 5)")
results = client.search_businesses("private_school", "lagos_lekki", max_results=5)
print(f"[RESULT] Count: {len(results)}")

if results:
    print("\nFirst result:")
    for k, v in results[0].items():
        print(f"  {k:20s}: {v}")
else:
    print("[WARN] Still empty — check internet connection")

# 4. Test seed fallback directly
print("\n[TEST] Seed fallback for law_firm")
seeds = client._get_seed_data("law_firm", "Victoria Island Lagos Nigeria")
print(f"[RESULT] Seed count: {len(seeds)}")
for s in seeds:
    print(f"  -> {s['business_name']}")

print("\n[DONE]")