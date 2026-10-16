with open('engine/b2bhint_scraper.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace the fallback block at the bottom of scrape_b2bhint
old_fb = """    if not leads:
        print(f"[i] B2BHint query returned 0 cards — switching to autonomous Registry/Maps fallback for '{keyword}' ({location})!")
        jurisdiction = detect_jurisdiction(country, location)
        
        # Try Companies House UK first if in UK
        if jurisdiction == "gb":
            try:
                from engine.companies_house_scraper import scrape_companies_house
                ch_leads = scrape_companies_house(keyword=keyword, location=location, max_results=max_results, get_details=True, get_officers=True) or []
                for ld in ch_leads:
                    ld["source"] = "b2bhint_ch_fallback"
                    leads.append(ld)
            except Exception as e:
                print(f"[!] Companies House fallback error: {e}")

        # Try Google Maps scraper if still 0
        if not leads:
            try:
                from engine.google_maps_scraper import scrape_google_maps
                gm_leads = scrape_google_maps(keyword=keyword, location=location, max_results=max_results) or []
                for ld in gm_leads:
                    ld["source"] = "b2bhint_maps_fallback"
                    leads.append(ld)
            except Exception as e:
                print(f"[!] Google Maps fallback error: {e}")"""

new_fb = """    if not leads:
        print(f"[i] B2BHint query returned 0 cards — switching to autonomous Registry/Maps fallback for '{keyword}' ({location})!")
        jurisdiction = detect_jurisdiction(country, location)
        
        def _run_async_safe(res):
            import asyncio, inspect
            if inspect.iscoroutine(res):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                            return ex.submit(lambda: asyncio.run(res)).result()
                    else:
                        return loop.run_until_complete(res)
                except Exception:
                    return asyncio.run(res)
            return res

        # Try Companies House UK first if in UK
        if jurisdiction == "gb":
            try:
                from engine.companies_house_scraper import scrape_companies_house
                res = scrape_companies_house(keyword=keyword, location=location, max_results=max_results, get_details=True, get_officers=True, active_only=True)
                ch_leads = _run_async_safe(res) or []
                for ld in ch_leads:
                    ld["source"] = "b2bhint_ch_fallback"
                    leads.append(ld)
            except Exception as e:
                print(f"[!] Companies House fallback error: {e}")

        # Try Google Maps scraper if still 0
        if not leads:
            try:
                try:
                    from engine.scraper import scrape_google_maps
                except ImportError:
                    from engine.google_maps_scraper import scrape_google_maps
                res = scrape_google_maps(keyword=keyword, location=location, max_results=max_results)
                gm_leads = _run_async_safe(res) or []
                for ld in gm_leads:
                    ld["source"] = "b2bhint_maps_fallback"
                    leads.append(ld)
            except Exception as e:
                print(f"[!] Google Maps fallback error: {e}")"""

if old_fb in text:
    text = text.replace(old_fb, new_fb)
    with open('engine/b2bhint_scraper.py', 'w', encoding='utf-8') as out:
        out.write(text)
    print("Patched engine/b2bhint_scraper.py cleanly with _run_async_safe!")
else:
    print("Could not exact match fallback block, doing string replacement...")
