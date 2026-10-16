import json 
out = open('loc_sample.txt', 'w', encoding='utf-8') 
data = json.load(open('engine/cqc_location_cache.json', encoding='utf-8')) 
locs = data.get('locations', []) 
out.write('TOTAL LOCATIONS: ' + str(len(locs)) + '\n\n') 
out.write('FIRST 3 RECORDS:\n') 
for loc in locs[:3]: out.write(str(loc) + '\n\n') 
out.close() 
print('DONE') 
