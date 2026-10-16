with open('engine/job_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Make create_job, update_job_progress, and complete_job dual-write to both column aliases
old_create = """def create_job(keyword: str, city: str, source: str, total_target: int = 0) -> Optional[int]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            \"\"\"
            INSERT INTO scrape_jobs (
                keyword, location, source, status, progress, leads_found, leads_inserted,
                leads_skipped, leads_updated, total_target, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            \"\"\",
            (keyword, location, source, STATUS_RUNNING, 0, 0, 0, 0, 0, total_target, datetime.now(timezone.utc).isoformat())
        )"""

new_create = """def create_job(keyword: str, city: str, source: str, total_target: int = 0, country: str = "United Kingdom") -> Optional[int]:
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ts = time.time()
        cur.execute(
            \"\"\"
            INSERT INTO scrape_jobs (
                keyword, city, location, country, source, status, progress, progress_pct,
                leads_found, inserted, leads_inserted, skipped, leads_skipped, updated, leads_updated,
                message, current_lead, total_target, started_at, started_at_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            \"\"\",
            (keyword, city, city, country, source, STATUS_RUNNING, 0, 0.0, 0, 0, 0, 0, 0, 0, 0, "Starting...", "Starting...", total_target, now_iso, now_ts)
        )"""

if old_create in text:
    text = text.replace(old_create, new_create, 1)
    with open('engine/job_manager.py', 'w', encoding='utf-8') as out:
        out.write(text)
    print("Patched create_job cleanly to dual-write v10.10!")
else:
    print("Could not match old create_job exactly.")

