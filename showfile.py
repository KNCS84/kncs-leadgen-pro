import sys 
f = open('engine/enricher.py', encoding='utf-8') 
out = open('enricher_full.txt', 'w', encoding='utf-8') 
lines = f.readlines() 
f.close() 
for i,l in enumerate(lines): 
    out.write(str(i+1) + ' ' + l) 
out.close() 
print('DONE') 
