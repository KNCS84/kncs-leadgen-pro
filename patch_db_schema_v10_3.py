with open('database.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace _ensure_universal_columns with comprehensive multi-table schema auto-heal
start = text.find('_SCHEMA_CHECKED = False')
end = text.find('def get_connection() -> sqlite3.Connection:')

if start != -1 and end != -1:
    old_schema = text[start:end]
    new_schema = """_SCHEMA_CHECKED = False

def _ensure_universal_columns(conn):
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    try:
        cur = conn.cursor()
        
        # 1. Check and heal 'leads' table
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(leads)")
            cols = {row[1] for row in cur.fetchall()}
            needed_leads = [
                ("company_number", "TEXT"),
                ("company_registration_number", "TEXT"),
                ("director_name", "TEXT"),
                ("decision_maker_name", "TEXT"),
                ("vertical", "TEXT"),
                ("bed_count", "INTEGER"),
                ("registration_date", "TEXT"),
                ("cqc_rating", "TEXT"),
                ("cqc_location_id", "TEXT"),
                ("cqc_url", "TEXT")
            ]
            for c_name, c_type in needed_leads:
                if c_name not in cols:
                    cur.execute(f"ALTER TABLE leads ADD COLUMN {c_name} {c_type}")
            
            # Sync alias fields
            cur.execute("UPDATE leads SET company_number = COALESCE(NULLIF(company_number, ''), company_registration_number) WHERE COALESCE(company_number, '') = '' AND COALESCE(company_registration_number, '') != ''")
            cur.execute("UPDATE leads SET company_registration_number = COALESCE(NULLIF(company_registration_number, ''), company_number) WHERE COALESCE(company_registration_number, '') = '' AND COALESCE(company_number, '') != ''")
            cur.execute("UPDATE leads SET director_name = COALESCE(NULLIF(director_name, ''), decision_maker_name) WHERE COALESCE(director_name, '') = '' AND COALESCE(decision_maker_name, '') != ''")
            cur.execute("UPDATE leads SET decision_maker_name = COALESCE(NULLIF(decision_maker_name, ''), director_name) WHERE COALESCE(decision_maker_name, '') != '' AND COALESCE(director_name, '') != ''")
            cur.execute("UPDATE leads SET vertical = 'care' WHERE (vertical IS NULL OR vertical = '') AND (industry LIKE '%care%' OR industry LIKE '%nursing%' OR industry LIKE '%residential%')")

        # 2. Check and heal 'care_home_opportunities' table
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='care_home_opportunities'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(care_home_opportunities)")
            cols_opp = {row[1] for row in cur.fetchall()}
            needed_opp = [
                ("opportunity_type", "TEXT"),
                ("urgency_level", "TEXT"),
                ("opportunity_score", "REAL"),
                ("urgency_score", "REAL")
            ]
            for c_name, c_type in needed_opp:
                if c_name not in cols_opp:
                    cur.execute(f"ALTER TABLE care_home_opportunities ADD COLUMN {c_name} {c_type}")

        # 3. Check and heal 'care_operator_groups' table
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='care_operator_groups'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(care_operator_groups)")
            cols_grp = {row[1] for row in cur.fetchall()}
            needed_grp = [
                ("director_name", "TEXT"),
                ("director_names", "TEXT"),
                ("group_type", "TEXT"),
                ("postcode_area", "TEXT"),
                ("detected_at", "TEXT"),
                ("company_numbers", "TEXT"),
                ("site_count", "INTEGER")
            ]
            for c_name, c_type in needed_grp:
                if c_name not in cols_grp:
                    cur.execute(f"ALTER TABLE care_operator_groups ADD COLUMN {c_name} {c_type}")
            
            cur.execute("UPDATE care_operator_groups SET director_name = COALESCE(NULLIF(director_name, ''), director_names) WHERE COALESCE(director_name, '') = '' AND COALESCE(director_names, '') != ''")
            cur.execute("UPDATE care_operator_groups SET director_names = COALESCE(NULLIF(director_names, ''), director_name) WHERE COALESCE(director_names, '') = '' AND COALESCE(director_name, '') != ''")

        conn.commit()
        _SCHEMA_CHECKED = True
    except Exception as exc:
        pass

"""
    text = text[:start] + new_schema + text[end:]
    with open('database.py', 'w', encoding='utf-8') as out:
        out.write(text)
    print("Upgraded database.py cleanly with multi-table v10.3 schema auto-heal!")
