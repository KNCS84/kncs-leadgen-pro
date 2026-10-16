import traceback

print("Testing config import...")
try:
    import config
    print("config: OK")
    print("All config attributes:")
    import inspect
    for name, val in inspect.getmembers(config):
        if not name.startswith('_'):
            print(f"  {name} = {val}")
except Exception as e:
    print("config FAILED:")
    traceback.print_exc()