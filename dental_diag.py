import json 
data = json.load(open('engine/cqc_location_cache.json', encoding='utf-8')) 
out = open('dental_diag.txt', 'w', encoding='utf-8') 
types = set(l.get('type','') for l in data['locations']) 
dental = [t for t in types if 'dent' in t.lower()] 
out.write('ALL TYPES:\n') 
for t in sorted(types): out.write('  ' + t + '\n') 
out.write('\nDENTAL TYPES:\n') 
for t in dental: out.write('  ' + t + '\n') 
out.close() 
print('DONE') 
