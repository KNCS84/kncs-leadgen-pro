# test_nigeria.py
# Save this file in C:\Users\Onyebuchi Amechi\leadgen-pro\

print("Testing Nigeria imports...")

try:
    from engine.nigeria.cac_client import lookup_nigerian_directors
    print("  cac_client             OK")
except Exception as e:
    print(f"  cac_client             FAILED: {e}")

try:
    from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient
    print("  google_maps_client     OK")
except Exception as e:
    print(f"  google_maps_client     FAILED: {e}")

try:
    from engine.nigeria.instagram_finder import InstagramFinder
    print("  instagram_finder       OK")
except Exception as e:
    print(f"  instagram_finder       FAILED: {e}")

try:
    from engine.nigeria.whatsapp_verifier import WhatsAppVerifier
    print("  whatsapp_verifier      OK")
except Exception as e:
    print(f"  whatsapp_verifier      FAILED: {e}")

try:
    from engine.nigeria.nigeria_enricher import NigeriaEnricher
    print("  nigeria_enricher       OK")
except Exception as e:
    print(f"  nigeria_enricher       FAILED: {e}")

try:
    from pages.nigeria_dashboard import main
    print("  nigeria_dashboard      OK")
except Exception as e:
    print(f"  nigeria_dashboard      FAILED: {e}")

print("")
print("Test complete.")
print("Any line showing FAILED needs to be fixed before running the app.")