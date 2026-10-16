import traceback

print("Testing database import...")
try:
    import database
    print("database: OK")
except Exception as e:
    print("database FAILED:")
    traceback.print_exc()

print("Testing database_patch import...")
try:
    import database_patch
    print("database_patch: OK")
except Exception as e:
    print("database_patch FAILED:")
    traceback.print_exc()

print("Testing get_all_leads...")
try:
    from database import get_all_leads
    leads = get_all_leads(limit=5)
    print("get_all_leads: OK —", len(leads), "leads returned")
except Exception as e:
    print("get_all_leads FAILED:")
    traceback.print_exc()

print("Done.")