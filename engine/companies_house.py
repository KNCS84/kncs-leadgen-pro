"""
Companies House Integration Module (Chapter 6.7 of Master Audit).
Provides lookup_company and test_companies_house.
"""
from typing import Optional, Dict, Any, List
from engine.companies_house_scraper import (
    search_companies,
    get_company_profile,
    get_company_officers,
    scrape_companies_house,
    build_linkedin_url,
    has_api_key,
)


def lookup_company(company_name: str, postcode: Optional[str] = None) -> Dict[str, Any]:
    """
    Lookup company in UK Companies House and return structured profile & officers.
    Per Chapter 6.7 contract:
      found, company_number, company_status, incorporated_date, registered_address,
      primary_name, primary_role, primary_linkedin, all_directors, all_directors_str
    """
    clean_name = (company_name or "").strip()
    if not clean_name:
        return {"found": False, "company_number": "", "company_name": ""}

    results = search_companies(clean_name, max_results=5)
    if not results:
        return {"found": False, "company_number": "", "company_name": clean_name}

    # Match by postcode if provided, otherwise top result
    best = results[0]
    if postcode and postcode.strip():
        pc_clean = postcode.strip().upper().replace(" ", "")
        for r in results:
            addr = (r.get("address") or "").upper().replace(" ", "")
            if pc_clean in addr:
                best = r
                break

    company_number = best.get("company_number", "")
    profile = get_company_profile(company_number) if company_number else {}
    officers = get_company_officers(company_number) if company_number else []

    primary_name = ""
    primary_role = ""
    primary_linkedin = ""
    all_names = []

    for off in officers:
        name = off.get("name", "")
        role = off.get("officer_role", "")
        if name:
            all_names.append(f"{name} ({role})" if role else name)
            if not primary_name and "director" in role.lower() or "active" in str(off.get("resigned_on", "")).lower():
                primary_name = name
                primary_role = role

    if not primary_name and officers:
        primary_name = officers[0].get("name", "")
        primary_role = officers[0].get("officer_role", "")

    if primary_name:
        try:
            primary_linkedin = build_linkedin_url(f"{primary_name} {clean_name}")
        except TypeError:
            primary_linkedin = build_linkedin_url(clean_name)

    return {
        "found": True,
        "company_number": company_number,
        "company_name": best.get("title") or clean_name,
        "company_status": profile.get("company_status") or best.get("company_status", "active"),
        "incorporated_date": profile.get("date_of_creation", ""),
        "registered_address": profile.get("registered_office_address") or best.get("address", ""),
        "primary_name": primary_name,
        "primary_role": primary_role,
        "primary_linkedin": primary_linkedin,
        "all_directors": all_names,
        "all_directors_str": ", ".join(all_names),
    }


def test_companies_house(company_name: str = "Barchester Healthcare", postcode: Optional[str] = None) -> bool:
    """Diagnostic runner for Companies House API."""
    res = lookup_company(company_name, postcode)
    if res.get("found"):
        print(f"✅ Found {res.get('company_name')} (#{res.get('company_number')}) - DM: {res.get('primary_name')}")
        return True
    else:
        print(f"❌ Company not found: {company_name}")
        return False
