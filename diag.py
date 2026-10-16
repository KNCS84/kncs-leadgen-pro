from engine.cqc_client import _get 
import json 
r = _get('/locations/1-10000302982') 
print(json.dumps(r, indent=2)) 
