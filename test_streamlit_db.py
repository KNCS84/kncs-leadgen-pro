import traceback
import sys

print("Python:", sys.version)
print("Testing full leads page import chain...")

try:
    from database import get_all_leads, get_connection
    print("Step 1 OK: database imports")
except Exception as e:
    print("Step 1 FAILED: database")
    traceback.print_exc()
    sys.exit(1)

try:
    from database_patch import (
        update_lead_status,
        update_lead_email,
        update_lead_enrichment,
    )
    print("Step 2 OK: database_patch imports")
except Exception as e:
    print("Step 2 FAILED: database_patch")
    traceback.print_exc()
    sys.exit(1)

try:
    from engine.scorer import get_contact_completeness
    print("Step 3 OK: scorer")
except Exception as e:
    print("Step 3 FAILED (non-fatal):", e)

try:
    from engine.enricher import find_email_multi_source, enrich_lead
    print("Step 4 OK: enricher")
except Exception as e:
    print("Step 4 FAILED (non-fatal):", e)

try:
    from engine.job_manager import any_job_running
    print("Step 5 OK: job_manager")
except Exception as e:
    print("Step 5 FAILED (non-fatal):", e)

try:
    from engine.nigeria.google_maps_client import NigeriaGoogleMapsClient
    print("Step 6 OK: nigeria client")
except Exception as e:
    print("Step 6 FAILED (non-fatal):", e)

try:
    from engine.nigeria.nigeria_enricher import NigeriaEnricher
    print("Step 7 OK: nigeria enricher")
except Exception as e:
    print("Step 7 FAILED (non-fatal):", e)

try:
    from engine.nigeria.decision_maker_finder import DecisionMakerFinder
    print("Step 8 OK: decision maker finder")
except Exception as e:
    print("Step 8 FAILED (non-fatal):", e)

print("\nAll steps complete.")
print("_DB_AVAILABLE would be: True")