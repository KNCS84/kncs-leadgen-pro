# find_prospects.py — Quick no-website prospect finder
# Run: python find_prospects.py

import csv
import json
import logging
from engine.yellowpages_scraper import scrape_yellowpages

logging.basicConfig(level=logging.INFO)


def find_no_website_leads(keyword: str, location: str, max_results: int = 50) -> list:
    """
    Scrapes YellowPages (US) or Yell (UK) and returns ONLY
    businesses with no website listed — your prospect list.
    """
    all_leads = scrape_yellowpages(keyword, location, max_results)
    no_website = [l for l in all_leads if not l.get("website")]

    def review_count(lead):
        try:
            notes = json.loads(lead.get("enrichment_notes") or "{}")
            return notes.get("yp_reviews", 0) or 0
        except Exception:
            return 0

    # Prioritize businesses with more reviews = proven demand
    no_website.sort(key=review_count, reverse=True)
    return no_website


def save_to_csv(leads: list, filename: str = "prospects.csv") -> None:
    if not leads:
        print("No leads to save.")
        return
    fields = ["business_name", "phone", "address", "postcode",
              "industry", "source", "enrichment_notes"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: lead.get(k, "") for k in fields})
    print(f"\n✅ Saved {len(leads)} prospects to {filename}")


if __name__ == "__main__":
    # ─── EDIT THESE 2 LINES PER SEARCH ───
    KEYWORD  = "plumber"
    LOCATION = "Austin, Texas"                  # US format: "City, State"
    # LOCATION = "Manchester, United Kingdom"   # UK format: "City, United Kingdom"

    MAX_RESULTS = 50   # scans this many, filters down to no-website ones

    print(f"Searching for '{KEYWORD}' in '{LOCATION}'...\n")
    leads = find_no_website_leads(KEYWORD, LOCATION, MAX_RESULTS)

    print(f"Found {len(leads)} businesses WITHOUT a website "
          f"(out of {MAX_RESULTS} scanned)\n")

    for i, lead in enumerate(leads[:10], 1):
        print(f"{i}. {lead['business_name']} | {lead['phone']} | {lead['address']}")

    save_to_csv(leads)