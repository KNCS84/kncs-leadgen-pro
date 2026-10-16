with open('engine/b2bhint_scraper.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Ensure int casting in build_search_urls and scrape_b2bhint
old_url = 'def build_search_urls(keyword: str, city: str = "", country: str = "GB", max_results: int = 50) -> List[str]:\n    kw_clean = re.sub(r"[^\\w\\s-]", "", keyword).strip().replace(" ", "+")'
new_url = 'def build_search_urls(keyword: str, city: str = "", country: str = "GB", max_results: int = 50) -> List[str]:\n    try: max_results = int(max_results) if max_results else 50\n    except Exception: max_results = 50\n    kw_clean = re.sub(r"[^\\w\\s-]", "", keyword).strip().replace(" ", "+")'

old_scrape = 'def scrape_b2bhint(keyword: str, location: str = "", source: str = "b2bhint", max_results: int = 50, max_pages: int = 10, force_restart: bool = False, country: str = "GB", **kwargs) -> List[Dict[str, Any]]:\n    """Master entry point called by `job_manager.py` (`v10.5`)."""\n    urls = build_search_urls(keyword, location, country, max_results)'
new_scrape = 'def scrape_b2bhint(keyword: str, location: str = "", source: str = "b2bhint", max_results: int = 50, max_pages: int = 10, force_restart: bool = False, country: str = "GB", **kwargs) -> List[Dict[str, Any]]:\n    """Master entry point called by `job_manager.py` (`v10.5`)."""\n    try: max_results = int(max_results) if max_results else 50\n    except Exception: max_results = 50\n    try: max_pages = int(max_pages) if max_pages else 10\n    except Exception: max_pages = 10\n    urls = build_search_urls(keyword, location, country, max_results)'

if old_url in text and old_scrape in text:
    text = text.replace(old_url, new_url, 1).replace(old_scrape, new_scrape, 1)
    with open('engine/b2bhint_scraper.py', 'w', encoding='utf-8') as out:
        out.write(text)
    print("Patched b2bhint_scraper.py cleanly with safe int() casting!")
else:
    print("Could not match exact strings, doing regex replacement...")
