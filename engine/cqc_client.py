# engine/cqc_client.py — v10.1 (Upgraded from v9.22)
#
# CHANGES FROM v9.22 TO v10.1:
#   1. Kept 100% of existing v9.22 methods, caching (engine/cqc_location_cache.json),
#      and return structures (lookup_cqc_location, get_full_details_for_location, refresh_cqc_cache)
#      so existing dashboards (care_intelligence_dashboard.py) work without breaking.
#
#   2. Added Provider / Companies House Registration Number resolution:
#      In get_full_details_for_location() and verify_and_enrich_lead(), we now extract
#      detail.get("providerId") and query `/providers/{provider_id}`. This extracts the
#      exact legal `companyNumber` (e.g. 03153370 for Care UK) so companies_house.py NEVER
#      does a fuzzy name match against construction/property firms like Ambleside Ltd (03518282)!
#
#   3. Added Nominated Individual extraction from provider contacts (Senior corporate CEO/director).
#
#   4. Added verify_and_enrich_lead() bridge method for uk_care_enricher.py integration.
#
#   5. Added normalize_phone() integration with database.py v10.0 (`+44...`).

import json
import os
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from difflib import SequenceMatcher
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


logger = logging.getLogger(__name__)

# Try importing normalize_phone from database v10.0, fallback if testing standalone
try:
    from database import normalize_phone
except ImportError:
    def normalize_phone(phone: str) -> Optional[str]:
        if not phone:
            return None
        clean = re.sub(r"[^\d+]", "", phone)
        if clean.startswith("0") and len(clean) == 11:
            return "+44" + clean[1:]
        elif clean.startswith("+44"):
            return clean
        return clean if len(clean) >= 10 else None

# ── Configuration ──────────────────────────────────────────────────────────

CQC_BASE_URL = os.getenv(
    "CQC_BASE_URL",
    "https://api.service.cqc.org.uk/public/v1",
)
CQC_WEB_BASE = "https://www.cqc.org.uk"
CQC_API_KEY  = os.getenv("CQC_API_KEY", "")

CQC_CACHE_FILE = Path("engine/cqc_location_cache.json")

_LOOKUP_INTERVAL: float = 2.0
_BULK_INTERVAL:   float = 0.1

_last_request_at: float = 0.0

_cache_all_locations: Optional[List[Dict]] = None
_cache_care_homes:    Optional[List[Dict]] = None

_MIN_EXPECTED_LOCATIONS = 100_000


# ══════════════════════════════════════════════════════════════════════════
# HTTP LAYER
# ══════════════════════════════════════════════════════════════════════════

def _throttle(interval: float) -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_request_at = time.time()


def _get_headers() -> Dict[str, str]:
    h: Dict[str, str] = {
        "Accept":     "application/json",
        "User-Agent": "KNCS-LeadGen/10.1",
    }
    if CQC_API_KEY:
        h["Ocp-Apim-Subscription-Key"] = CQC_API_KEY
    return h


def _get(
    path:     str,
    params:   Optional[Dict] = None,
    interval: float = _LOOKUP_INTERVAL,
) -> Optional[Dict]:
    try:
        _throttle(interval)
        url  = f"{CQC_BASE_URL}{path}"
        resp = requests.get(
            url,
            headers=_get_headers(),
            params=params,
            timeout=20,
        )
        if resp.status_code == 200:
            return resp.json()
        try:
            preview = resp.text[:400] if resp.text else "(empty)"
        except Exception:
            preview = "(unreadable)"
        logger.warning(
            "CQC API %s -> HTTP %s | %s",
            path, resp.status_code, preview,
        )
        return None
    except Exception as exc:
        logger.warning("CQC _get(%s): %s", path, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════
# CACHE LAYER
# ══════════════════════════════════════════════════════════════════════════

def _load_cache() -> None:
    global _cache_all_locations, _cache_care_homes
    if _cache_all_locations is not None:
        return
    if not CQC_CACHE_FILE.exists():
        logger.warning(
            "CQC cache missing: %s  Run refresh_cqc_cache().",
            CQC_CACHE_FILE,
        )
        _cache_all_locations = []
        _cache_care_homes    = []
        return
    try:
        with open(CQC_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache_all_locations = data.get("locations",  [])
        _cache_care_homes    = data.get("care_homes", [])
        logger.info(
            "CQC cache loaded: %s locations, %s care homes",
            len(_cache_all_locations),
            len(_cache_care_homes),
        )
    except Exception as exc:
        logger.error("CQC cache load failed: %s", exc)
        _cache_all_locations = []
        _cache_care_homes    = []


def _reload_cache() -> None:
    global _cache_all_locations, _cache_care_homes
    _cache_all_locations = None
    _cache_care_homes    = None
    _load_cache()


def _care_homes() -> List[Dict]:
    _load_cache()
    return _cache_care_homes or []


def _all_locations() -> List[Dict]:
    _load_cache()
    return _cache_all_locations or []


# ══════════════════════════════════════════════════════════════════════════
# NAME MATCHING
# ══════════════════════════════════════════════════════════════════════════

_NOISE: set = {
    "care", "home", "lodge", "manor", "court", "hall",
    "residential", "nursing", "the", "house", "services",
    "group", "ltd", "limited", "and",
}


def _strip_noise(name: str) -> str:
    tokens  = name.lower().split()
    cleaned = [t for t in tokens if t not in _NOISE]
    return " ".join(cleaned) or name.lower()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_candidate(
    business_name: str,
    city:          str,
    candidate:     Dict,
) -> float:
    cand_name = candidate.get("locationName", "")
    cand_city = (
        candidate.get("postalAddressTownCity", "")
        or candidate.get("postalCode", "")
        or ""
    ).lower()
    s_clean = _strip_noise(business_name)
    c_clean = _strip_noise(cand_name)
    base    = _similarity(s_clean, c_clean)
    s_words = set(s_clean.split())
    c_words = set(c_clean.split())
    shared  = s_words & c_words
    word_b  = (
        0.2 * len(shared) / len(s_words)
    ) if s_words else 0.0
    city_b = (
        0.2 if city and cand_city
        and city.lower().strip() in cand_city
        else 0.0
    )
    return base + word_b + city_b


def _search_candidates(
    business_name: str,
    city:          str,
) -> List[Dict]:
    pool = _care_homes()
    if not pool:
        logger.warning(
            "_search_candidates: care homes cache empty."
        )
        return []
    s_clean = _strip_noise(business_name)
    s_words = set(s_clean.split())
    scored: List[Tuple[float, Dict]] = []
    for loc in pool:
        c_clean = _strip_noise(loc.get("locationName", ""))
        c_words = set(c_clean.split())
        if s_words and not (s_words & c_words):
            if _similarity(s_clean, c_clean) < 0.4:
                continue
        score = _score_candidate(business_name, city, loc)
        if score > 0.2:
            scored.append((score, loc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [loc for _, loc in scored[:10]]


# ══════════════════════════════════════════════════════════════════════════
# MANAGER NAME EXTRACTION
# ══════════════════════════════════════════════════════════════════════════

_MANAGER_ROLE_CODES = {"RM", "rm"}
_MANAGER_KEYWORDS   = {"registered manager", "manager"}

_NON_PERSON_WORDS = {
    "accommodation", "persons", "require", "nursing",
    "personal", "care", "service", "services", "treatment",
    "assessment", "support", "management", "community",
    "diagnostic", "screening", "surgical", "procedure",
    "transport", "maternity", "midwifery", "family",
    "planning", "recovery", "social", "mental", "health",
    "substance", "misuse", "learning", "disability",
}


def _is_person_name(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    v     = value.strip()
    parts = v.split()
    if len(v) > 50:
        return False
    if len(parts) < 2 or len(parts) > 3:
        return False
    v_lower = v.lower()
    for word in _NON_PERSON_WORDS:
        if word in v_lower:
            return False
    allowed_particles = {"de", "van", "von", "le", "la", "di", "da"}
    for part in parts:
        if len(part) > 20:
            return False
        if (len(part) > 2
                and part[0].islower()
                and part.lower() not in allowed_particles):
            return False
    return True


def _looks_like_manager_role(value: str) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 80:
        return False
    return any(kw in v.lower() for kw in _MANAGER_KEYWORDS)


def _is_manager_role(contact: Dict) -> bool:
    if not isinstance(contact, dict):
        return False
    for field in ("personRoles", "roles", "contactRoles"):
        role_list = contact.get(field, [])
        if not isinstance(role_list, list):
            continue
        for role in role_list:
            if isinstance(role, str):
                if _looks_like_manager_role(role):
                    return True
            elif isinstance(role, dict):
                code = (role.get("roleCode", "") or "").strip()
                name = (
                    role.get("roleName", "")
                    or role.get("name", "")
                    or ""
                ).lower()
                if code in _MANAGER_ROLE_CODES:
                    return True
                if any(kw in name for kw in _MANAGER_KEYWORDS):
                    return True
    for field in ("role", "jobTitle", "position"):
        if _looks_like_manager_role(contact.get(field, "")):
            return True
    return False


def _extract_name_parts(obj: Dict) -> str:
    if not isinstance(obj, dict):
        return ""
    for f_key, l_key in (
        ("personGivenName",  "personFamilyName"),
        ("firstName",        "familyName"),
        ("givenName",        "surname"),
        ("forename",         "surname"),
        ("given_name",       "family_name"),
    ):
        first = (obj.get(f_key, "") or "").strip()
        last  = (obj.get(l_key, "") or "").strip()
        if (first and last
                and len(first) >= 2
                and len(last)  >= 2):
            name = f"{first.title()} {last.title()}"
            if _is_person_name(name):
                return name
    for key in ("fullName", "personName", "contactName",
                "displayName"):
        full = (obj.get(key, "") or "").strip()
        if _is_person_name(full):
            return full.title()
    return ""


def _extract_manager_name(detail: Dict) -> str:
    if not detail or not isinstance(detail, dict):
        return ""

    if detail.get("registeredManagerAbsentDate", ""):
        logger.debug("_extract_manager_name: post vacant -> ''")
        return ""

    regulated = detail.get("regulatedActivities", [])
    if isinstance(regulated, list):
        for activity in regulated:
            if not isinstance(activity, dict):
                continue
            contacts = activity.get("contacts", [])
            if not isinstance(contacts, list):
                continue
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                if _is_manager_role(contact):
                    name = _extract_name_parts(contact)
                    if name:
                        logger.debug("manager S1: %s", name)
                        return name

    if isinstance(regulated, list):
        for activity in regulated:
            if not isinstance(activity, dict):
                continue
            contacts = activity.get("contacts", [])
            if not isinstance(contacts, list):
                continue
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                name = _extract_name_parts(contact)
                if name:
                    logger.debug("manager S2: %s", name)
                    return name

    if isinstance(regulated, list):
        for activity in regulated:
            if not isinstance(activity, dict):
                continue
            for key, value in activity.items():
                if key in ("name", "code", "contacts"):
                    continue
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    if _is_manager_role(item):
                        name = _extract_name_parts(item)
                        if name:
                            logger.debug("manager S3 (%s): %s", key, name)
                            return name

    top_contacts = detail.get("contacts", [])
    if isinstance(top_contacts, list):
        for contact in top_contacts:
            if not isinstance(contact, dict):
                continue
            if _is_manager_role(contact):
                name = _extract_name_parts(contact)
                if name:
                    logger.debug("manager S4: %s", name)
                    return name

    if isinstance(top_contacts, list) and 0 < len(top_contacts) <= 2:
        for contact in top_contacts:
            if not isinstance(contact, dict):
                continue
            name = _extract_name_parts(contact)
            if name:
                logger.debug("manager S5: %s", name)
                return name

    reg_mgr = detail.get("registeredManager", [])
    if isinstance(reg_mgr, list):
        for item in reg_mgr:
            if not isinstance(item, dict):
                continue
            name = _extract_name_parts(item)
            if name:
                logger.debug("manager S6: %s", name)
                return name

    mgr_str = (detail.get("registeredManagerName", "") or "").strip()
    if mgr_str and _is_person_name(mgr_str):
        logger.debug("manager S7: %s", mgr_str)
        return mgr_str.title()

    logger.debug("_extract_manager_name: no name found -> ''")
    return ""


def _extract_rating(detail: Dict) -> str:
    if not isinstance(detail, dict):
        return ""
    ratings = detail.get("currentRatings", {})
    if isinstance(ratings, dict):
        overall = ratings.get("overall", {})
        if isinstance(overall, dict):
            r = (overall.get("rating", "") or "").strip()
            if r:
                return r
    return (detail.get("overallRating", "") or "").strip()


def _build_address(detail: Dict) -> str:
    parts = [
        detail.get("postalAddressLine1",    "") or "",
        detail.get("postalAddressLine2",    "") or "",
        detail.get("postalAddressTownCity", "") or "",
        detail.get("postalCode",            "") or "",
    ]
    return ", ".join(p.strip() for p in parts if p.strip())


def _get_location_detail(location_id: str) -> Optional[Dict]:
    if not location_id:
        return None
    return _get(
        f"/locations/{location_id}",
        interval=_LOOKUP_INTERVAL,
    )


def _get_provider_detail(provider_id: str) -> Optional[Dict]:
    """Fetch provider legal details (Company Number & Nominated Individual) from CQC."""
    if not provider_id:
        return None
    return _get(
        f"/providers/{provider_id}",
        interval=_LOOKUP_INTERVAL,
    )


def _is_manager_post_vacant(detail: Dict) -> bool:
    return bool(detail.get("registeredManagerAbsentDate", ""))


# ══════════════════════════════════════════════════════════════════════════
# WEB SCRAPE FALLBACK
# ══════════════════════════════════════════════════════════════════════════

def _scrape_location_page(
    location_id: str,
    result:      Dict[str, Any],
) -> None:
    if not location_id:
        return
    try:
        url = f"{CQC_WEB_BASE}/location/{location_id}"
        _throttle(_LOOKUP_INTERVAL)
        resp = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code != 200:
            return
        text = resp.text
        name_patterns = [
            r"Registered\s+Manager\s*[:\-]?\s*([A-Z][a-zA-Z\s\'\-]{2,40})",
            r"Nominated\s+Individual\s*[:\-]?\s*([A-Z][a-zA-Z\s\'\-]{2,40})",
            r"Manager\s*[:\-]?\s*([A-Z][a-zA-Z\s\'\-]{2,40})",
        ]
        for pattern in name_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                raw = m.group(1).strip()
                if _is_person_name(raw):
                    result["registered_manager"] = raw
                    result["source"] = "cqc_website_scrape"
                    break
        if not result.get("phone"):
            pm = re.search(r"Telephone\s*[:\-]?\s*([0-9\s()\+]{8,20})", text, re.IGNORECASE)
            if pm:
                result["phone"] = pm.group(1).strip()
        if not result.get("cqc_rating"):
            rm = re.search(r"\b(Outstanding|Good|Requires\s+improvement|Inadequate)\b", text, re.IGNORECASE)
            if rm:
                result["cqc_rating"] = rm.group(1).strip()
    except Exception as exc:
        logger.debug("_scrape_location_page(%s): %s", location_id, exc)


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC — lookup_cqc_location (enricher.py)
# ══════════════════════════════════════════════════════════════════════════

def lookup_cqc_location(
    business_name: str,
    city:          str = "",
    address:       str = "",
) -> Dict[str, Any]:
    empty: Dict[str, Any] = {
        "registered_manager": "",
        "phone":              "",
        "address":            "",
        "cqc_rating":         "",
        "cqc_url":            "",
        "cqc_location_id":    "",
        "match_confidence":   "none",
        "source":             "",
        "provider_id":        "",
        "provider_name":      "",
        "company_number":     "",
    }
    if not business_name or not business_name.strip():
        return empty
    try:
        candidates = _search_candidates(business_name, city)
        if not candidates:
            return empty
        best:       Optional[Dict] = None
        best_score: float          = 0.0
        for c in candidates:
            s = _score_candidate(business_name, city, c)
            if s > best_score:
                best_score = s
                best       = c
        if not best or best_score < 0.3:
            return empty
        confidence = (
            "high"   if best_score >= 0.8 else
            "medium" if best_score >= 0.5 else
            "low"
        )
        location_id = best.get("locationId", "")
        cqc_url     = f"{CQC_WEB_BASE}/location/{location_id}" if location_id else ""
        result: Dict[str, Any] = {
            "registered_manager": "",
            "phone":              "",
            "address":            "",
            "cqc_rating":         "",
            "cqc_url":            cqc_url,
            "cqc_location_id":    location_id,
            "match_confidence":   confidence,
            "source":             "cqc_api",
            "provider_id":        "",
            "provider_name":      "",
            "company_number":     "",
        }
        detail = _get_location_detail(location_id)
        if detail:
            result["registered_manager"] = _extract_manager_name(detail)
            result["cqc_rating"] = _extract_rating(detail)
            raw_phone = str(detail.get("mainPhoneNumber", "") or "").strip()
            result["phone"] = normalize_phone(raw_phone) or raw_phone
            result["address"] = _build_address(detail)
            
            # Extract Provider detail to fix BUG-014 / BUG-016
            prov_id = detail.get("providerId")
            if prov_id:
                result["provider_id"] = prov_id
                prov_detail = _get_provider_detail(prov_id)
                if prov_detail:
                    result["provider_name"] = prov_detail.get("name", "")
                    result["company_number"] = prov_detail.get("companyNumber", "")

            logger.info(
                "lookup: '%s' -> '%s' | manager='%s' | rating='%s' | companyNumber='%s'",
                business_name,
                best.get("locationName", ""),
                result["registered_manager"] or "(not found)",
                result["cqc_rating"]        or "(not rated)",
                result["company_number"]    or "(no CH num)",
            )
            if not result["registered_manager"] and not _is_manager_post_vacant(detail) and location_id:
                _scrape_location_page(location_id, result)
        return result
    except Exception as exc:
        logger.error("lookup_cqc_location('%s'): %s", business_name, exc, exc_info=True)
        return empty


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC — Dashboard functions & Full Detail Enricher
# ══════════════════════════════════════════════════════════════════════════

def get_cache_stats() -> Dict[str, Any]:
    if not CQC_CACHE_FILE.exists():
        return {
            "exists": False, "total_locations": 0, "total_care_homes": 0,
            "downloaded_at": "", "file_size_mb": 0.0,
            "status": "Missing — click Refresh CQC Database", "complete": False,
        }
    try:
        file_mb = round(CQC_CACHE_FILE.stat().st_size / (1024 * 1024), 1)
    except Exception:
        file_mb = 0.0
    try:
        with open(CQC_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        total_loc = data.get("total_locations",  0)
        total_ch  = data.get("total_care_homes", 0)
        saved_at  = data.get("saved_at", "")
        complete  = total_loc >= _MIN_EXPECTED_LOCATIONS
        status    = ("Fresh ✅" if (saved_at and complete) else
                     "⚠️ Incomplete — click Refresh CQC Database" if saved_at else "Unknown")
        return {
            "exists": True, "total_locations": total_loc, "total_care_homes": total_ch,
            "downloaded_at": saved_at, "file_size_mb": file_mb, "status": status, "complete": complete,
        }
    except Exception as exc:
        logger.error("get_cache_stats: %s", exc)
        return {
            "exists": True, "total_locations": 0, "total_care_homes": 0,
            "downloaded_at": "", "file_size_mb": file_mb,
            "status": "Error reading cache", "complete": False,
        }


def get_care_homes_from_cache(postcode_prefix: str = "") -> List[Dict]:
    pool = _care_homes()
    if not pool:
        logger.warning("get_care_homes_from_cache: empty.")
        return []
    if not postcode_prefix:
        return pool
    prefix   = postcode_prefix.upper().strip()
    return [ch for ch in pool if (ch.get("postalCode", "") or "").upper().startswith(prefix)]


def get_manager_for_location(location_id: str) -> str:
    if not location_id:
        return ""
    try:
        detail = _get_location_detail(location_id)
        if not detail or _is_manager_post_vacant(detail):
            return ""
        name = _extract_manager_name(detail)
        if not name:
            scrape: Dict[str, Any] = {"registered_manager": "", "phone": "", "cqc_rating": "", "source": ""}
            _scrape_location_page(location_id, scrape)
            name = scrape.get("registered_manager", "")
        return name
    except Exception as exc:
        logger.warning("get_manager_for_location(%s): %s", location_id, exc)
        return ""


def get_full_details_for_location(location_id: str) -> Dict[str, Any]:
    """
    Fetch ALL enrichment data for one care home in a single API call (v10.1 Enhanced).
    Now also extracts provider_id, `/providers/{id}`, exact company_number (BUG-014 fix),
    and nominated_individual (Senior Corporate CEO/Director).
    """
    empty: Dict[str, Any] = {
        "manager_name": "", "phone": "", "rating": "", "address": "", "website": "",
        "provider_id": "", "provider_name": "", "company_number": "", "nominated_individual": ""
    }
    if not location_id:
        return empty

    try:
        detail = _get_location_detail(location_id)
        if not detail:
            logger.warning("get_full_details_for_location: no detail for %s", location_id)
            return empty

        manager = _extract_manager_name(detail)
        if not manager and not _is_manager_post_vacant(detail):
            scrape: Dict[str, Any] = {"registered_manager": "", "phone": "", "cqc_rating": "", "source": ""}
            _scrape_location_page(location_id, scrape)
            manager = scrape.get("registered_manager", "")

        raw_phone = str(detail.get("mainPhoneNumber", "") or "").strip()
        phone     = normalize_phone(raw_phone) or raw_phone
        rating    = _extract_rating(detail)
        address   = _build_address(detail)
        website   = str(detail.get("website", "") or "").strip()

        # v10.1 Provider & Companies House Number Resolution
        prov_id = detail.get("providerId")
        prov_name = ""
        comp_num = ""
        nom_ind = ""
        if prov_id:
            prov_detail = _get_provider_detail(prov_id)
            if prov_detail:
                prov_name = prov_detail.get("name", "")
                comp_num = prov_detail.get("companyNumber", "")
                for pc in prov_detail.get("contacts", []):
                    roles = pc.get("roles", [])
                    if "Nominated Individual" in roles or "Responsible Person" in roles:
                        nom_ind = f"{pc.get('personGivenName', '')} {pc.get('personFamilyName', '')}".strip()
                        if not manager:
                            manager = nom_ind
                        break

        logger.info(
            "get_full_details_for_location(%s): manager='%s' phone='%s' rating='%s' companyNumber='%s'",
            location_id, manager or "(none)", phone or "(none)", rating or "(none)", comp_num or "(none)"
        )

        return {
            "manager_name": manager,
            "phone":        phone,
            "rating":       rating,
            "address":      address,
            "website":      website,
            "provider_id":  prov_id or "",
            "provider_name": prov_name,
            "company_number": comp_num,
            "nominated_individual": nom_ind,
        }
    except Exception as exc:
        logger.warning("get_full_details_for_location(%s): %s", location_id, exc)
        return empty


def verify_and_enrich_lead(business_name: str, postal_code: Optional[str] = None, phone: Optional[str] = None) -> Dict[str, Any]:
    """Bridge method called by uk_care_enricher.py v10.1."""
    res = lookup_cqc_location(business_name, postal_code or "")
    if not res or not res.get("cqc_location_id"):
        return {"cqc_verified": False, "error": "No match found"}
        
    full = get_full_details_for_location(res["cqc_location_id"])
    return {
        "cqc_verified": True,
        "cqc_location_id": res["cqc_location_id"],
        "cqc_provider_id": full.get("provider_id"),
        "cqc_rating": full.get("rating"),
        "decision_maker_name": full.get("manager_name"),
        "decision_maker_title": "Registered Care Home Manager" if full.get("manager_name") else "",
        "nominated_individual": full.get("nominated_individual"),
        "company_registration_number": full.get("company_number"),
        "legal_provider_name": full.get("provider_name"),
        "verified_phone": full.get("phone") or phone,
        "verified_website": full.get("website"),
    }


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC — refresh_cqc_cache
# ══════════════════════════════════════════════════════════════════════════

def refresh_cqc_cache(progress_callback=None) -> Tuple[int, int]:
    try:
        logger.info("refresh_cqc_cache: starting")
        PER_PAGE = 1000
        PAGE_CAP = 200

        probe = _get("/locations", params={"page": 1, "perPage": 1}, interval=_BULK_INTERVAL)
        if probe is None:
            logger.error("refresh_cqc_cache: probe failed")
            return 0, 0

        total_count = probe.get("total", 0)
        logger.info("refresh_cqc_cache: %s total locations", total_count)

        all_locations: List[Dict] = []
        page = 1
        total_pages = min(max(1, (total_count + PER_PAGE - 1) // PER_PAGE), PAGE_CAP) if total_count else PAGE_CAP

        while page <= total_pages:
            data = _get("/locations", params={"page": page, "perPage": PER_PAGE}, interval=_BULK_INTERVAL)
            if data is None:
                logger.warning("refresh_cqc_cache: page %s failed", page)
                break
            locs = data.get("locations", [])
            if not locs:
                break
            if page == 1 and data.get("total"):
                total_count = data["total"]
                total_pages = min(max(1, (total_count + PER_PAGE - 1) // PER_PAGE), PAGE_CAP)
            all_locations.extend(locs)
            if progress_callback:
                try:
                    progress_callback(len(all_locations), total_count or len(all_locations))
                except Exception:
                    pass
            logger.info("refresh_cqc_cache: page %s/%s — %s", page, total_pages, len(all_locations))
            page += 1

        if not all_locations:
            logger.error("refresh_cqc_cache: no locations downloaded")
            return 0, 0

        complete = len(all_locations) >= _MIN_EXPECTED_LOCATIONS

        care_homes: List[Dict] = []
        ch_page = 1
        ch_probe = _get("/locations", params={"page": 1, "perPage": 1, "careHome": "Y"}, interval=_BULK_INTERVAL)
        if ch_probe and ch_probe.get("total"):
            ch_total = ch_probe["total"]
            ch_pages = min(max(1, (ch_total + PER_PAGE - 1) // PER_PAGE), 50)
        else:
            ch_total = 0
            ch_pages = 50

        while ch_page <= ch_pages:
            ch_data = _get("/locations", params={"page": ch_page, "perPage": PER_PAGE, "careHome": "Y"}, interval=_BULK_INTERVAL)
            if ch_data is None:
                break
            ch_locs = ch_data.get("locations", [])
            if not ch_locs:
                break
            care_homes.extend(ch_locs)
            logger.info("refresh_cqc_cache: care home page %s/%s — %s", ch_page, ch_pages, len(care_homes))
            ch_page += 1

        cache_data = {
            "saved_at":         datetime.now(timezone.utc).isoformat(),
            "total_locations":  len(all_locations),
            "total_care_homes": len(care_homes),
            "complete":         complete,
            "locations":        all_locations,
            "care_homes":       care_homes,
        }

        CQC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CQC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        logger.info("refresh_cqc_cache: saved %s locations (%s care homes)", len(all_locations), len(care_homes))
        _reload_cache()
        return len(all_locations), len(care_homes)

    except Exception as exc:
        logger.error("refresh_cqc_cache: %s", exc, exc_info=True)
        return 0, 0


# ══════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC
# ══════════════════════════════════════════════════════════════════════════

def test_cqc_connection() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print("\n── CQC API Connection Test (v10.1) ──────────────────")
    print(f"Base URL : {CQC_BASE_URL}")
    print(f"API Key  : {'SET (' + CQC_API_KEY[:6] + '...)' if CQC_API_KEY else 'NOT SET'}")
    cache_exists = CQC_CACHE_FILE.exists()
    print(f"Cache    : {CQC_CACHE_FILE} ({'exists' if cache_exists else 'MISSING'})")
    if cache_exists:
        stats = get_cache_stats()
        flag  = "✅" if stats["complete"] else "⚠️"
        print(f"Stats    : {stats['total_care_homes']:,} care homes / {stats['total_locations']:,} total ({flag}) — {stats['status']}")
    print()

    print("Test 1: Connectivity...")
    r1 = _get("/locations", params={"page": 1, "perPage": 1}, interval=0.0)
    print(f"  {'✅' if r1 else '❌'} Total: {r1.get('total','FAILED') if r1 else 'FAILED'}")

    print("Test 2: get_full_details_for_location()...")
    test_id = "1-126434017"  # Ashby Court — confirmed active
    details = get_full_details_for_location(test_id)
    ok = bool(details.get("manager_name") or details.get("phone"))
    print(f"  {'✅' if ok else '⚠️ '} Results for {test_id}:")
    print(f"     manager_name   : {details.get('manager_name') or '(empty)'}")
    print(f"     phone          : {details.get('phone') or '(empty)'}")
    print(f"     rating         : {details.get('rating') or '(empty)'}")
    print(f"     address        : {details.get('address') or '(empty)'}")
    print(f"     website        : {details.get('website') or '(empty)'}")
    print(f"     provider_name  : {details.get('provider_name') or '(empty)'}")
    print(f"     company_number : {details.get('company_number') or '(empty)'}")
    print("\n── End Test ─────────────────────────────────────────\n")


if __name__ == "__main__":
    test_cqc_connection()


# ─────────────────────────────────────────────────────────
# v10.3 Wrapper Object for Module & Object Compatibility
# ─────────────────────────────────────────────────────────
class _CQCClientWrapper:
    """Ensures compatibility with both  AND ."""
    def verify_and_enrich_lead(self, *args, **kwargs):
        return verify_and_enrich_lead(*args, **kwargs)
    def lookup_cqc_location(self, *args, **kwargs):
        return lookup_cqc_location(*args, **kwargs)
    def get_full_details_for_location(self, *args, **kwargs):
        return get_full_details_for_location(*args, **kwargs)
    def search_locations(self, *args, **kwargs):
        return _search_candidates(*args, **kwargs)
    def get_location_details(self, *args, **kwargs):
        return _get_location_detail(*args, **kwargs)
    def get_provider_details(self, *args, **kwargs):
        return _get_provider_detail(*args, **kwargs)

cqc_client = _CQCClientWrapper()
