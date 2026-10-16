import requests
import re
import sys
sys.path.insert(0, '.')
from engine.enricher import get_headers, score_domain_relevance, is_bad_domain
from deep_research import _is_junk_website

query = "Nottingham Vascular Surgery Nottingham official site"
url = "https://html.duckduckgo.com/html/?q=" + query.replace(" ", "+")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

resp = requests.get(url, headers=headers, timeout=15)
print("Status:", resp.status_code)
print("Response length:", len(resp.text))
print()

pattern = r'https?://([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})'
raw_domains = re.findall(pattern, resp.text)

seen = set()
unique = []
for d in raw_domains:
    d = d.lower().replace("www.", "")
    if d not in seen:
        seen.add(d)
        unique.append(d)

print(f"Unique domains found: {len(unique)}")
print()

tokens = ["nottingham", "vascular", "surgery"]

print("=== ALL DOMAINS WITH FILTER REASON ===")
for d in unique:
    junk  = _is_junk_website("https://" + d)
    bad   = is_bad_domain(d, "")
    score = score_domain_relevance(d, "NOTTINGHAM VASCULAR SURGERY LTD")
    root  = d.split(".")[0]
    long_match = any(len(t) >= 5 and t in root for t in tokens)
    couk_match = d.endswith(".co.uk") and any(len(t) >= 4 and t in root for t in tokens)

    if junk or bad:
        reason = "FILTERED-junk/bad"
    elif score < 20:
        reason = f"FILTERED-score({score})"
    elif not long_match and not couk_match:
        reason = f"FILTERED-no-token score={score}"
    else:
        reason = f"*** PASSES score={score} ***"

    print(f"  {d[:55]:55s}  {reason}")

print()
print("=== CONTAINS nottingham/vascular/surgery ===")
for d in unique:
    if any(w in d for w in ["nottingham", "vascular", "surgery", "nvs"]):
        score = score_domain_relevance(d, "NOTTINGHAM VASCULAR SURGERY LTD")
        print(f"  {d}  score={score}")