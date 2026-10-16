import sqlite3

conn = sqlite3.connect('data/leads.db')
tables = ['care_home_opportunities', 'care_acquisition_signals', 'care_operator_groups',
          'dental_opportunities', 'dental_growth_signals', 'dental_review_analysis',
          'website_health_scans', 'digital_maturity_scores', 'cyber_risk_profiles',
          'technology_profiles', 'decision_makers']

for t in tables:
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({t})').fetchall()]
        print(f"{t}: {n} rows")
        print(f"  columns: {cols}\n")
    except sqlite3.OperationalError as e:
        print(f"{t}: ERROR - {e}\n")

conn.close()
