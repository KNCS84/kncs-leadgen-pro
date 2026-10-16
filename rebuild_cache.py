import logging
from engine.cqc_client import refresh_cqc_cache
logging.basicConfig(level=logging.INFO)
print("Starting...")
total, care = refresh_cqc_cache()
print(f"Done: {total} locations, {care} care homes")
