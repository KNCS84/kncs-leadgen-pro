# engine/country_router.py
# KNCS LeadGen Pro — v9.7 PREMIUM
# Extracted from router.py (they were incorrectly merged into one file)
# 
# Fixes applied:
#   🔴 Bug #1:  Separated from router.py — was never a standalone file
#   🟡 Bug #10: DIAL_CODE_TO_COUNTRY — duplicate key "1" fixed (US vs Canada)
#   🟡 Bug #11: tuple[str,str] → Tuple[str,str] (Python 3.8 compatible)
#   🟡 Bug #12: country.title() → _title_country() handles UK/UAE acronyms
#   🟡 Bug #16: SOURCE_HINTS narrowed — only matches against source field
#   🟢 Bug #17: market == "uk" or market == "europe" → market in {...}

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# ─────────────────────────────────────────────────────────
# PHONE / COUNTRY MAPS
# ─────────────────────────────────────────────────────────

# FIX #10: "1" appeared twice (US + Canada) — second overwrote first
# Canada shares +1 with US but differentiated by area code (beyond scope here)
# Using US as primary for "+1"; Canadian leads rely on city/source detection
DIAL_CODE_TO_COUNTRY: Dict[str, str] = {
    "234":  "Nigeria",
    "233":  "Ghana",
    "254":  "Kenya",
    "27":   "South Africa",
    "44":   "United Kingdom",
    "353":  "Ireland",
    "1":    "United States",      # FIX #10: Canada removed from "1" key
    "91":   "India",
    "880":  "Bangladesh",
    "92":   "Pakistan",
    "94":   "Sri Lanka",
    "62":   "Indonesia",
    "63":   "Philippines",
    "60":   "Malaysia",
    "65":   "Singapore",
    "66":   "Thailand",
    "84":   "Vietnam",
    "971":  "United Arab Emirates",
    "966":  "Saudi Arabia",
    "20":   "Egypt",
    "55":   "Brazil",
    "52":   "Mexico",
    "57":   "Colombia",
    "54":   "Argentina",
    "56":   "Chile",
    "61":   "Australia",
    "64":   "New Zealand",
    "49":   "Germany",
    "33":   "France",
    "39":   "Italy",
    "34":   "Spain",
    "31":   "Netherlands",
    "32":   "Belgium",
    "41":   "Switzerland",
    "43":   "Austria",
    "46":   "Sweden",
    "47":   "Norway",
    "45":   "Denmark",
    "358":  "Finland",
    "48":   "Poland",
}

NIGERIA_STYLE_COUNTRIES = {
    "nigeria", "ghana", "kenya", "south africa", "egypt",
    "india", "pakistan", "bangladesh", "sri lanka",
    "indonesia", "philippines", "malaysia", "singapore",
    "thailand", "vietnam", "uae", "united arab emirates",
    "saudi arabia", "brazil", "mexico", "colombia",
    "argentina", "chile",
}

UK_STYLE_COUNTRIES = {
    "united kingdom", "uk", "ireland", "germany", "france",
    "italy", "spain", "netherlands", "belgium", "switzerland",
    "austria", "sweden", "norway", "denmark", "finland", "poland",
}

MARKET_BUCKETS: Dict[str, str] = {
    "nigeria":              "nigeria",
    "ghana":                "africa",
    "kenya":                "africa",
    "south africa":         "africa",
    "egypt":                "africa",
    "united kingdom":       "uk",
    "uk":                   "uk",
    "ireland":              "uk",
    "united states":        "north_america",
    "canada":               "north_america",
    "australia":            "oceania",
    "new zealand":          "oceania",
    "india":                "asia",
    "pakistan":             "asia",
    "bangladesh":           "asia",
    "sri lanka":            "asia",
    "indonesia":            "asia",
    "philippines":          "asia",
    "malaysia":             "asia",
    "singapore":            "asia",
    "thailand":             "asia",
    "vietnam":              "asia",
    "uae":                  "middle_east",
    "united arab emirates": "middle_east",
    "saudi arabia":         "middle_east",
    "brazil":               "latam",
    "mexico":               "latam",
    "colombia":             "latam",
    "argentina":            "latam",
    "chile":                "latam",
    "germany":              "europe",
    "france":               "europe",
    "italy":                "europe",
    "spain":                "europe",
    "netherlands":          "europe",
    "belgium":              "europe",
    "switzerland":          "europe",
    "austria":              "europe",
    "sweden":               "europe",
    "norway":               "europe",
    "denmark":              "europe",
    "finland":              "europe",
    "poland":               "europe",
}

TIMEZONE_BY_COUNTRY: Dict[str, str] = {
    "nigeria":              "Africa/Lagos",
    "ghana":                "Africa/Accra",
    "kenya":                "Africa/Nairobi",
    "south africa":         "Africa/Johannesburg",
    "united kingdom":       "Europe/London",
    "uk":                   "Europe/London",
    "ireland":              "Europe/Dublin",
    "united states":        "America/New_York",
    "canada":               "America/Toronto",
    "india":                "Asia/Kolkata",
    "uae":                  "Asia/Dubai",
    "united arab emirates": "Asia/Dubai",
    "saudi arabia":         "Asia/Riyadh",
    "brazil":               "America/Sao_Paulo",
    "mexico":               "America/Mexico_City",
    "colombia":             "America/Bogota",
    "argentina":            "America/Argentina/Buenos_Aires",
    "chile":                "America/Santiago",
    "australia":            "Australia/Sydney",
    "new zealand":          "Pacific/Auckland",
    "indonesia":            "Asia/Jakarta",
    "philippines":          "Asia/Manila",
    "malaysia":             "Asia/Kuala_Lumpur",
    "singapore":            "Asia/Singapore",
    "germany":              "Europe/Berlin",
    "france":               "Europe/Paris",
    "egypt":                "Africa/Cairo",
    "pakistan":             "Asia/Karachi",
}

# FIX #16: SOURCE_HINTS only matched against source field
# Previously matched against ALL text — any lead named "Nigeria Fashion Ltd"
# operating in UK would get country = Nigeria. Now source-specific only.
SOURCE_HINTS: Dict[str, str] = {
    "companies_house":  "United Kingdom",
    "companies house":  "United Kingdom",
    "google maps uk":   "United Kingdom",
    "google_maps_uk":   "United Kingdom",
    "uk_yellowpages":   "United Kingdom",
    "nigeria_maps":     "Nigeria",
    "nairaland":        "Nigeria",
}

# Acronym countries that title() breaks
_ACRONYM_COUNTRIES = {
    "uae":  "UAE",
    "uk":   "UK",
    "usa":  "USA",
    "us":   "US",
}


# ─────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _title_country(country: str) -> str:
    """
    FIX #12: country.title() on 'uae' → 'Uae', 'uk' → 'Uk'.
    This helper preserves acronyms correctly.
    """
    low = country.strip().lower()
    if low in _ACRONYM_COUNTRIES:
        return _ACRONYM_COUNTRIES[low]
    return country.strip().title()


def _phone_country_code(phone: str) -> Tuple[str, str]:
    """
    Returns (dial_code, country) by longest-prefix match.
    FIX #11: tuple[str,str] → Tuple[str,str] (Python 3.8 compatible)
    """
    digits = _digits(phone)
    if not digits:
        return "", ""

    # Sort by code length descending — match longest prefix first
    codes = sorted(DIAL_CODE_TO_COUNTRY.keys(), key=len, reverse=True)
    for code in codes:
        if digits.startswith(code):
            return code, DIAL_CODE_TO_COUNTRY[code]
    return "", ""


def _detect_country_from_source(source: str) -> str:
    """
    FIX #16: Only match SOURCE_HINTS against the source field,
    not against business names, websites, or addresses.
    """
    if not source:
        return ""
    source_norm = _norm(source)
    for hint, country in SOURCE_HINTS.items():
        if hint in source_norm:
            return country
    return ""


def _detect_country_from_location(*parts: Any) -> str:
    """
    Detect country by scanning location fields only
    (location, city, address) — NOT source, NOT business name.
    """
    haystack = " | ".join(_norm(p) for p in parts if p)
    for country_name in DIAL_CODE_TO_COUNTRY.values():
        if _norm(country_name) in haystack:
            return country_name
    return ""


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def get_market_bucket(country: str) -> str:
    """Return market bucket for a country name."""
    country_n = _norm(country)
    return MARKET_BUCKETS.get(country_n, "other")


def get_timezone(country: str) -> str:
    """Return IANA timezone for a country name."""
    return TIMEZONE_BY_COUNTRY.get(_norm(country), "UTC")


def get_market_profile(country: str) -> dict:
    """
    Return channel preferences and tone for a given country.
    FIX #17: market == "uk" or market == "europe" → market in {...}
    """
    market = get_market_bucket(country)

    if market == "nigeria":
        return {
            "market": market,
            "preferred_channels": ["whatsapp", "call", "email"],
            "wa_primary": True,
            "tone": "friendly",
        }

    if market in {"africa", "latam", "asia", "middle_east"}:
        return {
            "market": market,
            "preferred_channels": ["whatsapp", "call", "email"],
            "wa_primary": True,
            "tone": "friendly",
        }

    if market in {"uk", "europe"}:                  # FIX #17
        return {
            "market": market,
            "preferred_channels": ["email", "call", "whatsapp_verified"],
            "wa_primary": False,
            "tone": "formal",
        }

    if market == "north_america":
        return {
            "market": market,
            "preferred_channels": ["email", "call"],
            "wa_primary": False,
            "tone": "direct",
        }

    return {
        "market": market,
        "preferred_channels": ["call", "email", "whatsapp_verified"],
        "wa_primary": False,
        "tone": "direct",
    }


def enrich_lead_with_country(lead: dict) -> dict:
    """
    Populates country, market, timezone and phone_country_code.
    Safe to call repeatedly — does not overwrite existing good data.
    
    FIX #12: Uses _title_country() instead of .title() for acronyms
    FIX #16: Source hints only checked against source field
    """
    lead = dict(lead or {})

    phone = lead.get("phone") or lead.get("phone_number") or ""
    country = _norm(lead.get("country") or "")
    market = _norm(lead.get("market") or "")
    timezone = lead.get("timezone") or ""
    country_confidence = _norm(lead.get("country_confidence") or "") or "low"
    phone_country_code = _digits(lead.get("phone_country_code") or "")

    # Step 1: Phone prefix detection
    if phone:
        code, detected_country = _phone_country_code(phone)
        if code and not phone_country_code:
            phone_country_code = code
        if detected_country and not country:
            country = _norm(detected_country)
            country_confidence = "high"

    # Step 2: Source field hints (FIX #16 — source only, not all fields)
    if not country:
        detected = _detect_country_from_source(lead.get("source") or "")
        if detected:
            country = _norm(detected)
            country_confidence = "high"

    # Step 3: Location fields (city, address, location — NOT business name)
    if not country:
        detected = _detect_country_from_location(
            lead.get("location"),
            lead.get("city"),
            lead.get("address"),
        )
        if detected:
            country = _norm(detected)
            country_confidence = "medium"

    # Step 4: Derive market and timezone
    if country and not market:
        market = get_market_bucket(country)

    if country and not timezone:
        timezone = get_timezone(country)

    # FIX #12: Use _title_country not raw .title()
    lead["country"] = _title_country(country) if country else ""
    lead["market"] = market
    lead["timezone"] = timezone
    lead["phone_country_code"] = phone_country_code
    lead["country_confidence"] = country_confidence

    return lead