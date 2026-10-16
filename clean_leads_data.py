import sqlite3, re, os, sys
sys.path.insert(0, os.path.abspath("."))
from engine.scorer import score_lead

conn = sqlite3.connect(os.path.join("data", "leads.db"))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. Clean junk domains from the leads table
junk = ["wiktionary", "hotels.com", "uptodown", "blissrom", "ccm.net", "t.me", "wa.me", "cam.ac.uk", "theglobalheroes", "escuelaportal", "bolt.host"]
cur.execute("SELECT id, website FROM leads WHERE website IS NOT NULL AND website != ''")
cleaned_web = 0
for lid, web in cur.fetchall():
    if any(j in (web or "").lower() for j in junk):
        cur.execute("UPDATE leads SET website = '', has_website = 0, website_active = 0 WHERE id = ?", (lid,))
        cleaned_web += 1

# 2. Fix UK leads that were mistakenly assigned 'Benin City'
uk_cities = ["London", "Birmingham", "Manchester", "Leeds", "Glasgow", "Liverpool", "Newcastle", "Sheffield", "Bristol", "Belfast", "Edinburgh", "Brighton", "Bath", "Cardiff", "Nottingham", "Leicester", "Coventry", "Bradford", "Stoke-on-Trent", "Wolverhampton", "Plymouth", "Southampton", "Reading", "Derby", "Dudley", "Norwich", "Portsmouth", "Luton", "Preston", "Milton Keynes", "Aberdeen", "Norwich", "Bournemouth", "Swindon", "Swansea", "Southend-on-Sea", "Middlesbrough", "Sunderland", "Warrington", "Huddersfield", "Oxford", "Slough", "Poole", "York", "Cambridge", "Gloucester", "Watford", "Grimsby", "Eastbourne", "Morecambe", "Chippenham", "Corsham", "Abingdon", "Bawtry", "Enniskillen", "Bingham", "Salisbury", "Wembley", "Hove", "Altrincham", "Edgware", "Brighouse", "Dundee", "Formby", "Melton Mowbray"]
cur.execute("SELECT id, address FROM leads WHERE country = 'United Kingdom' AND city = 'Benin City'")
fixed_cities = 0
for lid, addr in cur.fetchall():
    for c in uk_cities:
        if re.search(r'\b' + re.escape(c) + r'\b', addr or "", re.I):
            cur.execute("UPDATE leads SET city = ? WHERE id = ?", (c, lid))
            fixed_cities += 1
            break
conn.commit()

# 3. Rescore all leads using the inverted opportunity model
cur.execute("SELECT * FROM leads")
rescored = 0
for r in cur.fetchall():
    ld = dict(r)
    res = score_lead(ld)
    cur.execute("UPDATE leads SET fit_score=?, lead_priority=?, opportunity_score=?, urgency_score=?, reachability_score=?, best_channel=?, score=? WHERE id=?", (res.get("fit_score",0), res.get("lead_priority","medium"), res.get("opportunity_score",0), res.get("urgency_score",0), res.get("reachability_score",0), res.get("best_channel","unknown"), res.get("fit_score",0), ld["id"]))
    rescored += 1
conn.commit()

print(f"\n[SUCCESS] Sanitized {cleaned_web} junk URLs, fixed {fixed_cities} UK cities, rescored {rescored} leads!")
cur.execute("SELECT lead_priority, COUNT(*) FROM leads GROUP BY lead_priority")
for p, c in cur.fetchall():
    print(f"  * {p.upper():8}: {c}")
conn.close()