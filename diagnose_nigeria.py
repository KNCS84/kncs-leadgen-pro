# diagnose_nigeria.py
# Run with: python diagnose_nigeria.py

import inspect
import json
import requests
import time

print("=" * 60)
print("NIGERIA GOOGLE MAPS CLIENT DIAGNOSIS")
print("=" * 60)

# ── Test 1: Import ────────────────────────────────────────────
try:
    from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient
    print("\n[1] Import: OK")
except Exception as e:
    print(f"\n[1] Import FAILED: {e}")
    exit()

# ── Test 2: City and niche keys ───────────────────────────────
c = NigeriaGoogleMapsClient()
print(f"\n[2] City keys available: {list(c.NIGERIAN_CITIES.keys())}")
print(f"\n[3] Niche keys available: {list(c.NICHE_QUERIES.keys())}")
print(f"\n[4] lagos_lekki resolves to: {c.NIGERIAN_CITIES.get('lagos_lekki')}")
print(f"\n[5] private_school queries: {c.NICHE_QUERIES.get('private_school')}")

# ── Test 3: Print full search method source ───────────────────
print("\n[6] FULL search_businesses SOURCE:")
print("-" * 40)
print(inspect.getsource(NigeriaGoogleMapsClient.search_businesses))
print("-" * 40)

# ── Test 4: Print all method names ───────────────────────────
methods = [m for m in dir(NigeriaGoogleMapsClient)
           if not m.startswith("__")]
print(f"\n[7] All methods: {methods}")

# ── Test 5: Print full class source (first 5000 chars) ────────
print("\n[8] FULL CLASS SOURCE (first 5000 chars):")
print("-" * 40)
src = inspect.getsource(NigeriaGoogleMapsClient)
print(src[:5000])
print("-" * 40)

# ── Test 6: Try a live search with verbose error ──────────────
print("\n[9] ATTEMPTING LIVE SEARCH...")
try:
    results = c.search_businesses(
        niche="private_school",
        city_key="lagos_lekki",
        max_results=3,
    )
    print(f"    Result count: {len(results)}")
    if results:
        print(f"    First result: {json.dumps(results[0], indent=4)}")
    else:
        print("    EMPTY LIST RETURNED")
except Exception as e:
    print(f"    SEARCH EXCEPTION: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)