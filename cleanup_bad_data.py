from database import get_connection

conn = get_connection()
cur  = conn.cursor()

# Clear wheree.com directory websites and constructed emails
cur.execute("UPDATE leads SET website='', email='' WHERE website LIKE '%wheree.com%'")
print('wheree.com cleared:', cur.rowcount)

# Clear placeholder email
cur.execute("UPDATE leads SET email='' WHERE email='myname@email.com'")
print('placeholder email cleared:', cur.rowcount)

# Clear Australian domain for UK business
cur.execute("UPDATE leads SET website='', email='' WHERE website LIKE '%.com.au'")
print('.com.au cleared:', cur.rowcount)

# Clear generic shared domains assigned to wrong businesses
cur.execute("UPDATE leads SET website='', email='' WHERE website='https://circlefinancial.co.uk' AND business_name NOT LIKE '%circle%'")
print('circlefinancial false positives cleared:', cur.rowcount)

cur.execute("UPDATE leads SET website='', email='' WHERE website='https://godivafinancialplanning.co.uk' AND business_name NOT LIKE '%godiva%'")
print('godivafinancialplanning false positives cleared:', cur.rowcount)

cur.execute("UPDATE leads SET website='', email='' WHERE website='https://financialadvisers.co.uk'")
print('financialadvisers directory cleared:', cur.rowcount)

cur.execute("UPDATE leads SET website='', email='' WHERE website LIKE '%facesconsent.com%'")
print('facesconsent booking platform cleared:', cur.rowcount)

# Clear constructed emails from wrong domains
cur.execute("UPDATE leads SET email='' WHERE email LIKE '%@oldfieldadvisory.com' AND business_name NOT LIKE '%oldfield%'")
print('oldfieldadvisory wrong email cleared:', cur.rowcount)

cur.execute("UPDATE leads SET email='' WHERE email LIKE '%@hhclinics.co.uk' AND business_name NOT LIKE '%hh%'")
print('hhclinics wrong email cleared:', cur.rowcount)

cur.execute("UPDATE leads SET email='' WHERE email LIKE '%auxilium.limited%'")
print('company-name-as-email cleared:', cur.rowcount)

conn.commit()
conn.close()
print('Done — all bad data cleared')