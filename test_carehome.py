from dotenv import load_dotenv 
import os, requests 
load_dotenv() 
key = os.getenv('CQC_API_KEY') 
headers = {'Ocp-Apim-Subscription-Key': key} 
url = 'https://api.service.cqc.org.uk/public/v1/locations' 
params = {'page': 1, 'perPage': 1, 'careHome': 'Y'} 
r = requests.get(url, headers=headers, params=params) 
print('Status:', r.status_code) 
print('Total:', r.json().get('total', 'NOT FOUND')) 
print('Keys:', list(r.json().keys())) 
